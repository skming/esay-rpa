from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

import app.main as main_module
from app.models.schemas import FlowCreateRequest, FlowUpdateRequest, RunTaskRequest, RuntimeProgress, ScheduleSnapshot, TaskSnapshot
from app.services.flow_service import FlowService
from app.services.flow_store import FlowRow, SqlAlchemyFlowStore
from app.services.schedule_store import ScheduleRow, SqlAlchemyScheduleStore, create_schedule_engine
from app.services.task_store import SqlAlchemyTaskStore, TaskRow


def _contract() -> dict:
    requirement = {
        "id": "result-required",
        "description": "返回处理结果",
        "sourceKind": "product_default",
    }
    return {
        "requirements": [requirement],
        "deliverables": [{
            "id": "result",
            "variable": "result",
            "kind": "scalar",
            "requirementIds": ["result-required"],
        }],
    }


def _definition() -> dict:
    return {
        "nodes": [{"id": "result-node", "type": "script.python", "outputVariable": "result"}],
        "edges": [],
    }


def _task_request() -> RunTaskRequest:
    return RunTaskRequest(
        flowId="flow-current",
        flowName="当前契约任务",
        acceptanceContract=_contract(),
        timeoutMs=1000,
    )


def _task_snapshot() -> TaskSnapshot:
    now = datetime.now(UTC)
    return TaskSnapshot(
        taskId="task-current",
        flowId="flow-current",
        flowName="当前契约任务",
        status="queued",
        mode="run",
        progress=RuntimeProgress(currentStep=0, totalSteps=1, percent=0, elapsedMs=0),
        createdAt=now,
        updatedAt=now,
    )


async def test_current_contract_round_trips_through_all_stores(tmp_path) -> None:
    engine = create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'current.db'}")
    flow_store = SqlAlchemyFlowStore(engine)
    task_store = SqlAlchemyTaskStore(engine)
    schedule_store = SqlAlchemyScheduleStore(engine)
    await flow_store.create_schema()
    await task_store.create_schema()
    await schedule_store.create_schema()
    flow_service = FlowService(store=flow_store)

    created = await flow_service.create_flow(FlowCreateRequest(
        name="当前契约流程",
        definition=_definition(),
        acceptanceContract=_contract(),
    ))
    revised_definition = _definition()
    revised_definition["nodes"][0]["title"] = "生成版本快照"
    updated_flow = await flow_service.update_flow(created.flow_id, FlowUpdateRequest(definition=revised_definition))
    assert updated_flow is not None
    await task_store.save_task(_task_snapshot(), _task_request())
    now = datetime.now(UTC)
    await schedule_store.save(ScheduleSnapshot(
        scheduleId="schedule-current",
        name="当前契约调度",
        cronExpression="* * * * *",
        timezone="UTC",
        status="enabled",
        task=_task_request(),
        createdAt=now,
        updatedAt=now,
        nextRunAt=now + timedelta(minutes=1),
    ))

    current_snapshot = updated_flow.snapshots[0].model_dump(mode="json", by_alias=True)
    current_snapshot["acceptanceContract"] = _contract()
    current_task = _task_request().model_dump(mode="json", by_alias=True)
    current_task["acceptanceContract"] = _contract()
    async with engine.begin() as connection:
        await connection.execute(
            update(FlowRow).where(FlowRow.id == created.flow_id).values(
                acceptance_contract=_contract(), snapshots=[current_snapshot]
            )
        )
        await connection.execute(
            update(TaskRow).where(TaskRow.id == "task-current").values(request_payload=current_task)
        )
        await connection.execute(
            update(ScheduleRow).where(ScheduleRow.id == "schedule-current").values(task_payload=current_task)
        )

    restored_flow = await flow_store.get(created.flow_id)
    restored_task = await task_store.get_task("task-current")
    restored_schedule = await schedule_store.get("schedule-current")

    assert restored_flow is not None
    assert restored_flow.acceptance_contract.requirements[0].id == "result-required"
    assert restored_flow.snapshots[0].acceptance_contract.requirements[0].id == "result-required"
    assert restored_task is not None
    assert restored_task.acceptance_contract.requirements[0].id == "result-required"
    assert restored_schedule is not None
    assert restored_schedule.task.acceptance_contract.requirements[0].id == "result-required"
    await engine.dispose()


async def test_flow_and_task_list_apis_restore_current_contracts_from_isolated_database(tmp_path, monkeypatch) -> None:
    engine = create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'api-current.db'}")
    flow_store = SqlAlchemyFlowStore(engine)
    task_store = SqlAlchemyTaskStore(engine)
    await flow_store.create_schema()
    await task_store.create_schema()
    flow_service = FlowService(store=flow_store)
    flow = await flow_service.create_flow(FlowCreateRequest(
        name="当前契约 API 流程",
        definition=_definition(),
        acceptanceContract=_contract(),
    ))
    await task_store.save_task(_task_snapshot(), _task_request())

    current_task = _task_request().model_dump(mode="json", by_alias=True)
    current_task["acceptanceContract"] = _contract()
    async with engine.begin() as connection:
        await connection.execute(
            update(FlowRow).where(FlowRow.id == flow.flow_id).values(acceptance_contract=_contract())
        )
        await connection.execute(
            update(TaskRow).where(TaskRow.id == "task-current").values(request_payload=current_task)
        )

    class IsolatedTaskManager:
        async def flow_success_rates_30d(self) -> dict[str, int]:
            return {}

        async def list_tasks(self, *, flow_id=None, schedule_id=None, limit=50):
            return await task_store.list_tasks(flow_id=flow_id, schedule_id=schedule_id, limit=limit)

    monkeypatch.setattr(main_module, "flow_service", flow_service)
    monkeypatch.setattr(main_module, "task_manager", IsolatedTaskManager())
    async with AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://testserver") as client:
        flows_response = await client.get("/api/flows")
        tasks_response = await client.get("/api/tasks")

    assert flows_response.status_code == 200
    assert tasks_response.status_code == 200
    for response in (flows_response, tasks_response):
        requirement = response.json()[0]["acceptanceContract"]["requirements"][0]
        assert requirement["id"] == "result-required"
        assert "confidence" not in requirement
        assert "confirmed" not in requirement
    await engine.dispose()
