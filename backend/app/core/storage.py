"""Centralized filesystem layout & cache management for the RPA app data dir.

Single source of truth for where runtime files live under ~/.easy-rpa and
how they are named / pruned, so output naming and cache retention stay
consistent across every runner.

Layout
------
  <app_data>/
    workspace/                 user-facing working dir (script cwd)
      runs/<flow_key>/<task_id>/<run_timestamp>.<ext>
    runtime/                   mutable service state
      browser/profile/
      browser/cookies.json
      scrapling/
    ai/
      config.json
      chats/
    cache/
      scripts/inline_<hash>.py ephemeral inline-script files (pruned by age/count)
    logs/
"""
from __future__ import annotations

import os
import re
import time
import shutil
import unicodedata
from pathlib import Path

_DEFAULT_APP_DATA_DIR = Path.home() / ".easy-rpa"

# Run-output retention: keep this many most-recent runs per flow.
DEFAULT_OUTPUT_RETENTION = 10
RUN_OUTPUT_ROOT = "runs"

# Inline-script cache retention.
TEMP_SCRIPT_MAX_AGE_DAYS = 7
TEMP_SCRIPT_MAX_FILES = 300

_SLUG_STRIP = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_SLUG_COLLAPSE = re.compile(r"[\s_]+")


def resolve_workspace_root() -> Path:
    raw = os.getenv("RPA_WORKSPACE_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser()
    return _app_data_dir() / "workspace"


def resolve_run_root() -> Path:
    return resolve_workspace_root() / RUN_OUTPUT_ROOT


def resolve_cache_dir() -> Path:
    raw = os.getenv("RPA_CACHE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return _app_data_dir() / "cache"


def resolve_logs_dir() -> Path:
    raw = os.getenv("RPA_LOG_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return _app_data_dir() / "logs"


def resolve_runtime_root() -> Path:
    return _app_data_dir() / "runtime"


def resolve_browser_profile_dir() -> Path:
    return resolve_runtime_root() / "browser" / "profile"


def resolve_browser_cookies_path() -> Path:
    return resolve_runtime_root() / "browser" / "cookies.json"


def resolve_scrapling_storage_dir() -> Path:
    return resolve_runtime_root() / "scrapling"


def resolve_ai_dir() -> Path:
    return _app_data_dir() / "ai"


def resolve_ai_config_path() -> Path:
    raw = os.getenv("RPA_AI_CONFIG_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    return resolve_ai_dir() / "config.json"


def resolve_ai_chats_dir() -> Path:
    return resolve_ai_dir() / "chats"


def resolve_database_path() -> Path:
    raw = os.getenv("RPA_DATABASE_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    return _app_data_dir() / "db" / "rpa.sqlite3"


def _app_data_dir() -> Path:
    raw = os.getenv("RPA_APP_DATA_DIR", "").strip()
    return Path(raw).expanduser() if raw else _DEFAULT_APP_DATA_DIR


def resolve_app_data_dir() -> Path:
    return _app_data_dir()


# ── Naming ──────────────────────────────────────────────────────────────────

def slugify(name: str | None, *, fallback: str = "flow") -> str:
    """Filesystem-safe, human-readable slug. Keeps CJK and word chars, strips
    path separators and control chars, collapses whitespace/underscores."""
    text = unicodedata.normalize("NFKC", (name or "").strip())
    text = _SLUG_STRIP.sub("", text)
    text = _SLUG_COLLAPSE.sub("_", text).strip("_.")
    return text[:80] if text else fallback


# ── Run outputs (workspace/runs/<flow_key>/) ────────────────────────────────

def run_flow_dir(flow_key: str, *, workspace_root: Path | None = None) -> Path:
    root = (workspace_root or resolve_workspace_root()) / RUN_OUTPUT_ROOT / flow_key
    return root


def run_output_dir(flow_key: str, run_id: str | None = None, *, workspace_root: Path | None = None) -> Path:
    root = run_flow_dir(flow_key, workspace_root=workspace_root)
    return root / run_id if run_id else root


def prune_run_outputs(
    flow_key: str,
    *,
    keep: int = DEFAULT_OUTPUT_RETENTION,
    workspace_root: Path | None = None,
) -> list[Path]:
    """Keep the `keep` most-recent run directories in workspace/runs/<flow_key>/."""
    directory = run_flow_dir(flow_key, workspace_root=workspace_root)
    if keep < 0 or not directory.is_dir():
        return []
    runs = [p for p in directory.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if not runs:
        return []
    ranked = sorted(runs, key=lambda p: _dir_mtime(p), reverse=True)
    removed: list[Path] = []
    for run_dir in ranked[keep:]:
        try:
            shutil.rmtree(run_dir)
            removed.append(run_dir)
        except OSError:
            pass
    return removed


# ── Inline-script cache (cache/scripts/) ────────────────────────────────────

def temp_scripts_dir(*, cache_dir: Path | None = None) -> Path:
    directory = (cache_dir or resolve_cache_dir()) / "scripts"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def prune_temp_scripts(
    *,
    cache_dir: Path | None = None,
    max_age_days: int = TEMP_SCRIPT_MAX_AGE_DAYS,
    max_files: int = TEMP_SCRIPT_MAX_FILES,
) -> int:
    """Drop inline-script files older than max_age_days, then cap the directory
    at max_files newest entries. These are regenerated on demand, so removal is
    always safe. Returns the count removed."""
    directory = (cache_dir or resolve_cache_dir()) / "scripts"
    if not directory.is_dir():
        return 0
    files = [p for p in directory.iterdir() if p.is_file()]
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    survivors: list[Path] = []
    for p in files:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            if _safe_unlink(p):
                removed += 1
        else:
            survivors.append(p)
    if len(survivors) > max_files:
        survivors.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in survivors[max_files:]:
            if _safe_unlink(p):
                removed += 1
    return removed


def _safe_unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError:
        return False


def _dir_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
