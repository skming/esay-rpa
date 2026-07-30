"""Persist AI chat sessions as JSONL files under the unified app data tree.

File layout:
  ai/chats/flow_{flowId}.jsonl   — per-flow sessions
  ai/chats/{session_key}.jsonl   — non-flow sessions
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from app.core import storage

# 匹配真实已保存流程的 "flow_{UUID}"，故意排除未保存草稿的 "flow_local-*"。
_REAL_FLOW_RE = re.compile(r"^flow_(?!local-)([A-Za-z0-9_\-]+)$")

# 仅对非流程草稿会话做保留期清理，流程关联的对话不自动清理。
DRAFT_SESSION_KEEP = 30
DRAFT_SESSION_MAX_AGE_DAYS = 30


def _safe_key(value: str) -> str:
    """Return a filesystem-safe, path-traversal-free version of *value*."""
    return (re.sub(r"[^\w\-]", "_", value) or "default")[:80]


def _message_sort_key(message: dict[str, Any]) -> tuple[int, str]:
    try:
        created_at = int(message.get("createdAt") or 0)
    except (TypeError, ValueError):
        created_at = 0
    return created_at, str(message.get("id") or "")


class AiChatStore:
    def __init__(self, app_data_dir: str | None = None) -> None:
        self._base = storage.resolve_ai_chats_dir() if app_data_dir is None else Path(app_data_dir) / "ai" / "chats"


    def _path(self, session_key: str) -> Path:
        m = _REAL_FLOW_RE.match(session_key)
        if m:
            return self._base / f"flow_{_safe_key(m.group(1))}.jsonl"
        return self._base / f"{_safe_key(session_key)}.jsonl"


    def load(self, session_key: str) -> list[dict[str, Any]]:
        """Return all messages for the session; empty list if no file exists."""
        path = self._path(session_key)
        if not path.exists():
            return []
        messages: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if isinstance(msg, dict) and msg.get("id") and msg.get("role"):
                        msg.pop("diffPreview", None)
                        messages.append(msg)
                except json.JSONDecodeError:
                    continue
        except OSError:
            return []
        return messages


    def save(self, session_key: str, messages: list[dict[str, Any]]) -> None:
        path = self._path(session_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # diffPreview 是前端临时展示字段，体积大且可重新生成，故意不落盘。
        lines = [
            json.dumps({k: v for k, v in msg.items() if k != "diffPreview"}, ensure_ascii=False)
            for msg in messages
            if isinstance(msg, dict) and msg.get("id") and msg.get("role")
        ]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        if not _REAL_FLOW_RE.match(session_key):
            self._prune_draft_sessions(exclude=path)


    def _prune_draft_sessions(
        self,
        *,
        exclude: Path | None = None,
        keep: int = DRAFT_SESSION_KEEP,
        max_age_days: int = DRAFT_SESSION_MAX_AGE_DAYS,
    ) -> int:
        """先删超过 max_age_days 的草稿会话，再把目录裁到最新 `keep` 个；刚保存的文件永不删除，出错不抛异常。"""
        directory = self._base
        if not directory.is_dir():
            return 0
        try:
            files = [p for p in directory.glob("*.jsonl") if p.is_file()]
        except OSError:
            return 0
        keep_path = exclude.resolve() if exclude is not None else None
        cutoff = time.time() - max_age_days * 86400
        removed = 0
        survivors: list[Path] = []
        for p in files:
            if keep_path is not None and p.resolve() == keep_path:
                survivors.append(p)
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff and _unlink(p):
                removed += 1
            else:
                survivors.append(p)
        if len(survivors) > keep:
            survivors.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for p in survivors[keep:]:
                if keep_path is not None and p.resolve() == keep_path:
                    continue
                if _unlink(p):
                    removed += 1
        return removed


    def rename(self, from_key: str, to_key: str) -> bool:
        """把草稿会话改挂到真实流程；目标已落盘时按消息 id 合并，避免保存竞态拆散上下文。"""
        source = self._path(from_key)
        target = self._path(to_key)
        if source == target or not source.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            merged: dict[str, dict[str, Any]] = {}
            for message in [*self.load(from_key), *self.load(to_key)]:
                message_id = str(message.get("id") or "")
                if message_id:
                    merged[message_id] = message
            messages = list(merged.values())
            messages.sort(key=_message_sort_key)
            self.save(to_key, messages)
            return _unlink(source)
        try:
            source.replace(target)
        except OSError:
            return False
        return True


    def delete(self, session_key: str) -> bool:
        path = self._path(session_key)
        if path.exists():
            path.unlink()
            return True
        return False


    def list_sessions(self) -> list[dict[str, Any]]:
        """Return metadata for all sessions (flows + non-flow), newest first."""
        sessions: list[dict[str, Any]] = []

        if self._base.exists():
            for path in self._base.glob("*.jsonl"):
                if path.name.startswith("flow_"):
                    continue
                _collect_session_meta(path, key_override=path.stem, out=sessions)

            for path in self._base.glob("flow_*.jsonl"):
                flow_id = path.stem.removeprefix("flow_")
                _collect_session_meta(path, key_override=f"flow_{flow_id}", out=sessions)

        sessions.sort(key=lambda s: s["updated_at"], reverse=True)
        return sessions


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError:
        return False


def _collect_session_meta(path: Path, *, key_override: str, out: list[dict[str, Any]]) -> None:
    try:
        stat = path.stat()
        count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        out.append({
            "session_key": key_override,
            "message_count": count,
            "file_size": stat.st_size,
            "updated_at": stat.st_mtime,
        })
    except OSError:
        pass
