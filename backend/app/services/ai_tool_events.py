from __future__ import annotations

from typing import Any


def attach_tool_events(tool_name: str, result: Any) -> Any:
    if (
        not isinstance(result, dict)
        or result.get("error")
        or str(result.get("status") or "").startswith("blocked_")
    ):
        return result
    events: list[dict[str, Any]] = []
    if tool_name in {"create_flow", "update_flow", "apply_node_fix", "set_acceptance_contract"}:
        # 写入成功一定产生新 revision；缺少 revision 表示工具没有真正修改流程，
        # 不能让一次失败调用把已经获得的运行/验收证据清空。
        if not isinstance(result.get("revision"), int):
            return result
        affected = result.get("changed_nodes") or []
        if tool_name == "apply_node_fix" and isinstance(result.get("node_ref"), dict):
            affected = [{**result["node_ref"], "change": "updated"}]
        events.append({
            "type": "flow_written",
            "flow_id": result.get("flow_id"),
            "revision": result.get("revision"),
            "affected_nodes": affected,
        })
    elif tool_name == "run_flow" and result.get("task_id"):
        events.append({
            "type": "run_completed",
            "task_id": result.get("task_id"),
            "flow_revision": result.get("flow_revision"),
            "status": result.get("status"),
            "definition_digest": result.get("definition_digest"),
        })
    elif tool_name == "assert_run_output" and "passed" in result:
        events.append({
            "type": "audit_completed",
            "task_id": result.get("task_id"),
            "flow_revision": result.get("flow_revision"),
            "passed": result.get("passed"),
            "definition_digest": result.get("definition_digest"),
        })
    if events:
        result["events"] = events
    return result


def reduce_evidence_state(state: dict[str, Any], result: Any) -> None:
    if not isinstance(result, dict):
        return
    events = [event for event in (result.get("events") or []) if isinstance(event, dict)]
    if not events:
        return
    for event in events:
        event_type = event.get("type")
        if event_type == "flow_written":
            revision = event.get("revision")
            if isinstance(revision, int):
                state["current_flow_revision"] = revision
            state["run_verified_revision"] = None
            state["accepted_revision"] = None
        elif event_type == "run_completed" and event.get("status") == "success":
            revision = event.get("flow_revision")
            state["run_verified_revision"] = revision
        elif event_type == "audit_completed" and event.get("passed") is True:
            revision = event.get("flow_revision")
            state["accepted_revision"] = revision
    result["verification_status"] = current_verification_status(state)


def current_verification_status(state: dict[str, Any]) -> str:
    current = state.get("current_flow_revision")
    if current is not None and state.get("accepted_revision") == current:
        return "accepted"
    if current is not None and state.get("run_verified_revision") == current:
        return "run_verified"
    return "modified_unverified"
