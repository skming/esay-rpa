from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from app.core.storage import resolve_ai_dir

_MAX_REVISIONS = 50


def _ledger_dir() -> Path:
    return resolve_ai_dir() / "evidence"


def _ledger_path(flow_id: str) -> Path:
    safe_id = hashlib.sha256(flow_id.encode("utf-8")).hexdigest()
    return _ledger_dir() / f"flow_{safe_id}.json"


def load_verification_state(
    flow_id: str | None,
    current_revision: int | None,
    current_definition_digest: str | None = None,
) -> dict[str, Any]:
    state = {
        "current_flow_revision": current_revision,
        "run_verified_revision": None,
        "accepted_revision": None,
    }
    if not flow_id or current_revision is None:
        return state
    try:
        payload = json.loads(_ledger_path(flow_id).read_text(encoding="utf-8"))
    except Exception:
        return state
    revision = (payload.get("revisions") or {}).get(str(current_revision), {})
    if current_definition_digest is not None and revision.get("definition_digest") != current_definition_digest:
        return state
    if revision.get("run_status") == "success":
        state["run_verified_revision"] = current_revision
    if revision.get("accepted") is True:
        state["accepted_revision"] = current_revision
    return state


def record_events(flow_id: str | None, events: list[dict[str, Any]]) -> None:
    if not flow_id or not events:
        return
    path = _ledger_path(flow_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        payload = {}
    revisions = dict(payload.get("revisions") or {})
    for event in events:
        revision = event.get("revision", event.get("flow_revision"))
        if not isinstance(revision, int):
            continue
        entry = dict(revisions.get(str(revision)) or {})
        event_type = event.get("type")
        if event_type == "flow_written":
            entry = {"written": True}
        elif event_type == "run_completed":
            entry.update({
                "run_status": event.get("status"),
                "task_id": event.get("task_id"),
                "definition_digest": event.get("definition_digest"),
            })
        elif event_type == "audit_completed":
            entry.update({
                "accepted": event.get("passed") is True,
                "audit_task_id": event.get("task_id"),
                "definition_digest": event.get("definition_digest") or entry.get("definition_digest"),
            })
        entry["updated_at"] = time.time()
        revisions[str(revision)] = entry
    revisions = dict(sorted(
        revisions.items(),
        key=lambda item: int(item[0]) if item[0].isdigit() else -1,
        reverse=True,
    )[:_MAX_REVISIONS])
    try:
        _ledger_dir().mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps({"flow_id": flow_id, "revisions": revisions}, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_path.replace(path)
    except Exception:
        pass
