from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.models.schemas import FlowCreateRequest, FlowUpdateRequest
from app.services.flow_service import FlowService
from app.services.flow_store import InMemoryFlowStore, SqlAlchemyFlowStore
from app.services.schedule_store import create_schedule_engine


def build_definition() -> dict[str, object]:
    return {
        "nodes": [{"id": "start", "type": "start"}, {"id": "n1", "type": "browser.fetch", "outputVariable": "orders"}],
        "edges": [{"source": "start", "target": "n1"}],
    }


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_run_completion_cannot_overwrite_concurrent_flow_edits(tmp_path, monkeypatch, backend):
    store = InMemoryFlowStore() if backend == "memory" else SqlAlchemyFlowStore(create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}"))
    if backend == "sqlite":
        await store.create_schema()
    service = FlowService(store)
    flow = await service.create_flow(FlowCreateRequest(name="original", definition=build_definition()))
    entered, release = asyncio.Event(), asyncio.Event()
    save = store.save

    async def paused_save(snapshot):
        if snapshot.last_run_status == "success" and not entered.is_set():
            entered.set()
            await release.wait()
        return await save(snapshot)

    monkeypatch.setattr(store, "save", paused_save)
    completion = asyncio.create_task(service.update_last_run(flow.flow_id, "success", datetime.now(UTC)))
    editing = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        editing = asyncio.create_task(service.update_flow(flow.flow_id, FlowUpdateRequest(name="edited", definition={"nodes": [], "edges": []})))
        await asyncio.sleep(0.05)
        release.set()
        await asyncio.gather(completion, editing)
        restored = await service.get_flow(flow.flow_id)
        assert restored.name == "edited"
        assert restored.definition == {"nodes": [], "edges": []}
        assert restored.revision == 2
        assert restored.last_run_status == "success"
    finally:
        release.set()
        await asyncio.gather(*(task for task in (completion, editing) if task), return_exceptions=True)
        if backend == "sqlite":
            await store.close()


async def test_delete_cannot_be_undone_by_an_in_flight_save(monkeypatch):
    service = FlowService()
    flow = await service.create_flow(FlowCreateRequest(name="original", definition=build_definition()))
    save = service._store.save
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused_save(snapshot):
        entered.set()
        await release.wait()
        return await save(snapshot)

    monkeypatch.setattr(service._store, "save", paused_save)
    editing = asyncio.create_task(service.update_flow(flow.flow_id, FlowUpdateRequest(name="edited")))
    deleting = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        deleting = asyncio.create_task(service.delete_flow(flow.flow_id))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(editing, deleting)
        assert await service.get_flow(flow.flow_id) is None
    finally:
        release.set()
        await asyncio.gather(*(task for task in (editing, deleting) if task), return_exceptions=True)


async def test_outdated_run_notification_preserves_newer_status_and_edit_time():
    from datetime import timedelta
    service = FlowService()
    flow = await service.create_flow(FlowCreateRequest(name="flow", definition=build_definition()))
    earlier = flow.created_at - timedelta(seconds=1)
    await service.update_last_run(flow.flow_id, "error", earlier)
    restored = await service.get_flow(flow.flow_id)
    assert restored.updated_at == flow.created_at
    await service.update_last_run(flow.flow_id, "success", flow.created_at)
    await service.update_last_run(flow.flow_id, "error", earlier)
    restored = await service.get_flow(flow.flow_id)
    assert restored.last_run_status == "success"
    assert restored.last_run_at == flow.created_at


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
            acceptanceContract={
                "requirements": [{
                    "id": "orders-required",
                    "description": "测试订单交付",
                    "sourceKind": "product_default",


                }],
                "deliverables": [{
                    "id": "orders",
                    "variable": "orders",
                    "kind": "table",
                    "minRows": 1,
                    "requirementIds": ["orders-required"],
                }],
            },
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
    assert listed[0].revision == 1
    assert listed[0].acceptance_contract.deliverables[0].variable == "orders"

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
            acceptanceContract={
                "requirements": [{
                    "id": "row-count-required",
                    "description": "测试计数交付",
                    "sourceKind": "product_default",


                }],
                "deliverables": [{
                    "id": "row-count",
                    "variable": "row_count",
                    "kind": "scalar",
                    "requirementIds": ["row-count-required"],
                }],
            },
        ),
    )
    assert updated is not None
    assert updated.version == "v1.1.0"
    assert updated.revision == 2
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
    assert reloaded.revision == 2
    assert reloaded.snapshots[0].revision == 1
    assert reloaded.snapshots[0].input_variables[1].name == "erp_password"
    assert reloaded.snapshots[0].input_variables[1].value == ""
    assert reloaded.acceptance_contract.deliverables[0].id == "row-count"
    versions = await service.list_versions(created.flow_id)
    assert versions is not None
    assert [snapshot.revision for snapshot in versions] == [1]
    assert await service.list_versions("missing-flow") is None

    archived = await service.archive_flow(created.flow_id)
    assert archived is not None
    assert archived.status == "archived"

    deleted = await service.delete_flow(created.flow_id)
    assert deleted is True
    assert await service.get_flow(created.flow_id) is None

    await store.close()


async def test_sqlite_roundtrip_returns_timezone_aware_timestamps(tmp_path) -> None:
    engine = create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'flows.db'}")
    store = SqlAlchemyFlowStore(engine)
    await store.create_schema()
    service = FlowService(store=store)

    created = await service.create_flow(
        FlowCreateRequest(name="时区流程", version="v1.0.0", definition=build_definition(), status="draft")
    )

    reloaded = await service.get_flow(created.flow_id)
    assert reloaded is not None
    # SQLite 不保存时区，UTCDateTime 负责在读回时补齐 UTC。
    assert reloaded.created_at.tzinfo is not None
    assert reloaded.updated_at.tzinfo is not None
    assert reloaded.created_at == created.created_at

    await store.close()


async def test_update_flow_rejects_clearing_existing_acceptance_contract(tmp_path) -> None:
    """空契约恰好能绕过契约校验，允许它覆盖已有契约就等于静默清空，直到运行时才被拒绝。"""
    engine = create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'flows.db'}")
    store = SqlAlchemyFlowStore(engine)
    await store.create_schema()
    service = FlowService(store=store)

    created = await service.create_flow(
        FlowCreateRequest(
            name="契约流程",
            version="v1.0.0",
            definition=build_definition(),
            acceptanceContract={
                "requirements": [{
                    "id": "orders-required",
                    "description": "测试订单交付",
                    "sourceKind": "product_default",


                }],
                "deliverables": [{
                    "id": "orders",
                    "variable": "orders",
                    "kind": "table",
                    "requirementIds": ["orders-required"],
                }],
            },
            status="draft",
        )
    )

    with pytest.raises(ValueError, match="空验收契约"):
        await service.update_flow(created.flow_id, FlowUpdateRequest(acceptanceContract={}))

    reloaded = await service.get_flow(created.flow_id)
    assert reloaded is not None
    assert reloaded.acceptance_contract.deliverables[0].variable == "orders"
    assert reloaded.revision == created.revision

    await store.close()
