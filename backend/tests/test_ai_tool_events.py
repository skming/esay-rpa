from __future__ import annotations

from app.services.ai_guard_state import GuardState
from app.services.ai_tool_events import attach_tool_events, reduce_evidence_state


def test_blocked_or_failed_write_does_not_invalidate_evidence() -> None:
    state = GuardState(
        current_flow_revision=3,
        run_verified_revision=3,
        accepted_revision=3,
    )

    blocked = attach_tool_events(
        "update_flow",
        {"status": "blocked_by_orchestrator_guard", "blocked_tool": "update_flow"},
    )
    failed = attach_tool_events("apply_node_fix", {"status": "error"})
    reduce_evidence_state(state, blocked)
    reduce_evidence_state(state, failed)

    assert state.current_flow_revision == 3
    assert state.run_verified_revision == 3
    assert state.accepted_revision == 3
    assert "verification_status" not in blocked
    assert "verification_status" not in failed


def test_new_revision_invalidates_old_run_and_audit_evidence() -> None:
    state = GuardState(
        current_flow_revision=3,
        run_verified_revision=3,
        accepted_revision=3,
    )
    result = attach_tool_events(
        "update_flow",
        {"status": "applied", "flow_id": "flow", "revision": 4, "changed_nodes": []},
    )

    reduce_evidence_state(state, result)

    assert state.current_flow_revision == 4
    assert state.run_verified_revision is None
    assert state.accepted_revision is None
    assert result["verification_status"] == "modified_unverified"


def test_metadata_only_write_keeps_current_evidence():
    state = GuardState(
        current_flow_revision=3, current_definition_digest="d3", run_verified_revision=3, accepted_revision=3
    )
    result = attach_tool_events("update_flow", {"status": "applied", "revision": 3, "execution_changed": False})
    reduce_evidence_state(state, result)
    assert state.accepted_revision == 3 and state.run_verified_revision == 3
    assert "events" not in result


def test_same_revision_with_different_digest_cannot_be_verified():
    state = GuardState(current_flow_revision=3, current_definition_digest="current")
    result = attach_tool_events(
        "run_flow",
        {
            "task_id": "t",
            "status": "success",
            "flow_revision": 3,
            "definition_digest": "old",
            "acceptance_audit": {"passed": True, "flow_revision": 3, "definition_digest": "old"},
        },
    )
    reduce_evidence_state(state, result)
    assert state.accepted_revision is None and state.run_verified_revision is None
