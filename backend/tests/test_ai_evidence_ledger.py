from __future__ import annotations

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
