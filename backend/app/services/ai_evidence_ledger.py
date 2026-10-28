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


def _read_revisions(flow_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(_ledger_path(flow_id).read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict) or payload.get("flow_id") != flow_id:
        return {}
    revisions = payload.get("revisions")
    return revisions if isinstance(revisions, dict) else {}


def load_verification_state(
    flow_id: str | None,
    current_revision: int | None,
    current_definition_digest: str | None = None,
) -> dict[str, Any]:
    state = {
        "current_flow_revision": current_revision,
        "run_verified_revision": None,
        "accepted_revision": None,
        "verification_task_id": None,
    }
    if not flow_id or current_revision is None:
        return state
    revision = _read_revisions(flow_id).get(str(current_revision))
    if not isinstance(revision, dict):
        return state
    if current_definition_digest is not None and revision.get("definition_digest") != current_definition_digest:
        return state
    task_id = revision.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return state
    state["verification_task_id"] = task_id
    if revision.get("run_status") == "success":
        state["run_verified_revision"] = current_revision
        if revision.get("accepted") is True and revision.get("audit_task_id") == task_id:
            state["accepted_revision"] = current_revision
    return state


def record_events(flow_id: str | None, events: list[dict[str, Any]]) -> None:
    if not flow_id or not events:
        return
    path = _ledger_path(flow_id)
    revisions = _read_revisions(flow_id)
    for event in events:
        if not isinstance(event, dict):
            continue
        revision = event.get("revision", event.get("flow_revision"))
        if type(revision) is not int or revision < 1:
            continue
        previous = revisions.get(str(revision))
        entry = dict(previous) if isinstance(previous, dict) else {}
        event_type = event.get("type")
        if event_type == "flow_written":
            entry = {"written": True}
        elif event_type == "run_completed":
            task_id = event.get("task_id")
            status = event.get("status")
            if not isinstance(task_id, str) or not task_id or status not in {"success", "error", "stopped"}:
                continue
            identity = (task_id, event.get("definition_digest"), status)
            if identity != (entry.get("task_id"), entry.get("definition_digest"), entry.get("run_status")):
                entry.pop("accepted", None)
                entry.pop("audit_task_id", None)
            entry.update({"run_status": status, "task_id": task_id, "definition_digest": event.get("definition_digest")})
        elif event_type == "audit_completed":
            if (entry.get("run_status") != "success" or not entry.get("task_id")
                    or event.get("task_id") != entry["task_id"]
                    or event.get("definition_digest") != entry.get("definition_digest")):
                continue
            entry.update({"accepted": event.get("passed") is True, "audit_task_id": event["task_id"]})
        else:
            continue
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
