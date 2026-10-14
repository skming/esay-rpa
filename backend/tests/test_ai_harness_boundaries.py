"""跨编排与执行器边界的回归；只使用隔离存储和内存任务，不访问真实站点。"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.schemas import FlowAcceptanceContract, RuntimeVariableSnapshot
from app.services import ai_session_checkpoint as checkpoint
from app.services.ai_flow_state import FlowState, _refresh_run
from app.services.ai_guard_state import GuardState
from app.services.ai_orchestrator import (
    AiOrchestrator, _RunProgressSink, _conversation_task_key, _detect_turn_intents, _history_run_handle,
    _orchestrator_guard_after_tool, _sync_run_result,
)
from app.services.ai_tool_events import attach_tool_events, reduce_evidence_state
from app.services.ai_tools.executor import RpaToolExecutor
from app.services.ai_tools.lint_diff import ChangeContext
from app.services.task_manager import TaskManager


@pytest.mark.parametrize("status", ["queued", "awaiting_confirmation", "running", "success", "error", "stopped"])
async def test_ai_output_redacts_in_memory_secrets_in_every_task_state(status):
    task = SimpleNamespace(task_id="t", status=status, artifacts=[], error=None, variables=[
        RuntimeVariableSnapshot(name="secret_a", type="String", value="synthetic-secret-a", sensitive=True),
        RuntimeVariableSnapshot(name="secret_b", type="String", value="synthetic-secret-b", category="credential"),
        RuntimeVariableSnapshot(name="rows", type="Integer", value="12"),
    ])
    manager = TaskManager.__new__(TaskManager)
    manager._tasks = {"t": SimpleNamespace(snapshot=task)}
    result = await RpaToolExecutor(None, manager).execute("get_run_output", {"task_id": "t"})
    assert "synthetic-secret" not in json.dumps(result)
    if status != "running":
        assert result["variables"] == {"rows": "12"}
        assert result["redacted_variables"] == ["secret_a", "secret_b"]


@pytest.mark.parametrize("tool", ["run_flow", "create_schedule"])
async def test_ai_cannot_override_credentials_at_execution_boundary(tool):
    from test_ai_tools import _CredentialFlowService, _cred, FakeTaskManager, FakeScheduleService
    manager = FakeTaskManager(with_failing_tasks=False)
    executor = RpaToolExecutor(_CredentialFlowService([_cred("password")]), manager, FakeScheduleService())
    args = {"flow_id": "f", "variables": {"password": "synthetic-secret"}}
    if tool == "create_schedule":
        args["cron_expression"] = "0 9 * * *"
    result = await executor.execute(tool, args)
    assert result["status"] == "blocked_credential_override"
    assert "synthetic-secret" not in json.dumps(result)
    assert not manager.started


@pytest.mark.parametrize("text", [
    "只解释验收标准，先别重跑", "不要验收，只改配置", "Don't run it", "解释一下验收标准",
])
def test_negated_and_explanatory_run_requests_do_not_authorize_execution(text):
    messages = [{"role": "user", "content": "运行一次"}, {"role": "user", "content": text}]
    assert not _detect_turn_intents(messages, "f", FlowState(flow_id="f", is_blank=False)).run_authorized


def _contract(min_rows=100):
    return FlowAcceptanceContract.model_validate({
        "requirements": [{"id": "r1", "description": "订单数据", "sourceQuote": "至少100行订单，包含订单号"}],
        "deliverables": [{"id": "d1", "variable": "rows", "kind": "table", "required": True,
                          "minRows": min_rows, "requiredFields": ["订单号"], "requirementIds": ["r1"]}],
    })


def _contract_executor():
    flow = SimpleNamespace(revision=7, status="active", input_variables=[], acceptance_contract=_contract(),
                           definition={"nodes": [{"id": "rows", "type": "variable.set", "variableName": "rows", "value": []}]})
    service = SimpleNamespace(get_flow=AsyncMock(return_value=flow),
                              update_flow=AsyncMock(return_value=SimpleNamespace(revision=8, acceptance_contract=_contract(1))))
    return RpaToolExecutor(service, None), service


@pytest.mark.parametrize("change", ["rows", "fields", "required", "delete"])
async def test_existing_contract_cannot_be_weakened_by_a_repair_quote(change):
    executor, service = _contract_executor()
    raw = _contract().model_dump(mode="json", exclude_none=True)
    if change == "delete":
        raw["deliverables"] = []
    else:
        raw["deliverables"][0].update({"rows": {"min_rows": 1}, "fields": {"required_fields": []}, "required": {"required": False}}[change])
    result = await executor.execute("set_acceptance_contract", {
        "flow_id": "f", "acceptance_contract": raw, "requirement_change_quote": "修复流程",
    }, change_context=ChangeContext(flow_id="f", flow_revision=7, latest_user_message="修复流程，保持原要求"))
    assert result["status"] == "blocked_acceptance_contract_change", result
    service.update_flow.assert_not_awaited()


async def test_contract_change_requires_trusted_matching_revision_and_explicit_requirement():
    executor, service = _contract_executor()
    quote = "把交付行数从100行改为1行"
    args = {"flow_id": "f", "acceptance_contract": _contract(1).model_dump(mode="json", exclude_none=True), "requirement_change_quote": quote}
    assert (await executor.execute("set_acceptance_contract", args))["status"] == "blocked_acceptance_contract_change"
    assert (await executor.execute("set_acceptance_contract", args, change_context=ChangeContext(
        flow_id="f", flow_revision=6, latest_user_message=quote,
    )))["status"] == "blocked_acceptance_contract_change"
    result = await executor.execute("set_acceptance_contract", args, change_context=ChangeContext(
        flow_id="f", flow_revision=7, latest_user_message=quote,
    ))
    assert result["status"] == "applied"
    service.update_flow.assert_awaited_once()


async def test_unchanged_contract_aliases_do_not_increment_revision():
    executor, service = _contract_executor()
    result = await executor._set_acceptance_contract("f", _contract().model_dump(mode="json", by_alias=True, exclude_none=True))
    assert result["status"] == "no_effective_change", result
    service.update_flow.assert_not_awaited()
    state = GuardState(current_flow_revision=7, accepted_revision=7, run_succeeded=True, audit_passed=True)
    result = attach_tool_events("set_acceptance_contract", result)
    reduce_evidence_state(state, result)
    _orchestrator_guard_after_tool("set_acceptance_contract", result, state)
    assert state.accepted_revision == 7
    assert state.audit_passed and state.run_succeeded
    assert not result.get("events")


@pytest.mark.parametrize("tool", ["create_flow", "set_acceptance_contract"])
def test_rejected_contract_write_preserves_all_guard_state(tool):
    from dataclasses import asdict
    state = GuardState(flow_has_nodes=False, run_succeeded=True, audit_passed=True,
                       current_flow_revision=7, accepted_revision=7)
    before = asdict(state)
    result = attach_tool_events(tool, {"status": "blocked_acceptance_contract_change", "flow_id": "f"})
    reduce_evidence_state(state, result)
    _orchestrator_guard_after_tool(tool, result, state)
    assert asdict(state) == before


@pytest.mark.parametrize("quote,change", [
    ("把交付格式改成 Excel", {"min_rows": 1}),
    ("把交付行数从100行改为1行", {"required_fields": []}),
    ("把交付行数从100行改为10行", {"min_rows": 1}),
])
async def test_explicit_requirement_change_does_not_authorize_unrelated_weakening(quote, change):
    executor, service = _contract_executor()
    raw = _contract().model_dump(mode="json", exclude_none=True)
    raw["deliverables"][0].update(change)
    result = await executor.execute("set_acceptance_contract", {
        "flow_id": "f", "acceptance_contract": raw, "requirement_change_quote": quote,
    }, change_context=ChangeContext(flow_id="f", flow_revision=7, latest_user_message=quote))
    assert result["status"] == "blocked_acceptance_contract_change", result
    service.update_flow.assert_not_awaited()


@pytest.mark.parametrize("cancel_during_submission", [False, True])
async def test_cancelled_run_observer_persists_handle_and_next_call_reuses_task(monkeypatch, tmp_path, cancel_during_submission):
    from test_ai_tools import FakeTaskManager, _SimpleFlowService
    monkeypatch.setattr(checkpoint, "resolve_ai_dir", lambda: tmp_path)
    manager = FakeTaskManager(with_failing_tasks=False)
    started = asyncio.Event()
    release_submission = asyncio.Event()

    async def start(request):
        from datetime import UTC, datetime
        from app.models.schemas import TaskSnapshot, RuntimeProgress
        task = TaskSnapshot(taskId="t", flowId="f", flowName="test", status="queued", mode="run",
                            progress=RuntimeProgress(currentStep=0, totalSteps=1, percent=0, elapsedMs=0), createdAt=datetime.now(UTC), updatedAt=datetime.now(UTC),
                            flowRevision=1, definitionDigest="digest")
        manager.tasks.append(task)
        started.set()
        if cancel_during_submission:
            await release_submission.wait()
        return task

    manager.start_task = AsyncMock(side_effect=start)
    executor = RpaToolExecutor(_SimpleFlowService(), manager)
    state = GuardState(flow_id="f", task_key="a" * 64)
    observer = asyncio.create_task(executor._run_flow("f", progress_sink=_RunProgressSink(state, 1)))
    await asyncio.wait_for(started.wait(), 2)
    observer.cancel()
    release_submission.set()
    with pytest.raises(asyncio.CancelledError):
        await observer
    assert checkpoint.load("f", task_key="a" * 64)["pending_run"]["task_id"] == "t"
    result = await executor._run_flow("f")
    assert result["reused_existing_run"] is True
    manager.start_task.assert_awaited_once()


@pytest.mark.parametrize("status", ["queued", "running", "awaiting_confirmation"])
async def test_history_resume_refreshes_authoritative_task_and_hides_duplicate_run(monkeypatch, tmp_path, status):
    import litellm
    from test_ai_orchestrator import _RunnableFlowExecutor, _FakeStream, _chunk
    monkeypatch.setattr(checkpoint, "resolve_ai_dir", lambda: tmp_path)

    class Executor(_RunnableFlowExecutor):
        async def execute(self, name, args, *rest):
            if name == "get_run_status":
                self.calls.append((name, args))
                return {"task_id": "t", "flow_id": "flow-run", "status": status, "flow_revision": 3}
            return await super().execute(name, args, *rest)

    requests = []
    async def complete(**kwargs):
        requests.append(kwargs)
        return _FakeStream([_chunk(content="原任务仍在处理中。", finish="stop")])
    monkeypatch.setattr(litellm, "acompletion", complete)
    executor = Executor()
    events = [event async for event in AiOrchestrator(executor).stream([
        {"role": "user", "content": "运行一次"},
        {"role": "assistant", "content": "后台运行中", "toolCalls": [{"tool": "run_flow", "result": {
            "task_id": "t", "flow_id": "flow-run", "status": "timeout", "flow_revision": 3,
        }}]},
        {"role": "user", "content": "继续"},
    ], "test-model", "flow-run")]
    assert not [event for event in events if event["type"] == "error"]
    assert [name for name, _ in executor.calls].count("get_run_status") == 1
    assert "run_flow" not in [item["function"]["name"] for item in requests[0].get("tools", [])]
    assert checkpoint.load("flow-run")["pending_run"]["status"] == status


async def test_resumed_task_cannot_import_another_flows_success():
    executor = SimpleNamespace(execute=AsyncMock(return_value={"task_id": "t", "flow_id": "other", "status": "success"}))
    assert await _refresh_run(executor, {"task_id": "t", "flow_id": "f", "status": "pending"}) is None
    executor.execute.assert_awaited_once()


def test_history_restores_the_latest_failed_run_instead_of_an_older_success():
    messages = [{"role": "assistant", "toolCalls": [
        {"tool": "run_flow", "result": {"task_id": "old", "flow_id": "f", "status": "success"}},
        {"tool": "run_flow", "result": {"task_id": "latest", "flow_id": "f", "status": "error", "error": "failed"}},
    ]}]
    assert _history_run_handle(messages, "f")["task_id"] == "latest"


@pytest.mark.parametrize("status", ["error", "success"])
def test_restoring_a_failed_run_does_not_charge_the_retry_budget_twice(monkeypatch, tmp_path, status):
    monkeypatch.setattr(checkpoint, "resolve_ai_dir", lambda: tmp_path)
    state = GuardState(flow_id="f", current_flow_revision=3, current_definition_digest="digest")
    result = {"task_id": "t", "flow_id": "f", "status": status, "flow_revision": 3, "definition_digest": "digest",
              "error_summary": "failed"} if status == "error" else {
        "task_id": "t", "flow_id": "f", "status": "success", "flow_revision": 3, "definition_digest": "digest",
        "acceptance_audit": {"passed": False, "issues": [{"issue": "missing_rows"}]},
    }
    assert _sync_run_result(None, result, state)
    spent = state.attempt_budget["spent"]
    checkpoint.finish("f", state, rounds=1)
    restored = GuardState(flow_id="f", current_flow_revision=3, current_definition_digest="digest")
    restored.apply_checkpoint(checkpoint.load("f"))
    assert not _sync_run_result(None, result, restored)
    assert restored.attempt_budget["spent"] == spent


def test_new_task_does_not_inherit_old_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(checkpoint, "resolve_ai_dir", lambda: tmp_path)
    history = [{"role": "user", "content": "修复旧流程"}]
    key = _conversation_task_key(history)
    state = GuardState(task_key=key, attempt_budget={"spent": 3, "signatures": {}, "attempts": []})
    checkpoint.save("f", state, rounds=2)
    assert _conversation_task_key(history + [{"role": "user", "content": "继续"}]) == key
    new_key = _conversation_task_key(history + [{"role": "user", "content": "新任务：生成日报"}])
    assert checkpoint.load("f", task_key=new_key) == {}
    assert checkpoint.load("f", task_key=key)["attempt_budget"]["spent"] == 3


async def test_unshrinkable_request_stops_before_a_model_call(monkeypatch):
    import litellm
    from app.services import ai_orchestrator
    from test_ai_orchestrator import _RunnableFlowExecutor
    complete = AsyncMock(side_effect=AssertionError("超限请求不得发送"))
    monkeypatch.setattr(litellm, "acompletion", complete)
    monkeypatch.setattr(ai_orchestrator, "_context_char_budget", lambda model: 100)
    events = [event async for event in AiOrchestrator(_RunnableFlowExecutor()).stream(
        [{"role": "user", "content": "只看结构"}], "test-model", "flow-run",
    )]
    assert any(event.get("code") == "context_budget_exceeded" for event in events)
    complete.assert_not_awaited()


async def test_concurrent_ai_requests_cannot_submit_the_same_flow_twice(monkeypatch):
    executor = RpaToolExecutor(None, None)
    entered, release = asyncio.Event(), asyncio.Event()
    async def observe(*args):
        entered.set()
        await release.wait()
        return {"status": "success"}
    observe_mock = AsyncMock(side_effect=observe)
    monkeypatch.setattr(executor, "_observe_run_flow", observe_mock)
    first = asyncio.create_task(executor._run_flow("f"))
    await entered.wait()
    try:
        assert (await executor._run_flow("f"))["status"] == "blocked_run_in_progress"
        observe_mock.assert_awaited_once()
    finally:
        release.set()
        await first
    assert not executor._observing_runs


async def test_images_follow_all_results_in_a_multi_tool_batch(monkeypatch):
    import litellm
    from test_ai_orchestrator import _RunnableFlowExecutor, _FakeStream, _chunk, _tool_call_chunk
    class Executor(_RunnableFlowExecutor):
        async def execute(self, name, args, *rest):
            if name == "inspect_screenshot":
                return {"status": "success", "image_base64": "synthetic-image"}
            if name == "get_run_logs":
                return {"logs": []}
            return await super().execute(name, args, *rest)
    requests = []
    async def complete(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return _FakeStream([_chunk(tool_calls=[
                _tool_call_chunk(0, call_id="image", name="inspect_screenshot", arguments="{}"),
                _tool_call_chunk(1, call_id="logs", name="get_run_logs", arguments='{"task_id":"t"}'),
            ], finish="tool_calls")])
        pending = set()
        saw_image = False
        for message in kwargs["messages"]:
            assert "_context_synthetic" not in message
            if message["role"] == "assistant":
                pending.update(call["id"] for call in message.get("tool_calls") or [])
            elif message["role"] == "tool":
                pending.remove(message["tool_call_id"])
            elif message["role"] == "user" and isinstance(message["content"], list):
                assert not pending
                saw_image = True
        assert saw_image and not pending
        return _FakeStream([_chunk(content="已读取页面截图和任务日志。", finish="stop")])
    monkeypatch.setattr(litellm, "acompletion", complete)
    events = [event async for event in AiOrchestrator(Executor()).stream(
        [{"role": "user", "content": "只看结构"}], "gpt-4o", "flow-run",
    )]
    assert not [event for event in events if event["type"] == "error"]
    assert len(requests) == 2
