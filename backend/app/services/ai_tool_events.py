from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # 只在类型检查期引用：本模块运行期保持零 ai_* 依赖，属性读写不需要这个类。
    from app.services.ai_guard_state import GuardState


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
        # 审计随 run_flow 一起回来，所以两个事件同一轮产出：run_completed 说明「跑到底了」，
        # audit_completed 说明「产物合格」，证据等级是两级，不能合成一个。
        audit = result.get("acceptance_audit")
        if isinstance(audit, dict) and "passed" in audit:
            events.append({
                "type": "audit_completed",
                "task_id": audit.get("task_id") or result.get("task_id"),
                "flow_revision": audit.get("flow_revision"),
                "passed": audit.get("passed"),
                "definition_digest": audit.get("definition_digest"),
            })
    if events:
        result["events"] = events
    return result


def reduce_evidence_state(state: GuardState, result: Any) -> None:
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
                state.current_flow_revision = revision
            state.run_verified_revision = None
            state.accepted_revision = None
        elif event_type == "run_completed" and event.get("status") == "success":
            revision = event.get("flow_revision")
            state.run_verified_revision = revision
        elif event_type == "audit_completed" and event.get("passed") is True:
            revision = event.get("flow_revision")
            state.accepted_revision = revision
    result["verification_status"] = current_verification_status(state)


def current_verification_status(state: GuardState) -> str:
    current = state.current_flow_revision
    if current is not None and state.accepted_revision == current:
        return "accepted"
    if current is not None and state.run_verified_revision == current:
        return "run_verified"
    return "modified_unverified"
