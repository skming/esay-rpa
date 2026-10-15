"""Prompt/tool 审查问题的行为回归；使用临时存储和合成凭据。"""
import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock

import pytest

from app.core.logging import redact_log_text
from app.models.schemas import (
    FlowAcceptanceContract, FlowCreateRequest, RunTaskRequest, RuntimeProgress,
    RuntimeVariableSnapshot, TaskLogEntry, TaskLogLevel, TaskSnapshot,
    ScheduleCreateRequest, ScheduleTaskRequest, ScheduleUpdateRequest,
)
from app.services.ai_flow_state import FlowState
from app.services.ai_guard_state import GuardState
from app.services.ai_orchestrator import (
    AiOrchestrator, _TurnIntents, _orchestrator_guard_before_tool, _orchestrator_guard_after_tool,
    _system_prompt_for_round, _tool_schemas_for_round,
)
from app.services.ai_phases import evidence_already_collected, note_evidence, note_progress
from app.services.ai_prompts import SYSTEM_PROMPT, render_guard_contract
from app.services.ai_tool_events import attach_tool_events, reduce_evidence_state
from app.services.ai_tools.executor import RpaToolExecutor
from app.services.ai_tools.lint_diff import ChangeContext
from app.services.ai_tools.schemas import validate_tool_arguments
from app.services.flow_service import FlowService
from app.services.runtime_variables import RuntimeVariableStore
from app.services.scheduler_service import ScheduleService
from app.services.task_manager import TaskManager, TaskRecord
from app.services.variable_action_runner import VariableActionRunner


def task_snapshot(**updates):
    now = datetime.now(UTC)
    return TaskSnapshot(
        task_id="t", flow_id="f", flow_name="test", mode="run", status="error",
        progress=RuntimeProgress(current_step=0, total_steps=1, percent=0, elapsed_ms=0),
        created_at=now, updated_at=now,
    ).model_copy(update=updates)


async def test_task_log_error_and_confirmation_are_redacted_before_storage_and_publish():
    secret = "synthetic-password-alpha"
    variables = RuntimeVariableStore.from_initial({"password": secret}, sensitive_names={"password"})
    record = TaskRecord(request=RunTaskRequest(flow_name="test"), snapshot=task_snapshot(), variables=variables)
    manager = TaskManager.__new__(TaskManager)
    manager._task_store = SimpleNamespace(append_log=AsyncMock(), save_task=AsyncMock())
    manager._broker = SimpleNamespace(publish=AsyncMock())
    outcome = await VariableActionRunner().run(
        {"type": "variable.log", "message": "${var.password}"}, variables, timeout_ms=100,
    )
    variables.set("password", "synthetic-password-beta")
    await manager._append_log(record, "error", outcome.detail, f"request failed: {secret}")
    await manager._update_snapshot(record, error=f"failed: {secret}", confirmation_message=f"confirm: {secret}")
    entry = record.logs[0]
    assert secret not in entry.model_dump_json()
    assert secret not in record.snapshot.error
    assert secret not in record.snapshot.confirmation_message
    manager._task_store.append_log.assert_awaited_once_with(entry)
    manager._broker.publish.assert_awaited_once_with(entry)
    assert manager._task_store.save_task.await_args.args[0].error == "failed: [REDACTED]"
    assert variables.get("password") == "synthetic-password-beta"


@pytest.mark.parametrize("tool", ["get_run_logs", "get_run_error", "get_run_output", "get_run_status"])
@pytest.mark.parametrize("persisted", [False, True])
async def test_ai_task_reads_redact_raw_legacy_errors_and_logs(tool, persisted):
    secret = "synthetic-password-alpha"
    protected = RuntimeVariableSnapshot(name="password", type="String", value=secret, sensitive=True)
    task = task_snapshot(error=f"request failed: {secret}", variables=[protected.model_copy(update={"value": ""}) if persisted else protected])
    manager = SimpleNamespace(
        get_task=AsyncMock(return_value=task),
        get_logs=AsyncMock(return_value=[TaskLogEntry(task_id="t", level="error", message=secret, detail=secret)]),
    )
    service = SimpleNamespace(get_flow=AsyncMock(return_value=SimpleNamespace(definition={"nodes": []}, input_variables=[protected])))
    result = await RpaToolExecutor(service, manager).execute(tool, {"task_id": "t"}, change_context=ChangeContext(flow_id="f"))
    assert secret not in json.dumps(result)
    assert "[REDACTED]" in json.dumps(result)


async def test_redaction_precedes_output_truncation_and_preserves_protocol_status():
    secret = "long-synthetic-secret-" * 40
    task = task_snapshot(status="success", variables=[
        RuntimeVariableSnapshot(name="password", type="String", value=secret, sensitive=True),
        RuntimeVariableSnapshot(name="short_password", type="String", value="success", sensitive=True),
        RuntimeVariableSnapshot(name="ordinary_alias", type="String", value=secret),
        RuntimeVariableSnapshot(name="ordinary_output", type="String", value="z" * 600),
    ])
    result = await RpaToolExecutor(None, SimpleNamespace(get_task=AsyncMock(return_value=task))).execute("get_run_output", {"task_id": "t"})
    assert "long-synthetic-secret" not in json.dumps(result)
    assert result["variables"]["ordinary_alias"] == "[REDACTED]"
    assert result["variables"]["ordinary_output"] == "z" * 500 + "…（已截断）"
    assert result["status"] == "success"


def test_value_redaction_handles_overlap_and_partial_secret_patterns():
    secret = "prefix-password=sample"
    text = redact_log_text(f"value {secret}; abcdef", [secret, "abc", "abcdef"])
    assert "prefix-" not in text
    assert text == "value [REDACTED]; [REDACTED]"
    assert redact_log_text("R [REDACTED]", ["R"]) == "[REDACTED] [REDACTED]"


@pytest.mark.parametrize("tool", ["stop_run", "get_run_error", "get_run_logs", "get_run_output", "get_run_status", "audit_run"])
@pytest.mark.parametrize("bound,actual", [("f", "other"), (None, "other"), ("f", None)])
async def test_task_ownership_is_checked_before_dispatch(tool, bound, actual):
    task = task_snapshot(flow_id=actual) if actual else None
    manager = SimpleNamespace(get_task=AsyncMock(return_value=task), stop_task=AsyncMock(), get_logs=AsyncMock())
    executor = RpaToolExecutor(None, manager)
    executor._dispatch = AsyncMock()
    result = await executor.execute(tool, {"task_id": "t"}, change_context=ChangeContext(flow_id=bound))
    assert result["error"] == "tool_target_flow_mismatch"
    executor._dispatch.assert_not_awaited()
    manager.stop_task.assert_not_awaited()
    manager.get_logs.assert_not_awaited()


async def test_owned_stop_is_allowed_and_returns_its_flow():
    manager = SimpleNamespace(get_task=AsyncMock(return_value=task_snapshot(status="running")),
                              stop_task=AsyncMock(return_value=task_snapshot(status="stopped")))
    result = await RpaToolExecutor(None, manager).execute("stop_run", {"task_id": "t"}, change_context=ChangeContext(flow_id="f"))
    assert result["flow_id"] == "f" and result["status"] == "stopped"
    manager.stop_task.assert_awaited_once_with("t")


async def test_prefetched_reads_receive_the_same_trusted_flow_context():
    executor = SimpleNamespace(execute=AsyncMock(return_value={}))
    tasks = AiOrchestrator(executor)._prefetch_read_only_tools([
        (0, {"name": "get_run_logs", "arguments": '{"task_id":"other-task"}'}),
        (1, {"name": "list_schedules", "arguments": '{}'}),
    ], GuardState(flow_id="f"))
    await asyncio.gather(*tasks.values())
    assert len(executor.execute.await_args_list) == 2
    assert all(call.kwargs["change_context"].flow_id == "f" for call in executor.execute.await_args_list)


async def test_schedule_ownership_prevents_mutation_and_filters_listing():
    def schedule(flow):
        return SimpleNamespace(schedule_id=flow, name=flow, cron_expression="0 9 * * *", timezone="UTC",
                               status="disabled", task=SimpleNamespace(flow_id=flow, browser_executor="playwright"),
                               next_run_at=None, last_run_at=None)
    own, other = schedule("f"), schedule("other")
    service = SimpleNamespace(get_schedule=AsyncMock(return_value=other), update_schedule=AsyncMock(),
                              list_schedules=AsyncMock(return_value=[own, other]))
    executor = RpaToolExecutor(None, None, service)
    context = ChangeContext(flow_id="f")
    rejected = await executor.execute("toggle_schedule", {"schedule_id": "other", "enabled": False}, change_context=context)
    assert rejected["error"] == "tool_target_flow_mismatch"
    service.update_schedule.assert_not_awaited()
    result = await executor.execute("list_schedules", {}, change_context=context)
    assert result["count"] == 1 and result["schedules"][0]["flow_id"] == "f"


async def test_schedule_service_rechecks_flow_at_mutation_boundary():
    service = ScheduleService(SimpleNamespace())
    schedule = await service.create_schedule(ScheduleCreateRequest(
        name="test", enabled=False, cron_expression="0 9 * * *", task=ScheduleTaskRequest(flow_id="other"),
    ))
    with pytest.raises(ValueError, match="tool_target_flow_mismatch"):
        await service.update_schedule(schedule.schedule_id, ScheduleUpdateRequest(enabled=False), expected_flow_id="f")
    assert await service.get_schedule(schedule.schedule_id) == schedule


@pytest.mark.parametrize("tool,args,text", [
    ("stop_run", {"task_id": "t"}, "停止当前运行"),
    ("toggle_schedule", {"schedule_id": "s", "enabled": False}, "停用当前流程的定时任务"),
])
def test_failure_lock_allows_explicit_stop_and_disable_but_preserves_read_only(tool, args, text):
    state = GuardState(flow_id="f", flow_has_nodes=True, failure_budget_lock={"flow_id": "f"}, latest_user_message=text)
    assert _orchestrator_guard_before_tool(tool, args, state) is None
    state.read_only_tools = True
    assert _orchestrator_guard_before_tool(tool, args, state) is not None


def test_dynamic_reads_refresh_and_back_off_only_when_content_is_unchanged(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("app.services.ai_phases.time.monotonic", lambda: now[0])
    state = GuardState(flow_has_nodes=True, _last_tool_args={"task_id": "t"})
    args = {"task_id": "t"}
    first = {"logs": [{"message": "first"}], "count": 1}
    _orchestrator_guard_after_tool("get_run_logs", first, state)
    assert evidence_already_collected("get_run_logs", args, state)
    now[0] += 5
    assert not evidence_already_collected("get_run_logs", args, state)
    _orchestrator_guard_after_tool("get_run_logs", first, state)
    now[0] += 5
    assert evidence_already_collected("get_run_logs", args, state)
    now[0] += 5
    assert not evidence_already_collected("get_run_logs", args, state)
    _orchestrator_guard_after_tool("get_run_logs", {"logs": [{"message": "second"}], "count": 2}, state)
    now[0] += 5
    assert not evidence_already_collected("get_run_logs", args, state)
    note_evidence(state, "get_run_output", args, {"status": "success"})
    now[0] += 600
    assert evidence_already_collected("get_run_output", args, state)
    note_progress(state)
    assert not evidence_already_collected("get_run_output", args, state)


def test_async_page_can_refresh_without_an_unrelated_mutation(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("app.services.ai_phases.time.monotonic", lambda: now[0])
    state = GuardState()
    args = {"url": "https://example.test", "wait_selector": ".results"}
    note_evidence(state, "inspect_page", args, {"wait_result": {"status": "timed_out"}})
    assert evidence_already_collected("inspect_page", args, state)
    now[0] += 5
    assert not evidence_already_collected("inspect_page", args, state)


async def test_incremental_input_declarations_preserve_user_values_and_invalidate_old_evidence():
    service = FlowService()
    flow = await service.create_flow(FlowCreateRequest(name="test", input_variables=[
        RuntimeVariableSnapshot(name="password", type="String", value="synthetic-user-secret", sensitive=True),
        RuntimeVariableSnapshot(name="limit", type="Integer", value="20"),
    ], definition={"nodes": [{"id": "start", "type": "start"}, {"id": "end", "type": "end"}],
                   "edges": [{"id": "e", "source": "start", "target": "end"}]}))
    executor = RpaToolExecutor(service, None)
    args = {"flow_id": flow.flow_id, "input_variables": [
        {"name": "password", "type": "String", "value": ""},
        {"name": "limit", "type": "String", "value": "wrong"},
        {"name": "date_start", "type": "String", "value": "2026-10-01"},
        {"name": "api_token", "type": "String", "value": ""},
    ]}
    result = await executor.execute("update_flow", args, change_context=ChangeContext(flow_id=flow.flow_id))
    assert result["status"] == "applied" and result["execution_changed"]
    saved = await service.get_flow(flow.flow_id)
    assert saved.input_variables[:2] == flow.input_variables
    assert saved.input_variables[-1].sensitive and saved.input_variables[-1].category == "credential"
    assert saved.revision == flow.revision + 1
    assert "synthetic-user-secret" not in json.dumps(result)
    state = GuardState(current_flow_revision=flow.revision, accepted_revision=flow.revision)
    reduce_evidence_state(state, attach_tool_events("update_flow", result))
    assert state.accepted_revision is None
    again = await executor.execute("update_flow", args, change_context=ChangeContext(flow_id=flow.flow_id))
    assert again["status"] == "no_effective_change" and not again["execution_changed"]
    assert (await service.get_flow(flow.flow_id)).revision == saved.revision


async def test_incremental_declarations_cannot_write_existing_protected_values_or_duplicate_names():
    service = FlowService()
    flow = await service.create_flow(FlowCreateRequest(name="test", input_variables=[
        RuntimeVariableSnapshot(name="access_code", type="String", value="synthetic-user-secret", sensitive=True),
    ]))
    executor = RpaToolExecutor(service, None)
    rejected = await executor.execute("update_flow", {"flow_id": flow.flow_id, "input_variables": [
        {"name": "access_code", "type": "String", "value": "synthetic-ai-secret", "sensitive": False},
    ]})
    assert rejected["status"] == "blocked_credential_values"
    assert "synthetic-ai-secret" not in json.dumps(rejected)
    duplicate = await executor.execute("update_flow", {"flow_id": flow.flow_id, "input_variables": [
        {"name": "new", "type": "String"}, {"name": "new", "type": "String"},
    ]})
    assert duplicate["error"] == "invalid_input_variables"
    assert await service.get_flow(flow.flow_id) == flow


def contract_arguments(**assertions):
    return {"flow_id": "f", "acceptance_contract": {
        "requirements": [{"id": "r", "description": "test", "source_kind": "product_default"}],
        "deliverables": [{"id": "d", "variable": "rows", "kind": "table", "requirement_ids": ["r"], **assertions}],
    }}


def test_every_nested_acceptance_assertion_is_usable_and_rejects_unknown_fields():
    assertions = {
        "date_ranges": [{"field": "date", "start": "2026-10-01", "end": "2026-10-31"}],
        "allowed_values": [{"field": "state", "values": ["ok"]}],
        "numeric_ranges": [{"field": "amount", "minimum": 0, "maximum": 100}],
        "field_formats": [{"field": "date", "format": "date"}],
        "cross_field_assertions": [{"left_field": "paid", "operator": "lte", "right_field": "amount"}],
        "sort_assertions": [{"field": "date", "direction": "asc"}],
        "aggregate_assertions": [{"operation": "count", "expected": 10, "tolerance": 0}],
    }
    args = contract_arguments(**assertions)
    assert validate_tool_arguments("set_acceptance_contract", args) is None
    FlowAcceptanceContract.model_validate(args["acceptance_contract"])
    for key, values in assertions.items():
        rejected = contract_arguments(**{key: [{**values[0], "unknown": "synthetic-private-value"}]})
        result = validate_tool_arguments("set_acceptance_contract", rejected)
        assert result["error"] == "invalid_arguments"
        assert "synthetic-private-value" not in json.dumps(result)
    wrong_date = validate_tool_arguments("set_acceptance_contract", contract_arguments(date_ranges=[{"field": "date", "start_date": "2026-10-01"}]))
    assert wrong_date["issues"][0]["fields"] == ["start_date"]


def test_log_enum_matches_runtime_and_run_tool_requires_authorization():
    for level in get_args(TaskLogLevel):
        assert validate_tool_arguments("get_run_logs", {"task_id": "t", "level": level}) is None
    assert validate_tool_arguments("get_run_logs", {"task_id": "t", "level": "debug"}) is not None
    state = GuardState(flow_has_nodes=True)
    assert "run_flow" not in {s["function"]["name"] for s in _tool_schemas_for_round(state, _TurnIntents())}
    assert _orchestrator_guard_before_tool("run_flow", {"flow_id": "f"}, state)["guard_id"] == "run_not_authorized"
    state.run_authorized = True
    assert "run_flow" in {s["function"]["name"] for s in _tool_schemas_for_round(state, _TurnIntents())}


def test_non_browser_prompt_uses_the_same_security_rules_and_conservative_domain_selection():
    state = GuardState(flow_has_nodes=True, latest_user_message="修改文件输出路径")
    flow = FlowState(is_blank=False, nodes=[{"id": "save", "type": "file.write"}])
    prompt = _system_prompt_for_round(state, flow)
    assert len(prompt) < len(SYSTEM_PROMPT) * 0.8
    assert render_guard_contract() in prompt
    assert "普通输入与秘密分流" in prompt
    assert "选择器可靠性规范" not in prompt
    state.latest_user_message = "增加网页登录"
    assert _system_prompt_for_round(state, flow) == SYSTEM_PROMPT
    state.latest_user_message = "继续"
    flow.nodes.append({"id": "unknown", "type": "future.node"})
    assert _system_prompt_for_round(state, flow) == SYSTEM_PROMPT
    assert _system_prompt_for_round(state) == SYSTEM_PROMPT


@pytest.mark.parametrize("value", [0, False, "synthetic-secret"])
@pytest.mark.parametrize("field", ["value", "defaultValue"])
async def test_falsy_credential_values_are_still_rejected_before_normalization(value, field):
    executor = RpaToolExecutor(None, None)
    result = await executor.execute("create_flow", {"name": "test", "nodes": [], "input_variables": [
        {"name": "password", "type": "String", field: value},
    ]})
    assert result["status"] == "blocked_credential_values"


def test_repeat_evidence_reports_refresh_delay_without_consuming_failure_budget(monkeypatch):
    monkeypatch.setattr("app.services.ai_phases.time.monotonic", lambda: 100)
    state = GuardState(flow_has_nodes=True)
    args = {"task_id": "t"}
    note_evidence(state, "get_run_logs", args, {"count": 1})
    result = _orchestrator_guard_before_tool("get_run_logs", args, state)
    assert result["retry_after_seconds"] == 5
    assert state.attempt_budget["spent"] == 0


async def test_input_declarations_are_visible_to_same_write_node_validation():
    service = FlowService()
    flow = await service.create_flow(FlowCreateRequest(name="test", definition={
        "nodes": [{"id": "start", "type": "start"}, {"id": "end", "type": "end"}],
        "edges": [{"id": "e", "source": "start", "target": "end"}],
    }))
    result = await RpaToolExecutor(service, None).execute("update_flow", {
        "flow_id": flow.flow_id,
        "input_variables": [{"name": "source_path", "type": "String", "value": "input.txt"}],
        "add_nodes": [{"id": "log", "type": "variable.log", "message": "${var.source_path}"}],
        "remove_edge_ids": ["e"],
        "add_edges": [{"source": "start", "target": "log"}, {"source": "log", "target": "end"}],
    })
    assert result["status"] == "applied"
    assert not result.get("validation_issues")
    assert not any(item.get("blocks_run") for item in result.get("lint_findings", []))


async def test_owned_schedule_toggle_keeps_binding_and_disables_without_running():
    service = ScheduleService(SimpleNamespace())
    schedule = await service.create_schedule(ScheduleCreateRequest(
        name="test", enabled=False, cron_expression="0 9 * * *", task=ScheduleTaskRequest(flow_id="f"),
    ))
    result = await RpaToolExecutor(None, None, service).execute("toggle_schedule", {
        "schedule_id": schedule.schedule_id, "enabled": False,
    }, change_context=ChangeContext(flow_id="f"))
    assert result["status"] == "disabled" and result["flow_id"] == "f"
    assert (await service.get_schedule(schedule.schedule_id)).task == schedule.task
