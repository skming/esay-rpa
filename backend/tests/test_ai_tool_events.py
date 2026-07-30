from __future__ import annotations

from app.services.ai_tool_events import attach_tool_events, reduce_evidence_state


def test_blocked_or_failed_write_does_not_invalidate_evidence() -> None:
    state = {
        "current_flow_revision": 3,
        "run_verified_revision": 3,
        "accepted_revision": 3,
    }

    blocked = attach_tool_events(
        "update_flow",
        {"status": "blocked_by_orchestrator_guard", "blocked_tool": "update_flow"},
    )
    failed = attach_tool_events("apply_node_fix", {"status": "error"})
    reduce_evidence_state(state, blocked)
    reduce_evidence_state(state, failed)

    assert state["current_flow_revision"] == 3
    assert state["run_verified_revision"] == 3
    assert state["accepted_revision"] == 3
    assert "verification_status" not in blocked
    assert "verification_status" not in failed


def test_new_revision_invalidates_old_run_and_audit_evidence() -> None:
    state = {
        "current_flow_revision": 3,
        "run_verified_revision": 3,
        "accepted_revision": 3,
    }
    result = attach_tool_events(
        "update_flow",
        {"status": "applied", "flow_id": "flow", "revision": 4, "changed_nodes": []},
    )

    reduce_evidence_state(state, result)

    assert state["current_flow_revision"] == 4
    assert state["run_verified_revision"] is None
    assert state["accepted_revision"] is None
    assert result["verification_status"] == "modified_unverified"
