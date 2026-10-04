from __future__ import annotations

import logging
import os
import re
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


LOG_BACKUP_COUNT = 30
LOG_RETENTION_DAYS = 30
_NOISY_LOGGERS = ("httpx", "httpcore", "litellm", "LiteLLM", "openai", "urllib3", "asyncio", "playwright", "watchfiles")
_SECRET = re.compile(
    r"(?i)((?:\b(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"(?:client|api)[_-]?secret|password)\b[\"']?|\"(?:token|secret)\"|'(?:token|secret)')\s*[:=]\s*)"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|(?:(?:Bearer|Basic)\s+)?[^\s\"',;}&#]+)"
    r"|\b(?:sk-|rc-)[A-Za-z0-9_-]{16,}"
    r"|(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"
)
_QUERY_SECRET = re.compile(r"(?i)([?&](?:token|secret)=)[^\s\"',;}&#]+")
_BEARER = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]+=*")


def redact_log_text(text: str) -> str:
    # Bare token/secret labels can be business text; quoted fields and query parameters identify credential contexts.
    text = _SECRET.sub(lambda match: (match.group(1) or "") + "[REDACTED]", text)
    text = _QUERY_SECRET.sub(r"\1[REDACTED]", text)
    return _BEARER.sub(r"\1[REDACTED]", text)


class SafeLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        text = redact_log_text(super().format(record))
        if len(text) > 16000:
            return f"{text[:8000]}\n[truncated]\n{text[-8000:]}"
        return text


class DailyLogFileHandler(TimedRotatingFileHandler):
    retention_days: int = LOG_RETENTION_DAYS

    def _open(self):
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, self.mode, encoding=self.encoding, errors=self.errors)

    def doRollover(self) -> None:
        super().doRollover()
        prune_log_archives(Path(self.baseFilename), backup_count=self.backupCount, retention_days=self.retention_days)


class AccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.args, tuple) or len(record.args) != 5:
            return True
        _, method, _, _, status = record.args
        return method not in {"GET", "HEAD", "OPTIONS"} or int(status) >= 400


def prune_log_archives(path: Path, *, backup_count: int, retention_days: int) -> None:
    # Match only this application's archives; never sweep arbitrary files in RPA_LOG_DIR.
    dated = re.compile(re.escape(path.name) + r"\.\d{4}-\d{2}-\d{2}$")
    legacy = re.compile(r"backend-\d{4}-\d{2}-\d{2}\.log$") if path.name == "backend.log" else None
    cutoff = time.time() - retention_days * 86400
    survivors: list[Path] = []
    for candidate in path.parent.iterdir():
        match = dated.fullmatch(candidate.name)
        is_legacy = legacy is not None and legacy.fullmatch(candidate.name) is not None
        if not match and not is_legacy:
            continue
        try:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink()
            else:
                survivors.append(candidate)
        except OSError:
            continue
    survivors.sort(key=lambda candidate: candidate.stat().st_mtime, reverse=True)
    for candidate in survivors[backup_count:]:
        try:
            candidate.unlink()
        except OSError:
            pass


def create_log_handler(
    path: Path,
    *,
    backup_count: int = LOG_BACKUP_COUNT,
    retention_days: int = LOG_RETENTION_DAYS,
) -> DailyLogFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    prune_log_archives(path, backup_count=backup_count, retention_days=retention_days)
    handler = DailyLogFileHandler(path, when="midnight", backupCount=backup_count, encoding="utf-8", delay=True, utc=True)
    handler.retention_days = retention_days
    formatter = SafeLogFormatter("%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    return handler


def setup_logging(
    log_dir: str,
    *,
    level: str = "INFO",
    backup_count: int = LOG_BACKUP_COUNT,
    retention_days: int = LOG_RETENTION_DAYS,
    module_levels: dict[str, str] | None = None,
) -> None:
    if not log_dir:
        return
    root = logging.getLogger()
    uvicorn = logging.getLogger("uvicorn")
    # Reloads/tests may initialize again; remove only handlers owned by this setup.
    for owner in (root, uvicorn):
        for handler in list(owner.handlers):
            if getattr(handler, "_easy_rpa", False):
                owner.removeHandler(handler)
                handler.close()
    try:
        handler = create_log_handler(Path(log_dir) / "backend.log", backup_count=backup_count, retention_days=retention_days)
    except OSError as exc:
        logging.getLogger(__name__).warning("文件日志不可用：%s", redact_log_text(str(exc)))
        return
    handler._easy_rpa = True
    levels = [logging.getLevelName(level), *[logging.getLevelName(item) for item in (module_levels or {}).values()]]
    handler.setLevel(min(levels))
    root.setLevel(level)
    root.addHandler(handler)
    # Uvicorn owns console handlers and does not propagate to root by default.
    if not uvicorn.propagate:
        uvicorn.addHandler(handler)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, AccessLogFilter) for item in access.filters):
        access.addFilter(AccessLogFilter())
    if not access.propagate:
        access.propagate = True
    for owner in (uvicorn, access):
        for console in owner.handlers:
            if isinstance(console, logging.StreamHandler) and not isinstance(console, logging.FileHandler):
                console.setFormatter(handler.formatter)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    for module, module_level in (module_levels or {}).items():
        logging.getLogger(module).setLevel(module_level)


_audit_handler: DailyLogFileHandler | None = None


def write_audit_line(path: Path, line: str) -> None:
    global _audit_handler
    if _audit_handler is None or _audit_handler.baseFilename != str(path.resolve()):
        if _audit_handler is not None:
            _audit_handler.close()
        _audit_handler = create_log_handler(path)
        _audit_handler.setFormatter(logging.Formatter("%(message)s"))
    # Logging's handler lock serializes concurrent action receipts and rollover.
    _audit_handler.handle(logging.LogRecord("extension.audit", logging.INFO, "", 0, line, (), None))
