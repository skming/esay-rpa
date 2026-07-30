from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.models.schemas import FlowCreateRequest, RunTaskRequest, ScheduleCreateRequest, ScheduleUpdateRequest
from app.services.flow_runner import FlowRunService
from app.services.flow_service import FlowService
from app.services.log_broker import LogBroker
from app.services.scheduler_service import SchedulerLoop, ScheduleService
from app.services.task_manager import TaskManager
from tests.test_task_manager import FakeRunner


def acceptance_contract(variable: str) -> dict:
    return {
        "requirements": [{
            "id": "scheduled-output",
            "description": "调度测试交付",
            "sourceKind": "product_default",
            "confidence": 1,
            "confirmed": True,
        }],
        "deliverables": [{
            "id": "scheduled-result",
            "variable": variable,
            "kind": "table",
            "requirementIds": ["scheduled-output"],
        }],
    }


def build_task_request() -> RunTaskRequest:
    return RunTaskRequest(
        flowName="调度测试流程",
        targetUrl="https://quotes.toscrape.com/",
        selector=".quote .text::text",
        timeoutMs=1000,
    )


async def test_schedule_service_create_update_and_trigger() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    service = ScheduleService(task_manager=task_manager)
    try:
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="每小时采集",
                cronExpression="0 * * * *",
                timezone="Asia/Shanghai",
                task=build_task_request(),
            )
        )

        assert schedule.status == "enabled"
        assert schedule.next_run_at is not None
        assert schedule.next_run_at.tzinfo is not None

        updated = await service.update_schedule(
            schedule.schedule_id,
            ScheduleUpdateRequest(enabled=False),
        )
        assert updated is not None
        assert updated.status == "disabled"
        assert updated.next_run_at is None

        triggered = await service.trigger_schedule(schedule.schedule_id)
        assert triggered is not None
        assert triggered.last_task_id is not None
        assert await task_manager.get_task(triggered.last_task_id) is not None
    finally:
        await task_manager.stop_workers()


async def test_schedule_trigger_runs_bound_flow_definition() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    flow_run_service = FlowRunService(task_manager=task_manager)
    service = ScheduleService(task_manager=task_manager, flow_service=flow_service, flow_run_service=flow_run_service)
    try:
        flow = await flow_service.create_flow(
            FlowCreateRequest(
                name="流程定义调度",
                version="v1.0.0",
                status="active",
                definition={
                    "nodes": [
                        {"id": "start", "type": "start"},
                        {
                            "id": "fetch",
                            "type": "browser.fetch",
                            "targetUrl": "https://quotes.toscrape.com/",
                            "selector": ".quote .author::text",
                            "fetcher": "static",
                            "extractMode": "text",
                            "outputVariable": "scheduled_rows",
                            "timeoutMs": 1000,
                        },
                    ],
                    "edges": [{"source": "start", "target": "fetch"}],
                },
                acceptanceContract=acceptance_contract("scheduled_rows"),
            )
        )
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="按流程定义采集",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": flow.flow_id, "selector": ".wrong::text"}),
            )
        )

        triggered = await service.trigger_schedule(schedule.schedule_id)

        assert triggered is not None
        assert triggered.last_task_id is not None
        task = await task_manager.get_task(triggered.last_task_id)
        assert task is not None
        assert task.flow_id == flow.flow_id
        for _ in range(20):
            task = await task_manager.get_task(triggered.last_task_id)
            assert task is not None
            if task.status == "success":
                break
            await asyncio.sleep(0.01)
        task = await task_manager.get_task(triggered.last_task_id)
        assert task is not None
        assert task.result is not None
        assert task.result.selector == ".quote .author::text"
        assert task.run_config.scope == "full"
    finally:
        await task_manager.stop_workers()


async def test_schedule_trigger_preserves_run_config_for_bound_flow() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    flow_run_service = FlowRunService(task_manager=task_manager)
    service = ScheduleService(task_manager=task_manager, flow_service=flow_service, flow_run_service=flow_run_service)
    try:
        flow = await flow_service.create_flow(
            FlowCreateRequest(
                name="带配置调度流程",
                version="v1.0.0",
                status="active",
                definition={
                    "nodes": [
                        {"id": "start", "type": "start"},
                        {
                            "id": "fetch",
                            "type": "browser.fetch",
                            "targetUrl": "https://quotes.toscrape.com/",
                            "selector": ".quote .text::text",
                            "outputVariable": "scheduled_rows",
                            "timeoutMs": 1000,
                        },
                    ],
                    "edges": [{"source": "start", "target": "fetch"}],
                },
                inputVariables=[{"name": "retry_count", "type": "Integer", "scope": "全局", "value": "1"}],
                acceptanceContract=acceptance_contract("scheduled_rows"),
            )
        )
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="带运行配置调度",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=RunTaskRequest(
                    flowId=flow.flow_id,
                    flowName=flow.name,
                    targetUrl="https://example.com/fallback",
                    selector=".fallback::text",
                    scope="from-selection",
                    startNodeId="fetch",
                    failureStrategy="retry",
                    screenshot=False,
                    concurrency=4,
                    timeoutMs=12_000,
                    variables={"retry_count": 5, "batch_id": "B-01"},
                ),
            )
        )

        triggered = await service.trigger_schedule(schedule.schedule_id)

        assert triggered is not None
        assert triggered.last_task_id is not None
        task = await task_manager.get_task(triggered.last_task_id)
        assert task is not None
        assert task.run_config.scope == "from-selection"
        assert task.run_config.start_node_id == "fetch"
        assert task.run_config.failure_strategy == "retry"
        assert task.run_config.screenshot is False
        assert task.run_config.concurrency == 4
        assert {variable.name: variable.value for variable in task.variables}["retry_count"] == "5"
        assert {variable.name: variable.value for variable in task.variables}["batch_id"] == "B-01"
    finally:
        await task_manager.stop_workers()


async def test_due_schedules_filters_enabled_items() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    service = ScheduleService(task_manager=task_manager)
    schedule = await service.create_schedule(
        ScheduleCreateRequest(
            name="每分钟采集",
            cronExpression="* * * * *",
            timezone="UTC",
            task=build_task_request(),
        )
    )

    due_items = await service.due_schedules(datetime(2099, 1, 1, tzinfo=UTC))
    assert [item.schedule_id for item in due_items] == [schedule.schedule_id]


async def test_scheduler_loop_triggers_due_schedule() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    service = ScheduleService(task_manager=task_manager)
    try:
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="自动触发采集",
                cronExpression="* * * * *",
                timezone="UTC",
                task=build_task_request(),
            )
        )
        assert await service.update_schedule(schedule.schedule_id, ScheduleUpdateRequest(enabled=True)) is not None
        store = service._store
        current = await store.get(schedule.schedule_id)
        assert current is not None
        await store.save(current.model_copy(update={"next_run_at": datetime.now(UTC) - timedelta(seconds=1)}))

        loop = SchedulerLoop(schedule_service=service, interval_seconds=0.05)
        loop.start()
        try:
            for _ in range(20):
                current = await service.get_schedule(schedule.schedule_id)
                assert current is not None
                if current.last_task_id is not None:
                    break
                await asyncio.sleep(0.05)
        finally:
            await loop.stop()

        current = await service.get_schedule(schedule.schedule_id)
        assert current is not None
        assert current.last_task_id is not None
        assert await task_manager.get_task(current.last_task_id) is not None
    finally:
        await task_manager.stop_workers()
