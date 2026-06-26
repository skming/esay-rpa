from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from app.models.schemas import ScheduleCreateRequest, ScheduleSnapshot, ScheduleUpdateRequest
from app.services.flow_runner import FlowRunService
from app.services.flow_service import FlowService
from app.services.schedule_store import InMemoryScheduleStore, ScheduleStore
from app.services.task_manager import TaskManager

logger = logging.getLogger(__name__)


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
        task_snapshot = await self._start_scheduled_task(current)
        updated = current.model_copy(
            update={
                "last_run_at": now,
                "last_task_id": task_snapshot.task_id,
                "updated_at": now,
                "next_run_at": self._compute_next_run(current.cron_expression, current.timezone, now) if current.status == "enabled" else None,
            }
        )
        return await self._store.save(updated)

    async def _start_scheduled_task(self, schedule: ScheduleSnapshot):
        """Start task(s) for a schedule. Returns the last-started TaskSnapshot.

        When flow_id is None (「所有流程」mode), starts one task per active flow
        in parallel and returns the last snapshot.
        """
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
            for t in tasks:
                if isinstance(t, BaseException):
                    logger.warning("所有流程调度中部分流程启动失败：%s", t)
                else:
                    last_ok = t
            if last_ok is None:
                raise ValueError("所有流程均启动失败")
            return last_ok

        if flow_id is None:
            return await self._task_manager.start_task(task_with_schedule_id)

        if self._flow_service is None or self._flow_run_service is None:
            return await self._task_manager.start_task(task_with_schedule_id)

        flow = await self._flow_service.get_flow(flow_id)
        if flow is None:
            raise ValueError(f"调度绑定的流程不存在: {flow_id}")
        return await self._flow_run_service.run_flow(
            flow,
            mode=schedule.task.mode,
            run_request=task_with_schedule_id,
        )

    async def run_due_schedules(self, at: datetime | None = None) -> list[ScheduleSnapshot]:
        triggered: list[ScheduleSnapshot] = []
        now = at or datetime.now(UTC)
        for schedule in await self.due_schedules(now):
            try:
                snapshot = await self.trigger_schedule(schedule.schedule_id)
            except ValueError as exc:
                logger.warning("跳过失效调度 %s：%s", schedule.schedule_id, exc)
                await self._advance_next_run_at(schedule, now)
                continue
            if snapshot is not None:
                triggered.append(snapshot)
        return triggered

    async def _advance_next_run_at(self, schedule: ScheduleSnapshot, now: datetime) -> None:
        """Advance next_run_at without executing the task — used when trigger fails."""
        try:
            next_run = self._compute_next_run(schedule.cron_expression, schedule.timezone, now)
            await self._store.save(schedule.model_copy(update={"next_run_at": next_run}))
        except Exception as exc:
            logger.debug("next_run_at 更新失败 %s: %s", schedule.schedule_id, exc)

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
        except ValueError as exc:
            logger.warning("调度循环停止时忽略已知调度异常：%s", exc)
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
