from __future__ import annotations

import json
import logging
import os
import time

import pytest

from app.core.config import load_settings
from app.core.logging import AccessLogFilter, create_log_handler, prune_log_archives, redact_log_text, setup_logging


def emit(handler, message: str, *, exc_info=None) -> None:
    handler.handle(logging.LogRecord("app.test", logging.ERROR, "", 0, message, (), exc_info))


def test_daily_rotation_bounds_archive_count(tmp_path) -> None:
    handler = create_log_handler(tmp_path / "backend.log", backup_count=2)
    try:
        for index in range(12):
            handler.rolloverAt = int(time.time()) - (12 - index) * 86400
            emit(handler, f"{index} {'采集结果' * 12}")
        files = list(tmp_path.iterdir())
        assert len(files) == 3
        assert all(file.name == "backend.log" or file.name.startswith("backend.log.") for file in files)
        assert "11 " in (tmp_path / "backend.log").read_text()
    finally:
        handler.close()


def test_current_day_file_has_no_size_limit(tmp_path) -> None:
    handler = create_log_handler(tmp_path / "backend.log")
    try:
        for _ in range(600):
            emit(handler, "采集结果" * 1000)
        assert (tmp_path / "backend.log").stat().st_size > 5 * 1024 * 1024
        assert len(list(tmp_path.iterdir())) == 1
    finally:
        handler.close()


def test_logs_keep_error_stack_and_redact_credentials(tmp_path) -> None:
    handler = create_log_handler(tmp_path / "backend.log")
    try:
        try:
            raise ValueError("Authorization: Bearer sample-credential")
        except ValueError:
            import sys
            emit(handler, "request failed", exc_info=sys.exc_info())
        text = (tmp_path / "backend.log").read_text()
        assert "Traceback" in text and "ValueError" in text and "request failed" in text
        assert "sample-credential" not in text
        assert "[REDACTED]" in text
    finally:
        handler.close()


@pytest.mark.parametrize("text", [
    'api_key="sample-credential"',
    'https://provider.test/api?token=sample-credential',
    'postgresql://user:sample-credential@db/rpa',
    'sk-00000000000000000000000000000000',
    'password="sample-credential with spaces"',
])
def test_redaction_handles_known_credential_formats(text) -> None:
    redacted = redact_log_text(text)
    assert "sample-credential" not in redacted
    assert "sk-00000000000000000000000000000000" not in redacted
    assert "with spaces" not in redacted


@pytest.mark.parametrize(("text", "expected"), [
    ('token=123 secret=公开标签 prompt_tokens=200', 'token=123 secret=公开标签 prompt_tokens=200'),
    ('token: count secret: label password_strength=strong', 'token: count secret: label password_strength=strong'),
    ('{"token": "sample-credential", "secret": "sample-credential"}', '{"token": [REDACTED], "secret": [REDACTED]}'),
    ("{'token': 'sample-credential'}", "{'token': [REDACTED]}"),
    ('clientSecret=sample-credential api_secret=sample-credential', 'clientSecret=[REDACTED] api_secret=[REDACTED]'),
    ('request Bearer sample-credential failed', 'request Bearer [REDACTED] failed'),
    ('Authorization: Basic sample-credential', 'Authorization: [REDACTED]'),
    ('GET /api?token=sample-credential&secret=sample-credential&page=2#section', 'GET /api?token=[REDACTED]&secret=[REDACTED]&page=2#section'),
    ('https://provider.test/?token=sample-credential#section', 'https://provider.test/?token=[REDACTED]#section'),
])
def test_redaction_distinguishes_credentials_from_business_text(text, expected) -> None:
    assert redact_log_text(text) == expected


def test_archive_cleanup_only_touches_owned_files(tmp_path) -> None:
    stale = tmp_path / "backend-2026-01-01.log"
    stale.write_text("old")
    os.utime(stale, (time.time() - 31 * 86400,) * 2)
    for day in ("2026-01-02", "2026-01-03", "2026-01-04"):
        (tmp_path / f"backend.log.{day}").write_text("recent")
    (tmp_path / "backend.log").write_text("active")
    (tmp_path / "unrelated.log").write_text("keep")
    (tmp_path / "backend.log.custom").write_text("keep")
    prune_log_archives(tmp_path / "backend.log", backup_count=2, retention_days=30)
    remaining = {file.name for file in tmp_path.iterdir()}
    assert {"backend.log", "unrelated.log", "backend.log.custom"}.issubset(remaining)
    assert len(remaining) == 5
    assert stale.name not in remaining


def test_archive_cleanup_failure_does_not_block_logging(tmp_path, monkeypatch) -> None:
    from pathlib import Path
    stale = tmp_path / "backend-2026-01-01.log"
    stale.write_text("old")
    os.utime(stale, (time.time() - 31 * 86400,) * 2)
    original_unlink = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == stale:
            raise PermissionError("archive locked")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    handler = create_log_handler(tmp_path / "backend.log")
    try:
        emit(handler, "still writable")
        assert "still writable" in (tmp_path / "backend.log").read_text()
    finally:
        handler.close()


def test_setup_is_idempotent_and_captures_uvicorn_errors(tmp_path) -> None:
    root = logging.getLogger()
    uvicorn = logging.getLogger("uvicorn")
    access = logging.getLogger("uvicorn.access")
    root_handlers, uvicorn_handlers = root.handlers[:], uvicorn.handlers[:]
    root_level, uvicorn_level = root.level, uvicorn.level
    root.handlers = [handler for handler in root_handlers if not getattr(handler, "_easy_rpa", False)]
    uvicorn.handlers = []
    old_propagate, old_access_propagate = uvicorn.propagate, access.propagate
    old_filters = access.filters[:]
    try:
        uvicorn.propagate = False
        uvicorn.setLevel(logging.INFO)
        setup_logging(str(tmp_path))
        setup_logging(str(tmp_path))
        logging.getLogger("uvicorn.error").error("bind failed")
        root.error("single message")
        text = (tmp_path / "backend.log").read_text()
        assert text.count("single message") == 1
        assert text.count("bind failed") == 1
        assert len([handler for handler in root.handlers if getattr(handler, "_easy_rpa", False)]) == 1
    finally:
        for handler in root.handlers:
            if getattr(handler, "_easy_rpa", False):
                handler.close()
        root.handlers, uvicorn.handlers = root_handlers, uvicorn_handlers
        root.setLevel(root_level)
        uvicorn.setLevel(uvicorn_level)
        uvicorn.propagate, access.propagate = old_propagate, old_access_propagate
        access.filters = old_filters


def test_successful_reads_are_quiet_but_failures_and_mutations_remain() -> None:
    filter_ = AccessLogFilter()
    def record(method, status):
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %s", ("client", method, "/api/health", "1.1", status), None)
    assert not filter_.filter(record("GET", 200))
    assert filter_.filter(record("GET", 500))
    assert filter_.filter(record("POST", 200))


def test_log_settings_have_separate_count_and_age(monkeypatch) -> None:
    monkeypatch.setenv("RPA_LOG_BACKUP_COUNT", "2")
    monkeypatch.setenv("RPA_LOG_RETENTION_DAYS", "7")
    settings = load_settings()
    assert (settings.log_backup_count, settings.log_retention_days) == (2, 7)


@pytest.mark.parametrize("name", ["RPA_LOG_BACKUP_COUNT", "RPA_LOG_RETENTION_DAYS"])
def test_log_settings_reject_nonpositive_limits(monkeypatch, name) -> None:
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError, match=name):
        load_settings()


def test_module_log_level_rejects_empty_logger_name(monkeypatch) -> None:
    monkeypatch.setenv("RPA_LOG_LEVELS", ":DEBUG")
    with pytest.raises(ValueError, match="模块名不能为空"):
        load_settings()


def test_audit_rotation_preserves_json_lines(tmp_path, monkeypatch) -> None:
    import app.core.logging as log_module
    from app.services.extension_bridge_service import _write_audit_record
    monkeypatch.setenv("RPA_LOG_DIR", str(tmp_path))
    for index in range(15):
        if log_module._audit_handler is not None and log_module._audit_handler.baseFilename == str(tmp_path / "extension_bridge_audit.jsonl"):
            log_module._audit_handler.rolloverAt = int(time.time()) - (15 - index) * 86400
        _write_audit_record({"requestId": str(index), "error": "api_key=sample-credential", "ok": False})
    assert len(list(tmp_path.glob("extension_bridge_audit.jsonl.*"))) > 0
    for file in tmp_path.iterdir():
        for line in file.read_text().splitlines():
            assert json.loads(line)["ok"] is False
            assert "sample-credential" not in line
