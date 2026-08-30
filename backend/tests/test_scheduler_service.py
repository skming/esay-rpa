from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

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


def all_flows_definition() -> dict:
    return {
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
    }


def build_task_request() -> RunTaskRequest:
    return RunTaskRequest(
        flowName="调度测试流程",
        targetUrl="https://quotes.toscrape.com/",
        selector=".quote .text::text",
        timeoutMs=1000,
        # 调度载荷要么绑定 flow_id（触发时由 FlowRunner 现取流程定义、覆盖这里的 flowDefinition），
        # 要么自带定义。TaskManager 不再从遗留顶层字段拼临时节点执行。
        flowDefinition={
            "nodes": [
                {"id": "start", "type": "start"},
                {
                    "id": "fetch",
                    "type": "browser.fetch",
                    "targetUrl": "https://quotes.toscrape.com/",
                    "selector": ".quote .text::text",
                    "timeoutMs": 1000,
                },
            ],
            "edges": [{"source": "start", "target": "fetch"}],
        },
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


async def test_scheduler_loop_stop_survives_cancelled_worker() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    service = ScheduleService(task_manager=task_manager)
    try:
        loop = SchedulerLoop(schedule_service=service, interval_seconds=0.05)
        loop.start()
        await asyncio.sleep(0)
        assert loop._worker is not None
        loop._worker.cancel()

        # stop() 是 lifespan 关停链的第一步，抛出去后面的资源清理全不跑。
        await loop.stop()

        loop.start()
        assert loop._worker is not None
        await loop.stop()
    finally:
        await task_manager.stop_workers()


async def test_run_due_schedules_records_last_error_and_isolates_failures() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        broken = await service.create_schedule(
            ScheduleCreateRequest(
                name="绑定已删除流程",
                cronExpression="* * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": "missing-flow"}),
            )
        )
        flow = await flow_service.create_flow(
            FlowCreateRequest(
                name="可用流程",
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
        healthy = await service.create_schedule(
            ScheduleCreateRequest(
                name="正常调度",
                cronExpression="* * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": flow.flow_id}),
            )
        )

        now = datetime.now(UTC)
        for schedule in (broken, healthy):
            await service._store.save(schedule.model_copy(update={"next_run_at": now - timedelta(seconds=1)}))

        triggered = await service.run_due_schedules(now)

        assert [item.schedule_id for item in triggered] == [healthy.schedule_id]

        failed = await service.get_schedule(broken.schedule_id)
        assert failed is not None
        assert failed.last_error is not None and "missing-flow" in failed.last_error
        assert failed.next_run_at is not None and failed.next_run_at > now
        assert failed.last_task_id is None

        ok = await service.get_schedule(healthy.schedule_id)
        assert ok is not None
        assert ok.last_error is None
    finally:
        await task_manager.stop_workers()
async def test_trigger_schedule_clears_last_error() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    service = ScheduleService(task_manager=task_manager)
    try:
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="恢复后清空错误",
                cronExpression="* * * * *",
                timezone="UTC",
                task=build_task_request(),
            )
        )
        await service._store.save(schedule.model_copy(update={"last_error": "上一轮失败"}))

        triggered = await service.trigger_schedule(schedule.schedule_id)
        assert triggered is not None
        assert triggered.last_error is None
    finally:
        await task_manager.stop_workers()


async def test_run_due_schedules_stops_rescheduling_unparseable_cron() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    service = ScheduleService(task_manager=task_manager)
    try:
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="非法 cron",
                cronExpression="* * * * *",
                timezone="UTC",
                task=build_task_request(),
            )
        )
        now = datetime.now(UTC)
        # 绕开 store 写入非法 cron：schema 校验拦得住新建，但老库里存着已失效的表达式。
        await service._store.save(
            schedule.model_copy(update={"cron_expression": "99 99 * * *", "next_run_at": now - timedelta(seconds=1)})
        )

        assert await service.run_due_schedules(now) == []

        current = await service.get_schedule(schedule.schedule_id)
        assert current is not None
        assert current.next_run_at is None
        assert current.last_error is not None
        assert await service.due_schedules(now) == []
    finally:
        await task_manager.stop_workers()

async def test_update_schedule_clears_last_error() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    service = ScheduleService(task_manager=task_manager)
    schedule = await service.create_schedule(
        ScheduleCreateRequest(
            name="改完配置就该清错误",
            cronExpression="0 0 * * *",
            timezone="UTC",
            task=build_task_request(),
        )
    )
    await service._store.save(schedule.model_copy(update={"last_error": "上一轮失败", "next_run_at": None}))

    updated = await service.update_schedule(schedule.schedule_id, ScheduleUpdateRequest(cronExpression="30 9 * * *"))

    assert updated is not None
    assert updated.last_error is None
    assert updated.next_run_at is not None


async def test_manual_trigger_failure_records_last_error() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=FlowService(),
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="手动触发绑定已删除流程",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": "missing-flow"}),
            )
        )

        with pytest.raises(ValueError, match="missing-flow"):
            await service.trigger_schedule(schedule.schedule_id)

        current = await service.get_schedule(schedule.schedule_id)
        assert current is not None
        assert current.last_error is not None and "missing-flow" in current.last_error
        assert current.last_task_id is None
        assert current.next_run_at is not None
    finally:
        await task_manager.stop_workers()


async def test_disabled_schedule_failure_keeps_next_run_empty() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=FlowService(),
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="停用后手动触发",
                cronExpression="0 * * * *",
                timezone="UTC",
                enabled=False,
                task=build_task_request().model_copy(update={"flow_id": "missing-flow"}),
            )
        )

        with pytest.raises(ValueError, match="missing-flow"):
            await service.trigger_schedule(schedule.schedule_id)

        current = await service.get_schedule(schedule.schedule_id)
        assert current is not None
        # 给停用调度算出 next_run_at，界面上就成了「已停用但显示下次运行时间」。
        assert current.next_run_at is None
        assert current.last_error is not None
    finally:
        await task_manager.stop_workers()


async def test_all_flows_mode_reports_partially_failed_flows() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        await flow_service.create_flow(
            FlowCreateRequest(
                name="可用流程",
                version="v1.0.0",
                status="active",
                definition=all_flows_definition(),
                acceptanceContract=acceptance_contract("scheduled_rows"),
            )
        )
        # 缺验收契约的流程在 FlowRunService 里就抛，进不到 TaskManager。
        await flow_service.create_flow(
            FlowCreateRequest(
                name="缺契约流程",
                version="v1.0.0",
                status="active",
                definition=all_flows_definition(),
            )
        )
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="所有流程",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": None}),
            )
        )

        triggered = await service.trigger_schedule(schedule.schedule_id)

        assert triggered is not None
        assert triggered.last_task_id is not None
        assert triggered.last_error is not None
        assert "1/2" in triggered.last_error
        assert "缺契约流程" in triggered.last_error
        assert "验收契约" in triggered.last_error
    finally:
        await task_manager.stop_workers()


async def test_all_flows_mode_total_failure_names_the_reason() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        await flow_service.create_flow(
            FlowCreateRequest(name="缺契约流程", version="v1.0.0", status="active", definition=all_flows_definition())
        )
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="全部启动失败",
                cronExpression="* * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": None}),
            )
        )
        now = datetime.now(UTC)
        await service._store.save(schedule.model_copy(update={"next_run_at": now - timedelta(seconds=1)}))

        assert await service.run_due_schedules(now) == []

        current = await service.get_schedule(schedule.schedule_id)
        assert current is not None
        # 只写「所有流程均启动失败」等于把唯一的诊断线索留在日志里。
        assert current.last_error is not None and "验收契约" in current.last_error
        assert current.next_run_at is not None and current.next_run_at > now
    finally:
        await task_manager.stop_workers()
