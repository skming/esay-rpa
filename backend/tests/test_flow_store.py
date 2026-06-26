from __future__ import annotations

from app.models.schemas import FlowCreateRequest, FlowUpdateRequest
from app.services.flow_service import FlowService
from app.services.flow_store import SqlAlchemyFlowStore
from app.services.schedule_store import create_schedule_engine


def build_definition() -> dict[str, object]:
    return {
        "nodes": [{"id": "start", "type": "start"}, {"id": "n1", "type": "browser.fetch"}],
        "edges": [{"source": "start", "target": "n1"}],
    }


async def test_flow_service_persists_crud_with_sqlalchemy(tmp_path) -> None:
    engine = create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'flows.db'}")
    store = SqlAlchemyFlowStore(engine)
    await store.create_schema()
    service = FlowService(store=store)

    created = await service.create_flow(
        FlowCreateRequest(
            name="订单流程",
            version="v1.0.0",
            description="初始版本",
            definition=build_definition(),
            inputVariables=[
                {"category": "flow", "name": "username", "sensitive": False, "type": "String", "scope": "全局", "value": "zhang.san"},
                {"category": "credential", "name": "erp_password", "sensitive": True, "type": "String", "scope": "全局", "value": "secret-pass"},
            ],
            status="draft",
        )
    )

    listed = await service.list_flows()
    assert [item.flow_id for item in listed] == [created.flow_id]
    assert listed[0].definition["nodes"][1]["type"] == "browser.fetch"
    assert listed[0].input_variables[0].name == "username"
    assert listed[0].input_variables[0].category == "flow"
    assert listed[0].input_variables[0].sensitive is False
    assert listed[0].input_variables[1].name == "erp_password"
    assert listed[0].input_variables[1].category == "credential"
    assert listed[0].input_variables[1].sensitive is True

    updated = await service.update_flow(
        created.flow_id,
        FlowUpdateRequest(
            version="v1.1.0",
            status="active",
            definition={"nodes": [], "edges": []},
            inputVariables=[
                {"category": "environment", "name": "run_scope", "sensitive": False, "type": "String", "scope": "全局", "value": "staging"},
                {"category": "flow", "name": "row_count", "sensitive": False, "type": "Integer", "scope": "全局", "value": "5"},
            ],
        ),
    )
    assert updated is not None
    assert updated.version == "v1.1.0"
    assert updated.status == "active"
    assert updated.input_variables[0].name == "run_scope"
    assert updated.input_variables[0].category == "environment"
    assert updated.input_variables[0].sensitive is False
    assert updated.input_variables[1].name == "row_count"
    assert updated.input_variables[1].category == "flow"
    assert updated.updated_at >= created.updated_at

    reloaded = await service.get_flow(created.flow_id)
    assert reloaded is not None
    assert [item.category for item in reloaded.input_variables] == ["environment", "flow"]
    assert [item.sensitive for item in reloaded.input_variables] == [False, False]

    archived = await service.archive_flow(created.flow_id)
    assert archived is not None
    assert archived.status == "archived"

    deleted = await service.delete_flow(created.flow_id)
    assert deleted is True
    assert await service.get_flow(created.flow_id) is None

    await store.close()
