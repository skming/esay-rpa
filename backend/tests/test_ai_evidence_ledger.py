from __future__ import annotations

import json

import pytest

from app.services import ai_evidence_ledger


def test_evidence_ledger_restores_current_revision_verification(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ai_evidence_ledger, "_ledger_dir", lambda: tmp_path)
    ai_evidence_ledger.record_events("flow-1", [
        {"type": "flow_written", "revision": 4},
        {"type": "run_completed", "flow_revision": 4, "status": "success", "task_id": "task-1", "definition_digest": "digest-4"},
        {"type": "audit_completed", "flow_revision": 4, "passed": True, "task_id": "task-1", "definition_digest": "digest-4"},
    ])

    restored = ai_evidence_ledger.load_verification_state("flow-1", 4, "digest-4")

    assert restored["run_verified_revision"] == 4
    assert restored["accepted_revision"] == 4
    assert ai_evidence_ledger.load_verification_state("flow-1", 5)["accepted_revision"] is None
    assert ai_evidence_ledger.load_verification_state("flow-1", 4, "different")["accepted_revision"] is None


@pytest.mark.parametrize("status", ["success", "error", "stopped"])
def test_new_run_cannot_reuse_another_tasks_audit(tmp_path, monkeypatch, status):
    monkeypatch.setattr(ai_evidence_ledger, "_ledger_dir", lambda: tmp_path)
    ai_evidence_ledger.record_events("f", [
        {"type": "run_completed", "flow_revision": 1, "status": "success", "task_id": "old", "definition_digest": "d"},
        {"type": "audit_completed", "flow_revision": 1, "passed": True, "task_id": "old", "definition_digest": "d"},
    ])
    assert ai_evidence_ledger.load_verification_state("f", 1, "d")["accepted_revision"] == 1
    ai_evidence_ledger.record_events("f", [
        {"type": "run_completed", "flow_revision": 1, "status": status, "task_id": "new", "definition_digest": "d"},
        {"type": "audit_completed", "flow_revision": 1, "passed": True, "task_id": "old", "definition_digest": "d"},
    ])
    restored = ai_evidence_ledger.load_verification_state("f", 1, "d")
    assert restored["accepted_revision"] is None
    assert restored["run_verified_revision"] == (1 if status == "success" else None)


@pytest.mark.parametrize("payload", [[], {"revisions": []}, {"revisions": {"1": []}}, {"revisions": {"1": "invalid"}}])
def test_malformed_but_valid_json_ledger_is_ignored(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(ai_evidence_ledger, "_ledger_dir", lambda: tmp_path)
    if isinstance(payload, dict):
        payload["flow_id"] = "f"
    ai_evidence_ledger._ledger_path("f").write_text(json.dumps(payload))
    restored = ai_evidence_ledger.load_verification_state("f", 1, "d")
    assert restored["accepted_revision"] is None
    ai_evidence_ledger.record_events("f", [{
        "type": "run_completed", "flow_revision": 1, "status": "success", "task_id": "new", "definition_digest": "d",
    }])
    assert ai_evidence_ledger.load_verification_state("f", 1, "d")["run_verified_revision"] == 1
