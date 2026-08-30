from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from app.models.schemas import ScheduleCreateRequest, ScheduleSnapshot, ScheduleUpdateRequest, TaskSnapshot
from app.services.flow_runner import FlowRunService
from app.services.flow_service import FlowService
from app.services.schedule_store import InMemoryScheduleStore, ScheduleStore
from app.services.task_manager import TaskManager

logger = logging.getLogger(__name__)


def _describe_flow_failures(failures: list[tuple[str, BaseException]]) -> str:
    """按原因归并：一批流程往往栽在同一句校验上，逐条罗列会把真正不同的那条原因挤出可读范围。"""
    grouped: dict[str, list[str]] = {}
    for flow_name, exc in failures:
        grouped.setdefault(str(exc) or exc.__class__.__name__, []).append(flow_name)
    return "；".join(f"{'、'.join(names)}：{reason}" for reason, names in grouped.items())


class ScheduleService:
    """Manages cron schedules: CRUD operations, next-run computation, and background tick execution."""

    def __init__(
        self,
        task_manager: TaskManager,
        store: ScheduleStore | None = None,
        flow_service: FlowService | None = None,
        flow_run_service: FlowRunService | None = None,
    ) -> None:
        self._task_manager = task_manager
        self._store = store or InMemoryScheduleStore()
        self._flow_service = flow_service
        self._flow_run_service = flow_run_service
        self._on_task_started = None

    def set_task_started_hook(self, hook) -> None:
        self._on_task_started = hook

    def _notify_task_started(self, snapshot, flow_id: str | None, schedule_name: str) -> None:
        if self._on_task_started is None or snapshot is None:
            return
        try:
            self._on_task_started(
                snapshot.task_id,
                getattr(snapshot, "flow_id", None) or flow_id,
                schedule_name,
            )
        except Exception as exc:  # hook must never break scheduling
            logger.warning("task_started hook failed: %s", exc)

    async def create_schedule(self, request: ScheduleCreateRequest) -> ScheduleSnapshot:
        now = datetime.now(UTC)
        schedule = ScheduleSnapshot(
            schedule_id=str(uuid4()),
            name=request.name,
            cron_expression=request.cron_expression,
            timezone=request.timezone,
            status="enabled" if request.enabled else "disabled",
            task=request.task,
            created_at=now,
            updated_at=now,
            next_run_at=self._compute_next_run(request.cron_expression, request.timezone, now) if request.enabled else None,
        )
        return await self._store.save(schedule)

    async def list_schedules(self) -> list[ScheduleSnapshot]:
        return await self._store.list()

    async def get_schedule(self, schedule_id: str) -> ScheduleSnapshot | None:
        return await self._store.get(schedule_id)

    async def update_schedule(self, schedule_id: str, request: ScheduleUpdateRequest) -> ScheduleSnapshot | None:
        current = await self._store.get(schedule_id)
        if current is None:
            return None

        cron_expression = request.cron_expression or current.cron_expression
        timezone = request.timezone or current.timezone
        status = self._resolve_status(current.status, request.enabled)
        now = datetime.now(UTC)
        updated = current.model_copy(
            update={
                "name": request.name or current.name,
                "cron_expression": cron_expression,
                "timezone": timezone,
                "status": status,
                "task": request.task or current.task,
                "updated_at": now,
                "next_run_at": self._compute_next_run(cron_expression, timezone, now) if status == "enabled" else None,
                # 用户改完配置就等于宣告上一轮失败已作废；不清会让界面的失败提示挂到下一次成功触发为止。
                "last_error": None,
            }
        )
        return await self._store.save(updated)

    async def delete_schedule(self, schedule_id: str) -> bool:
        return await self._store.delete(schedule_id)

    async def trigger_schedule(self, schedule_id: str) -> ScheduleSnapshot | None:
        current = await self._store.get(schedule_id)
        if current is None:
            return None

        now = datetime.now(UTC)
        try:
            # 顺序不能换：cron/时区非法时这一步抛在启动之后，last_task_id 落不进库，任务成孤儿。
            next_run_at = self._compute_next_run(current.cron_expression, current.timezone, now) if current.status == "enabled" else None
            task_snapshot, partial_error = await self._start_scheduled_task(current)
        except Exception as exc:
            # 手动触发和轮询共用这条记账：只在 run_due_schedules 里记，界面就看不见手动触发的失败。
            await self._record_failure(current, now, str(exc) or exc.__class__.__name__)
            raise
        updated = current.model_copy(
            update={
                "last_run_at": now,
                "last_task_id": task_snapshot.task_id,
                "updated_at": now,
                "next_run_at": next_run_at,
                "last_error": partial_error,
            }
        )
        return await self._store.save(updated)

    async def _start_scheduled_task(self, schedule: ScheduleSnapshot) -> tuple[TaskSnapshot, str | None]:
        """返回（任务快照，部分失败说明）。flow_id 为 None（"所有流程"模式）时并发启动所有活跃流程，
        只要有一个成功就算触发成功，没起来的那些只能靠第二个返回值落进 last_error 才看得见。"""
        task_with_schedule_id = schedule.task.model_copy(update={"schedule_id": schedule.schedule_id})
        flow_id = schedule.task.flow_id

        if flow_id is None and self._flow_service is not None and self._flow_run_service is not None:
            flows = await self._flow_service.list_flows()
            active_flows = [f for f in flows if f.status not in ("archived", "disabled", "paused")]
            if not active_flows:
                raise ValueError("没有可运行的活跃流程")
            tasks = await asyncio.gather(
                *[
                    self._flow_run_service.run_flow(
                        flow,
                        mode=schedule.task.mode,
                        run_request=task_with_schedule_id.model_copy(
                            update={"flow_id": flow.flow_id, "flow_name": flow.name}
                        ),
                    )
                    for flow in active_flows
                ],
                return_exceptions=True,
            )
            last_ok = None
            failures: list[tuple[str, BaseException]] = []
            for t, f in zip(tasks, active_flows):
                if isinstance(t, BaseException):
                    failures.append((f.name, t))
                else:
                    self._notify_task_started(t, f.flow_id, schedule.name)
                    last_ok = t
            if last_ok is None:
                raise ValueError(f"所有流程均启动失败：{_describe_flow_failures(failures)}")
            if not failures:
                return last_ok, None
            summary = _describe_flow_failures(failures)
            logger.warning("所有流程调度中部分流程启动失败：%s", summary)
            return last_ok, f"{len(failures)}/{len(active_flows)} 个流程未启动：{summary}"

        if flow_id is None:
            snapshot = await self._task_manager.start_task(task_with_schedule_id)
            self._notify_task_started(snapshot, None, schedule.name)
            return snapshot, None

        if self._flow_service is None or self._flow_run_service is None:
            snapshot = await self._task_manager.start_task(task_with_schedule_id)
            self._notify_task_started(snapshot, flow_id, schedule.name)
            return snapshot, None

        flow = await self._flow_service.get_flow(flow_id)
        if flow is None:
            raise ValueError(f"调度绑定的流程不存在: {flow_id}")
        snapshot = await self._flow_run_service.run_flow(
            flow,
            mode=schedule.task.mode,
            run_request=task_with_schedule_id,
        )
        self._notify_task_started(snapshot, flow_id, schedule.name)
        return snapshot, None

    async def run_due_schedules(self, at: datetime | None = None) -> list[ScheduleSnapshot]:
        triggered: list[ScheduleSnapshot] = []
        now = at or datetime.now(UTC)
        for schedule in await self.due_schedules(now):
            try:
                snapshot = await self.trigger_schedule(schedule.schedule_id)
            except Exception as exc:
                # 抛出去会被 SchedulerLoop 的兜底 except 接走，同一 tick 里排在后面的到期调度全不跑。
                # 原因已由 trigger_schedule 记进 last_error；同一条失败每秒一份 traceback 只会把日志淹掉。
                logger.warning("调度 %s 触发失败：%s", schedule.schedule_id, exc)
                continue
            if snapshot is not None:
                triggered.append(snapshot)
        return triggered

    async def _record_failure(self, schedule: ScheduleSnapshot, now: datetime, error: str) -> None:
        """last_error 是失败的唯一出口：不落库，API 和界面就看不出这次 tick 和成功的区别。
        next_run_at 留在过去会让下个 tick 立刻重触发；cron 算不出下一次、或调度已停用时只能清空，
        等用户改完 cron 由 update_schedule 重算。"""
        next_run: datetime | None = None
        if schedule.status == "enabled":
            try:
                next_run = self._compute_next_run(schedule.cron_expression, schedule.timezone, now)
            except Exception:
                next_run = None
        await self._store.save(schedule.model_copy(update={"next_run_at": next_run, "last_error": error, "updated_at": now}))

    async def due_schedules(self, at: datetime | None = None) -> list[ScheduleSnapshot]:
        now = at or datetime.now(UTC)
        return await self._store.due(now)

    def _resolve_status(self, current_status: str, enabled: bool | None) -> str:
        if enabled is None:
            return current_status
        return "enabled" if enabled else "disabled"

    def _compute_next_run(self, cron_expression: str, timezone: str, now: datetime) -> datetime:
        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"未知时区: {timezone}") from exc

        localized_now = now.astimezone(zone)
        try:
            next_local = croniter(cron_expression, localized_now).get_next(datetime)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"无效 Cron 表达式: {cron_expression}") from exc

        return next_local.astimezone(UTC)


class SchedulerLoop:
    def __init__(self, schedule_service: ScheduleService, interval_seconds: float = 1.0) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds 必须大于 0")
        self._schedule_service = schedule_service
        self._interval_seconds = interval_seconds
        self._worker: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._stop_event = asyncio.Event()
        self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._worker is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        try:
            await self._worker
        except asyncio.CancelledError:
            # _run 自己吞掉了所有 Exception，能漏到这里的只有 worker 被取消。而 stop() 是
            # lifespan 关停链的第一步，异常放出去后面 task_manager/runtime_services 的清理
            # 全不跑，浏览器进程和数据库连接漏到进程被杀为止。
            logger.warning("调度循环在停止前已被取消")
        finally:
            self._worker = None
            self._stop_event = None

    async def _run(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self._schedule_service.run_due_schedules()
            except Exception:
                logger.exception("调度循环执行失败，等待下个周期继续")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue
