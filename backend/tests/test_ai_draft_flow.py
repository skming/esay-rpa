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
            "requirements": [{"id": "r1", "description": "生成结果", "source_kind": "product_default", "confidence": 1, "confirmed": True}],
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


async def test_update_flow_activates_draft_only_when_business_nodes_written():
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
