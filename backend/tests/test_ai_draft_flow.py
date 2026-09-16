from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.schemas import FlowCreateRequest
from app.services.ai_guard_state import GuardState
from app.services.ai_orchestrator import _build_change_context
from app.services.ai_tools.executor import RpaToolExecutor
from app.services.ai_tools.lint_diff import ChangeContext
from app.services.flow_service import FlowService


def generated_args():
    return {
        "name": "生成流程",
        "nodes": [{"id": "n1", "type": "variable.set", "variableName": "result", "value": "完成"}],
        "acceptance_contract": {
            "requirements": [{"id": "r1", "description": "生成结果", "source_kind": "product_default"}],
            "deliverables": [{"id": "d1", "variable": "result", "kind": "scalar", "requirement_ids": ["r1"]}],
        },
    }


async def test_create_flow_fills_persisted_draft_and_preserves_id():
    service = FlowService()
    draft = await service.create_flow(FlowCreateRequest(name="未命名流程"))
    executor = RpaToolExecutor(service, SimpleNamespace())
    result = await executor.execute("create_flow", generated_args(), change_context=ChangeContext(draft_flow_id=draft.flow_id))
    assert result.get("error") is None, result
    assert result["flow_id"] == draft.flow_id
    assert result["status"] == "active"
    saved = await service.get_flow(draft.flow_id)
    assert saved.name == "生成流程"
    assert len(await service.list_flows()) == 1


@pytest.mark.parametrize("status,nodes", [("active", []), ("draft", [{"id": "existing", "type": "variable.set"}])])
async def test_create_flow_does_not_replace_existing_content(status, nodes):
    service = FlowService()
    draft = await service.create_flow(FlowCreateRequest(name="保留", status=status, definition={"nodes": nodes}))
    executor = RpaToolExecutor(service, SimpleNamespace())
    result = await executor.execute("create_flow", generated_args(), change_context=ChangeContext(draft_flow_id=draft.flow_id))
    assert result["error"] == "draft_flow_not_empty"
    assert await service.get_flow(draft.flow_id) == draft
    assert len(await service.list_flows()) == 1


async def test_invalid_generation_leaves_draft_unchanged():
    service = FlowService()
    draft = await service.create_flow(FlowCreateRequest(name="未命名流程"))
    executor = RpaToolExecutor(service, SimpleNamespace())
    args = generated_args()
    args["acceptance_contract"]["deliverables"][0]["variable"] = "missing"
    result = await executor.execute("create_flow", args, change_context=ChangeContext(draft_flow_id=draft.flow_id))
    assert result["error"] == "acceptance_contract_invalid"
    assert await service.get_flow(draft.flow_id) == draft


async def test_deleted_draft_does_not_create_replacement():
    service = SimpleNamespace(get_flow=AsyncMock(return_value=None), create_flow=AsyncMock())
    result = await RpaToolExecutor(service, SimpleNamespace()).execute(
        "create_flow", generated_args(), change_context=ChangeContext(draft_flow_id="deleted"),
    )
    assert result["error"] == "draft_flow_not_found"
    service.create_flow.assert_not_called()


def test_change_context_binds_only_blank_flow():
    assert _build_change_context(GuardState(flow_id="draft")).draft_flow_id == "draft"
    assert _build_change_context(GuardState(flow_id="existing", flow_has_nodes=True)).draft_flow_id is None


async def test_persisted_draft_survives_service_restart_and_generation(tmp_path):
    from app.services.flow_store import SqlAlchemyFlowStore
    from app.services.schedule_store import create_schedule_engine

    url = f"sqlite+aiosqlite:///{tmp_path / 'draft.db'}"
    engine = create_schedule_engine(url)
    store = SqlAlchemyFlowStore(engine)
    await store.create_schema()
    original = await FlowService(store).create_flow(FlowCreateRequest(name="未命名流程"))
    await engine.dispose()

    engine = create_schedule_engine(url)
    try:
        service = FlowService(SqlAlchemyFlowStore(engine))
        restored = await service.get_flow(original.flow_id)
        assert restored.status == "draft"
        assert restored.definition == {}
        result = await RpaToolExecutor(service, SimpleNamespace()).execute(
            "create_flow", generated_args(), change_context=ChangeContext(draft_flow_id=original.flow_id),
        )
        assert result["flow_id"] == original.flow_id
        assert result["status"] == "active"
        assert [flow.flow_id for flow in await service.list_flows()] == [original.flow_id]
    finally:
        await engine.dispose()


async def test_update_flow_keeps_draft_until_contract_is_added():
    service = FlowService()
    draft = await service.create_flow(FlowCreateRequest(name="未命名流程"))
    executor = RpaToolExecutor(service, SimpleNamespace())
    result = await executor.execute("update_flow", {"flow_id": draft.flow_id, "name": "更名草稿"})
    assert result["status"] == "applied"
    assert (await service.get_flow(draft.flow_id)).status == "draft"
    result = await executor.execute("update_flow", {
        "flow_id": draft.flow_id,
        "add_nodes": [{"id": "n1", "type": "variable.set", "variableName": "result", "value": "完成"}],
    })
    assert result["status"] == "applied"
    assert (await service.get_flow(draft.flow_id)).status == "draft"
    result = await executor.execute("set_acceptance_contract", {
        "flow_id": draft.flow_id, "acceptance_contract": generated_args()["acceptance_contract"],
    })
    assert result["status"] == "applied"
    assert (await service.get_flow(draft.flow_id)).status == "active"


async def test_generation_preserves_user_configured_draft_variables():
    service = FlowService()
    draft = await service.create_flow(FlowCreateRequest(
        name="未命名流程",
        input_variables=[{"name": "login", "value": "already-configured", "category": "flow", "type": "String", "scope": "全局"}],
    ))
    args = generated_args()
    args["input_variables"] = [{"name": "login", "value": "", "type": "String"}]
    args["nodes"][0]["value"] = "${var.login}"
    result = await RpaToolExecutor(service, SimpleNamespace()).execute(
        "create_flow", args, change_context=ChangeContext(draft_flow_id=draft.flow_id),
    )
    assert result["status"] == "active"
    assert not result.get("validation_issues")
    assert (await service.get_flow(draft.flow_id)).input_variables[0].value == "already-configured"


async def test_contract_can_be_added_to_saved_draft_without_recreating_nodes():
    service = FlowService()
    executor = RpaToolExecutor(service, SimpleNamespace(list_tasks=AsyncMock(return_value=[])))
    args = generated_args()
    contract = args.pop("acceptance_contract")
    created = await executor.execute("create_flow", args)
    assert created["status"] == "draft"
    flow_id = created["flow_id"]
    before = await service.get_flow(flow_id)
    from app.services.ai_flow_state import build_flow_state
    restored = await build_flow_state(executor, flow_id)
    assert restored.acceptance_contract_initialized is False
    blocked = await executor.execute("run_flow", {"flow_id": flow_id})
    assert blocked["status"] == "blocking_acceptance_contract"
    applied = await executor.execute("set_acceptance_contract", {
        "flow_id": flow_id, "acceptance_contract": contract,
    })
    assert applied["status"] == "applied"
    after = await service.get_flow(flow_id)
    assert after.status == "active"
    assert after.definition == before.definition
    assert after.revision > before.revision
    assert len(await service.list_flows()) == 1
    restored = await build_flow_state(executor, flow_id)
    assert restored.acceptance_contract_initialized is True


@pytest.mark.parametrize("status", ["paused", "disabled", "archived"])
async def test_setting_contract_preserves_non_draft_status(status):
    args = generated_args()
    service = FlowService()
    flow = await service.create_flow(FlowCreateRequest(
        name=args["name"], status=status, definition={"nodes": args["nodes"]},
    ))
    result = await RpaToolExecutor(service, SimpleNamespace()).execute("set_acceptance_contract", {
        "flow_id": flow.flow_id, "acceptance_contract": args["acceptance_contract"],
    })
    assert result["status"] == "applied"
    assert (await service.get_flow(flow.flow_id)).status == status


def test_initial_contract_still_requires_real_user_source_and_existing_contract_requires_change_quote():
    from app.services.ai_guards import _check_acceptance_contract_change, _check_acceptance_contract_sources
    contract = {"requirements": [{"id": "r", "description": "输出订单", "source_kind": "user", "source_quote": "输出订单"}]}
    state = GuardState(acceptance_contract_initialized=False, user_requirement_text="输出订单", latest_user_message="继续")
    args = {"acceptance_contract": contract}
    assert _check_acceptance_contract_change("set_acceptance_contract", args, state) is None
    assert _check_acceptance_contract_sources("set_acceptance_contract", args, state) is None
    state.user_requirement_text = "其他需求"
    assert _check_acceptance_contract_sources("set_acceptance_contract", args, state) is not None
    state.acceptance_contract_initialized = True
    assert _check_acceptance_contract_change("set_acceptance_contract", args, state) is not None


@pytest.mark.parametrize("field", ["confidence", "confirmed"])
@pytest.mark.parametrize("tool", ["create_flow", "set_acceptance_contract"])
def test_model_cannot_supply_confirmation_claims(field, tool):
    from app.services.ai_tools.schemas import validate_tool_arguments
    args = generated_args()
    args["acceptance_contract"]["requirements"][0][field] = True
    if tool == "set_acceptance_contract":
        args = {"flow_id": "draft", "acceptance_contract": args["acceptance_contract"]}
    result = validate_tool_arguments(tool, args)
    assert result["error"] == "invalid_arguments"
    assert any(field in issue.get("fields", []) for issue in result["issues"])
