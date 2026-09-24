from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.models.schemas import FlowCreateRequest, FlowUpdateRequest, RunTaskRequest, ScheduleCreateRequest, ScheduleTaskRequest, ScheduleUpdateRequest
from app.services import browser_profile_lock
from app.services.browser_action_runner import BrowserActionResult, BrowserActionRunner
from app.services.flow_runner import FlowRunService
from app.services.flow_service import FlowService
from app.services.log_broker import LogBroker
from app.services.scheduler_service import SchedulerLoop, ScheduleService
from app.services.task_manager import TaskManager
from tests.test_task_manager import AlwaysFailingRunner, FakeRunner, SlowRunner, wait_for_status


def _active_flow_request(name: str, *, status: str = "active", nodes: list[dict] | None = None) -> FlowCreateRequest:
    definition = {"nodes": nodes, "edges": []} if nodes is not None else all_flows_definition()
    return FlowCreateRequest(
        name=name,
        version="v1.0.0",
        status=status,
        definition=definition,
        acceptanceContract=acceptance_contract("scheduled_rows"),
    )


def acceptance_contract(variable: str) -> dict:
    return {
        "requirements": [{
            "id": "scheduled-output",
            "description": "调度测试交付",
            "sourceKind": "product_default",


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


def build_task_request() -> ScheduleTaskRequest:
    return ScheduleTaskRequest(
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


class RecordingTaskManager(TaskManager):
    def __init__(self) -> None:
        super().__init__(runner=FakeRunner(), broker=LogBroker())
        self.started_requests: list[RunTaskRequest] = []

    async def start_task(self, request: RunTaskRequest):
        self.started_requests.append(request)
        return await super().start_task(request)


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


async def test_schedule_uses_current_bound_flow_executor() -> None:
    task_manager = RecordingTaskManager()
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        flow = await flow_service.create_flow(
            _active_flow_request("按流程切换执行器").model_copy(update={"default_browser_executor": "extension"})
        )
        schedule = await service.create_schedule(ScheduleCreateRequest(
            name="跟随流程配置",
            cronExpression="0 * * * *",
            timezone="UTC",
            task=build_task_request().model_copy(update={"flow_id": flow.flow_id, "browser_executor": "playwright"}),
        ))

        first = await service.trigger_schedule(schedule.schedule_id)
        assert first is not None and first.last_task_id is not None
        assert task_manager.started_requests[-1].browser_executor == "extension"
        await wait_for_status(task_manager, first.last_task_id, {"error"})

        await flow_service.update_flow(flow.flow_id, FlowUpdateRequest(defaultBrowserExecutor="playwright"))
        second = await service.trigger_schedule(schedule.schedule_id)
        assert second is not None and second.last_task_id is not None
        assert task_manager.started_requests[-1].browser_executor == "playwright"
    finally:
        await task_manager.stop_workers()


async def test_schedule_runs_bound_flow_without_acceptance_contract() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        flow = await flow_service.create_flow(FlowCreateRequest(
            name="手工创建的流程",
            status="active",
            definition=all_flows_definition(),
        ))
        schedule = await service.create_schedule(ScheduleCreateRequest(
            name="运行无契约流程",
            cronExpression="0 * * * *",
            timezone="UTC",
            task=build_task_request().model_copy(update={"flow_id": flow.flow_id}),
        ))

        triggered = await service.trigger_schedule(schedule.schedule_id)
        assert triggered is not None and triggered.last_task_id is not None
        task = await wait_for_status(task_manager, triggered.last_task_id, {"success"})
        assert task.result is not None
        assert not task.acceptance_contract.requirements
        assert not task.acceptance_contract.deliverables
    finally:
        await task_manager.stop_workers()


async def test_schedule_rejects_incomplete_nonempty_acceptance_contract() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        flow = await flow_service.create_flow(_active_flow_request("契约残缺流程"))
        schedule = await service.create_schedule(ScheduleCreateRequest(
            name="检查非空契约",
            cronExpression="0 * * * *",
            timezone="UTC",
            task=build_task_request().model_copy(update={"flow_id": flow.flow_id}),
        ))
        flow.acceptance_contract = flow.acceptance_contract.model_copy(update={"deliverables": []})

        with pytest.raises(ValueError, match="至少需要一个 deliverable"):
            await service.trigger_schedule(schedule.schedule_id)
    finally:
        await task_manager.stop_workers()


async def test_all_flows_schedule_uses_each_flow_executor() -> None:
    task_manager = RecordingTaskManager()
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        await flow_service.create_flow(
            _active_flow_request("插件流程").model_copy(update={"default_browser_executor": "extension"})
        )
        await flow_service.create_flow(FlowCreateRequest(
            name="Playwright 流程",
            status="active",
            definition=all_flows_definition(),
        ))
        schedule = await service.create_schedule(ScheduleCreateRequest(
            name="混合执行器",
            cronExpression="0 * * * *",
            timezone="UTC",
            task=build_task_request().model_copy(update={"flow_id": None, "browser_executor": "playwright"}),
        ))

        triggered = await service.trigger_schedule(schedule.schedule_id)
        assert triggered is not None
        assert triggered.last_error is None
        assert {request.flow_name: request.browser_executor for request in task_manager.started_requests} == {
            "插件流程": "extension",
            "Playwright 流程": "playwright",
        }
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
                task=ScheduleTaskRequest(
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


async def test_selected_flows_schedule_starts_only_selected_flows() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        first = await flow_service.create_flow(_active_flow_request("选中一"))
        second = await flow_service.create_flow(_active_flow_request("选中二"))
        unselected = await flow_service.create_flow(_active_flow_request("未选中"))
        schedule = await service.create_schedule(ScheduleCreateRequest(
            name="指定流程",
            cronExpression="0 * * * *",
            timezone="UTC",
            task=build_task_request().model_copy(update={"flow_ids": [first.flow_id, second.flow_id]}),
        ))

        triggered = await service.trigger_schedule(schedule.schedule_id)

        assert triggered is not None
        tasks = await task_manager.list_tasks(schedule_id=schedule.schedule_id, limit=10)
        assert {task.flow_id for task in tasks} == {first.flow_id, second.flow_id}
        assert unselected.flow_id not in {task.flow_id for task in tasks}
        assert triggered.last_error is None
    finally:
        await task_manager.stop_workers()


class _StubPage:
    async def screenshot(self, **kwargs: object) -> bytes:
        return b"\x89PNG\r\n\x1a\nfake"


class _StubPersistentContext:
    async def new_page(self) -> _StubPage:
        return _StubPage()


async def _stub_open_persistent_context(profile_dir: str, *, headless: bool):
    async def _closer() -> None:
        return None

    return _StubPersistentContext(), _closer


class _SerializingBrowserActionRunner(BrowserActionRunner):
    """跑真实的 create_context/close_context（含新加的 acquire_exclusive/release_exclusive），只把
    Playwright 拉起换成假上下文，并在独占窗口里留一段重叠。没串行时同批两个流程会同时进入 run——
    counter 越过 1，或第二个 create_context 在打开浏览器前就撞上被占用直接报错。"""

    def __init__(self, session_dir: str, *, overlap: float, counter: dict[str, int]) -> None:
        super().__init__(session_dir=session_dir)
        self._overlap = overlap
        self._counter = counter

    async def run(self, node, variables, context, *, timeout_ms: int):
        self._counter["current"] += 1
        self._counter["max"] = max(self._counter["max"], self._counter["current"])
        try:
            await asyncio.sleep(self._overlap)
        finally:
            self._counter["current"] -= 1
        return BrowserActionResult(action_type=str(node["type"]), detail=str(node.get("selector", "")), values=[str(node["type"])])

    async def screenshot(self, context) -> bytes:
        return b"\x89PNG\r\n\x1a\nfake"


def _browser_action_flow_request(name: str) -> FlowCreateRequest:
    return FlowCreateRequest(
        name=name,
        version="v1.0.0",
        status="active",
        definition={
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "act", "type": "browser.click", "selector": "#go"},
            ],
            "edges": [{"source": "start", "target": "act"}],
        },
    )


async def test_batch_flows_sharing_the_browser_profile_all_succeed(tmp_path, monkeypatch) -> None:
    """同一调度批次里两个都要开浏览器的流程共用同一 user-data-dir：修复后它们排队执行、双双 success，
    而不是第二个在打开浏览器前撞上被占用秒失败。旧测试只断言「两个任务已创建」，正好漏掉这条真实缺陷。"""
    monkeypatch.setattr("app.services.browser_action_runner.open_persistent_context", _stub_open_persistent_context)
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    counter = {"current": 0, "max": 0}
    # 换成跑真实锁逻辑的执行器，共用同一 session_dir → 两个运行争用同一把目录锁
    task_manager._browser_action_runner = _SerializingBrowserActionRunner(  # type: ignore[attr-defined]  # noqa: SLF001
        str(tmp_path / "profile"), overlap=0.05, counter=counter
    )
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        first = await flow_service.create_flow(_browser_action_flow_request("批次一"))
        second = await flow_service.create_flow(_browser_action_flow_request("批次二"))
        schedule = await service.create_schedule(ScheduleCreateRequest(
            name="同批多流程",
            cronExpression="0 * * * *",
            timezone="UTC",
            task=build_task_request().model_copy(update={"flow_ids": [first.flow_id, second.flow_id], "screenshot": False}),
        ))

        triggered = await service.trigger_schedule(schedule.schedule_id)
        assert triggered is not None

        tasks = await task_manager.list_tasks(schedule_id=schedule.schedule_id, limit=10)
        by_flow = {task.flow_id: task.task_id for task in tasks}
        assert set(by_flow) == {first.flow_id, second.flow_id}

        # 等到终态再判：两个需浏览器的流程都要 success，而非一个 success 一个撞占用 error
        for task_id in by_flow.values():
            done = await wait_for_status(task_manager, task_id, {"success", "error", "stopped"})
            assert done.status == "success", f"{task_id} 终态为 {done.status}，同批浏览器争用未被串行化"

        # 独占窗口从不重叠：串行成立时同时进入 run 的运行数恒为 1；收尾干净、目录不再登记占用
        assert counter["max"] == 1
        assert browser_profile_lock.holder(str(tmp_path / "profile")) is None
    finally:
        await task_manager.stop_workers()


async def test_selected_flows_schedule_records_deleted_flow_failure() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        kept = await flow_service.create_flow(_active_flow_request("保留流程"))
        removed = await flow_service.create_flow(_active_flow_request("删除流程"))
        schedule = await service.create_schedule(ScheduleCreateRequest(
            name="指定流程",
            cronExpression="0 * * * *",
            timezone="UTC",
            task=build_task_request().model_copy(update={"flow_ids": [kept.flow_id, removed.flow_id]}),
        ))
        await flow_service.delete_flow(removed.flow_id)

        triggered = await service.trigger_schedule(schedule.schedule_id)

        assert triggered is not None
        assert triggered.last_error is not None and "1/2" in triggered.last_error
        tasks = await task_manager.list_tasks(schedule_id=schedule.schedule_id, limit=10)
        assert {task.flow_id for task in tasks} == {kept.flow_id, removed.flow_id}
        assert any(task.flow_id == removed.flow_id and task.status == "error" for task in tasks)
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
        removed_flow = await flow_service.create_flow(_active_flow_request("即将删除的流程"))
        broken = await service.create_schedule(
            ScheduleCreateRequest(
                name="绑定已删除流程",
                cronExpression="* * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": removed_flow.flow_id}),
            )
        )
        await flow_service.delete_flow(removed_flow.flow_id)
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
        assert failed.last_error is not None and removed_flow.flow_id in failed.last_error
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
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        removed_flow = await flow_service.create_flow(_active_flow_request("即将删除的流程"))
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="手动触发绑定已删除流程",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": removed_flow.flow_id}),
            )
        )
        await flow_service.delete_flow(removed_flow.flow_id)

        with pytest.raises(ValueError, match=removed_flow.flow_id):
            await service.trigger_schedule(schedule.schedule_id)

        current = await service.get_schedule(schedule.schedule_id)
        assert current is not None
        assert current.last_error is not None and removed_flow.flow_id in current.last_error
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
        # 没有可执行节点的流程在 FlowRunService 里就抛，进不到 TaskManager。
        await flow_service.create_flow(
            FlowCreateRequest(
                name="无可执行节点流程",
                version="v1.0.0",
                status="active",
                definition={"nodes": [{"id": "start", "type": "start"}], "edges": []},
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
        assert "无可执行节点流程" in triggered.last_error
        assert "缺少可执行节点" in triggered.last_error

        summary = None
        for _ in range(50):
            summary = await service.get_run_summary(triggered)
            if summary is not None and summary.running == 0:
                break
            await asyncio.sleep(0.01)
        assert summary is not None
        assert summary.total == 2
        assert summary.success == 1
        assert summary.failed == 1
        assert summary.status == "partial"
        failed_tasks = [await task_manager.get_task(task_id) for task_id in summary.task_ids]
        assert any(task is not None and task.flow_name == "无可执行节点流程" and "缺少可执行节点" in (task.error or "") for task in failed_tasks)
    finally:
        await task_manager.stop_workers()


async def test_all_flows_mode_logs_repeated_partial_failure_once(caplog) -> None:
    """同一批流程永远起不来，秒级 cron 下每个 tick 打一行会把日志淹掉；last_error 已经承载了当前状态。"""
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
        await flow_service.create_flow(
            FlowCreateRequest(
                name="无可执行节点流程", version="v1.0.0", status="active",
                definition={"nodes": [{"id": "start", "type": "start"}], "edges": []},
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

        with caplog.at_level(logging.WARNING, logger="app.services.scheduler_service"):
            first = await service.trigger_schedule(schedule.schedule_id)
            second = await service.trigger_schedule(schedule.schedule_id)

        assert first is not None and second is not None
        assert second.last_error == first.last_error
        assert "无可执行节点流程" in (second.last_error or "")
        repeated = [r for r in caplog.records if "所有流程调度中部分流程启动失败" in r.getMessage()]
        assert len(repeated) == 1
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
            FlowCreateRequest(
                name="无可执行节点流程", version="v1.0.0", status="active",
                definition={"nodes": [{"id": "start", "type": "start"}], "edges": []},
            )
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
        assert current.last_error is not None and "缺少可执行节点" in current.last_error
        assert current.next_run_at is not None and current.next_run_at > now
    finally:
        await task_manager.stop_workers()


async def test_all_flows_mode_excludes_draft_flows() -> None:
    """草稿是真实独立状态，绝不能混进「所有流程」批次。草稿也配好契约，以证明它是被资格过滤挡下、
    而非缺契约在启动阶段失败——后者会掩盖真正要验的「选流程时就排除草稿」这一行为。"""
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        active = await flow_service.create_flow(_active_flow_request("上线流程"))
        draft = await flow_service.create_flow(_active_flow_request("草稿流程", status="draft"))
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
        # 草稿被过滤不算失败：last_error 只承载真正的启动失败。
        assert triggered.last_error is None
        flow_ids = {t.flow_id for t in await task_manager.list_tasks(limit=200)}
        assert active.flow_id in flow_ids
        assert draft.flow_id not in flow_ids
    finally:
        await task_manager.stop_workers()


async def test_scheduled_task_is_tagged_with_schedule_id() -> None:
    """每个被调度启动的 task 必须带 schedule_id：批次终态要靠它聚合，非重叠策略也要靠它判定。
    走 FlowRunService 的绑定流程/所有流程路径此前会在重建 RunTaskRequest 时把 schedule_id 丢掉。"""
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        flow = await flow_service.create_flow(_active_flow_request("绑定流程"))
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="绑定调度",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": flow.flow_id}),
            )
        )

        triggered = await service.trigger_schedule(schedule.schedule_id)

        assert triggered is not None and triggered.last_task_id is not None
        task = await task_manager.get_task(triggered.last_task_id)
        assert task is not None
        assert task.schedule_id == schedule.schedule_id
    finally:
        await task_manager.stop_workers()


def _confirmation_definition() -> dict:
    """带 requireConfirmation 敏感动作的定义：fetch 节点满足契约变量，confirm 节点触发无人值守门控。"""
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
            {"id": "confirm", "type": "browser.click", "selector": ".buy", "requireConfirmation": True},
        ],
        "edges": [
            {"source": "start", "target": "fetch"},
            {"source": "fetch", "target": "confirm"},
        ],
    }

async def test_manual_trigger_blocks_on_overlapping_batch() -> None:
    """上一批未结束时手动触发必须显式报错，而不是再起一批去抢同一浏览器 profile。"""
    runner = SlowRunner()
    task_manager = TaskManager(runner=runner, broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        flow = await flow_service.create_flow(_active_flow_request("慢流程"))
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="重叠触发",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": flow.flow_id}),
            )
        )

        first = await service.trigger_schedule(schedule.schedule_id)
        assert first is not None and first.last_task_id is not None

        with pytest.raises(ValueError, match="上一批任务尚未结束"):
            await service.trigger_schedule(schedule.schedule_id, manual=True)
    finally:
        runner.release.set()
        await task_manager.stop_workers()

async def test_timed_trigger_skips_overlapping_batch_without_recording_failure() -> None:
    """定时轮询遇到重叠只静默跳过并推下次触发，不动 last_run/last_task/last_error——跳过不是失败。"""
    runner = SlowRunner()
    task_manager = TaskManager(runner=runner, broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        flow = await flow_service.create_flow(_active_flow_request("慢流程"))
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="重叠跳过",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": flow.flow_id}),
            )
        )

        first = await service.trigger_schedule(schedule.schedule_id)
        assert first is not None and first.last_task_id is not None

        skipped = await service.trigger_schedule(schedule.schedule_id)

        assert skipped is not None
        assert skipped.last_task_id == first.last_task_id
        assert skipped.last_run_at == first.last_run_at
        assert skipped.last_error == first.last_error
        # 没有第二批被启动。
        batch = await task_manager.list_tasks(schedule_id=schedule.schedule_id, limit=200)
        assert len(batch) == 1
    finally:
        runner.release.set()
        await task_manager.stop_workers()

async def test_run_summary_reflects_execution_failures_across_batch() -> None:
    """启动成功≠执行成功，「所有流程」不能用最后一个 task 代表整批：两条都在执行阶段失败，
    调度中心必须聚合出整批 failed。"""
    task_manager = TaskManager(runner=AlwaysFailingRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        await flow_service.create_flow(_active_flow_request("流程甲"))
        await flow_service.create_flow(_active_flow_request("流程乙"))
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
        # 两条都成功启动，触发本身不算失败。
        assert triggered.last_error is None

        summary = None
        for _ in range(50):
            summary = await service.get_run_summary(triggered)
            if summary is not None and summary.running == 0:
                break
            await asyncio.sleep(0.01)

        assert summary is not None
        assert summary.total == 2
        assert summary.failed == 2
        assert summary.status == "failed"
    finally:
        await task_manager.stop_workers()

async def test_create_schedule_rejects_draft_bound_flow() -> None:
    """create 就要用触发同一判据拦下绑定草稿的调度，别造出每次触发都失败的定时炸弹。"""
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(task_manager=task_manager, flow_service=flow_service)
    draft = await flow_service.create_flow(_active_flow_request("草稿流程", status="draft"))

    with pytest.raises(ValueError, match="草稿"):
        await service.create_schedule(
            ScheduleCreateRequest(
                name="绑定草稿",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": draft.flow_id}),
            )
        )

async def test_create_schedule_rejects_extension_confirmation_flow() -> None:
    """extension 执行器下含 requireConfirmation 的流程无人值守会挂到确认超时，create 就该拦。"""
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(task_manager=task_manager, flow_service=flow_service)
    flow = await flow_service.create_flow(
        FlowCreateRequest(
            name="敏感动作流程",
            version="v1.0.0",
            status="active",
            definition=_confirmation_definition(),
            acceptanceContract=acceptance_contract("scheduled_rows"),
            defaultBrowserExecutor="extension",
        )
    )

    with pytest.raises(ValueError, match="requireConfirmation"):
        await service.create_schedule(
            ScheduleCreateRequest(
                name="扩展敏感调度",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": flow.flow_id}),
            )
        )


async def test_trigger_rechecks_confirmation_after_flow_executor_changes() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    flow = await flow_service.create_flow(FlowCreateRequest(
        name="执行器后来切到插件",
        status="active",
        definition=_confirmation_definition(),
        acceptanceContract=acceptance_contract("scheduled_rows"),
    ))
    schedule = await service.create_schedule(ScheduleCreateRequest(
        name="跟随当前执行器检查",
        cronExpression="0 * * * *",
        timezone="UTC",
        task=build_task_request().model_copy(update={"flow_id": flow.flow_id}),
    ))

    await flow_service.update_flow(flow.flow_id, FlowUpdateRequest(defaultBrowserExecutor="extension"))
    with pytest.raises(ValueError, match="requireConfirmation"):
        await service.trigger_schedule(schedule.schedule_id)
    updated = await service.get_schedule(schedule.schedule_id)
    assert updated is not None and "requireConfirmation" in (updated.last_error or "")


def test_confirmation_guard_ignores_nodes_without_confirmation_runtime() -> None:
    from app.services.scheduler_service import _definition_has_confirmation

    assert not _definition_has_confirmation(
        {"nodes": [{"id": "fetch", "type": "browser.fetch", "requireConfirmation": True}]}
    )
async def test_trigger_blocks_when_bound_flow_becomes_draft() -> None:
    """创建后流程被改成草稿：触发时按当前状态重判，停在启动前并记原因，不跑出无人负责的结果。"""
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        flow = await flow_service.create_flow(_active_flow_request("上线后改草稿"))
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="绑定后降级",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": flow.flow_id}),
            )
        )
        await flow_service.set_flow_status(flow.flow_id, "draft")

        with pytest.raises(ValueError, match="草稿"):
            await service.trigger_schedule(schedule.schedule_id)

        current = await service.get_schedule(schedule.schedule_id)
        assert current is not None
        assert current.last_error is not None and "草稿" in current.last_error
        assert current.last_task_id is None
        assert await task_manager.list_tasks(schedule_id=schedule.schedule_id, limit=200) == []
    finally:
        await task_manager.stop_workers()


async def test_concurrent_manual_triggers_start_only_one_batch() -> None:
    runner = SlowRunner()
    task_manager = TaskManager(runner=runner, broker=LogBroker())
    flow_service = FlowService()
    service = ScheduleService(
        task_manager=task_manager,
        flow_service=flow_service,
        flow_run_service=FlowRunService(task_manager=task_manager),
    )
    try:
        flow = await flow_service.create_flow(_active_flow_request("慢流程"))
        schedule = await service.create_schedule(
            ScheduleCreateRequest(
                name="并发触发",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": flow.flow_id}),
            )
        )
        results = await asyncio.gather(
            service.trigger_schedule(schedule.schedule_id, manual=True),
            service.trigger_schedule(schedule.schedule_id, manual=True),
            return_exceptions=True,
        )
        assert sum(isinstance(result, ValueError) for result in results) == 1
        assert len(await task_manager.list_schedule_batch_tasks(schedule.schedule_id, schedule.created_at)) == 1
    finally:
        runner.release.set()
        await task_manager.stop_workers()


async def test_enabled_schedule_rejects_missing_bound_flow() -> None:
    service = ScheduleService(task_manager=TaskManager(runner=FakeRunner(), broker=LogBroker()), flow_service=FlowService())
    with pytest.raises(ValueError, match="流程不存在"):
        await service.create_schedule(
            ScheduleCreateRequest(
                name="已删除的流程",
                cronExpression="0 * * * *",
                timezone="UTC",
                task=build_task_request().model_copy(update={"flow_id": "missing-flow"}),
            )
        )


def test_preview_uses_scheduler_cron_semantics() -> None:
    service = ScheduleService(task_manager=TaskManager(runner=FakeRunner(), broker=LogBroker()))
    runs = service.preview_next_runs("0 9 1 * 1", "Asia/Shanghai", count=2, now=datetime(2026, 9, 23, tzinfo=UTC))
    assert runs[0] == datetime(2026, 9, 28, 1, tzinfo=UTC)
    assert runs[1] == datetime(2026, 10, 1, 1, tzinfo=UTC)
