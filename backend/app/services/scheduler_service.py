from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from app.models.schemas import (
    FlowSnapshot,
    ScheduleCreateRequest,
    ScheduleRunSummary,
    ScheduleSnapshot,
    ScheduleTaskRequest,
    ScheduleUpdateRequest,
    TaskSnapshot,
)
from app.services.browser_action_runner import is_browser_action_node
from app.services.flow_runner import FlowRunService
from app.services.flow_service import FlowService
from app.services.schedule_store import InMemoryScheduleStore, ScheduleStore
from app.services.task_manager import TaskManager

logger = logging.getLogger(__name__)

# 排程运行资格只认 active：draft/paused/disabled/archived 都不是「可无人值守运行」的状态。
_SCHEDULABLE_FLOW_STATUS = "active"
# 这些状态代表「这一批还没跑完」，重叠触发会让两批任务抢同一浏览器、也让批次终态无从区分。
_IN_FLIGHT_TASK_STATUSES = frozenset({"queued", "running", "awaiting_confirmation"})
_FLOW_STATUS_BLOCK_REASON = {
    "draft": "流程仍是草稿，未发布不能定时运行",
    "paused": "流程处于暂停状态，不能定时运行",
    "disabled": "流程已停用，不能定时运行",
    "archived": "流程已归档，不能定时运行",
}


def _definition_has_confirmation(definition: dict[str, object]) -> bool:
    nodes = definition.get("nodes") if isinstance(definition, dict) else None
    if not isinstance(nodes, list):
        return False
    return any(
        isinstance(node, dict) and is_browser_action_node(node) and node.get("requireConfirmation") is True
        for node in nodes
    )


def flow_schedule_block_reason(flow: FlowSnapshot) -> str | None:
    """流程能否被无人值守调度的唯一判据，create/update/enable/触发四处共用，保证口径一致。
    返回 None 表示可运行，否则给出人能看懂的具体原因。"""
    if flow.status != _SCHEDULABLE_FLOW_STATUS:
        return _FLOW_STATUS_BLOCK_REASON.get(flow.status, f"流程状态为 {flow.status}，不能定时运行")
    # requireConfirmation 只在扩展执行器下挂起等待人工点确认；定时无人值守没人点，会一路挂到超时后
    # 取消整个任务，每次触发都失败。playwright 执行器下该标志本就不生效，无需拦。
    if flow.default_browser_executor == "extension" and _definition_has_confirmation(flow.definition):
        return "流程含 requireConfirmation 敏感动作，扩展执行器无人值守触发会挂起到确认超时后失败"
    return None


def _has_acceptance_contract(flow: FlowSnapshot) -> bool:
    # 手工流程允许保存空契约，调度应能执行；只对已声明的契约做完整性校验。
    return bool(flow.acceptance_contract.requirements or flow.acceptance_contract.deliverables)


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
        self._trigger_locks: dict[str, asyncio.Lock] = {}

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
        await self._assert_bound_flow_schedulable(request.task, "enabled" if request.enabled else "disabled")
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
        # 启用一个绑定了不合格流程（如已被改成草稿）的调度，就是在制造每次触发都失败的定时炸弹。
        await self._assert_bound_flow_schedulable(updated.task, updated.status)
        return await self._store.save(updated)

    async def delete_schedule(self, schedule_id: str) -> bool:
        return await self._store.delete(schedule_id)

    async def trigger_schedule(self, schedule_id: str, *, manual: bool = False) -> ScheduleSnapshot | None:
        lock = self._trigger_locks.setdefault(schedule_id, asyncio.Lock())
        async with lock:
            return await self._trigger_schedule_locked(schedule_id, manual=manual)

    async def _trigger_schedule_locked(self, schedule_id: str, *, manual: bool) -> ScheduleSnapshot | None:
        current = await self._store.get(schedule_id)
        if current is None:
            return None

        now = datetime.now(UTC)
        if await self._batch_in_flight(current):
            # 上一批还没跑完就再起一批：真并发会让两批任务抢同一个浏览器 profile，而且「所有流程」
            # 批次的终态要靠 schedule_id 聚合，重叠批次会把两批混成一堆分不清成败。
            if manual:
                raise ValueError("上一批任务尚未结束，已跳过本次触发；请等其完成或到调度历史查看进度")
            # 定时轮询遇到重叠只静默跳过并把 next_run_at 推到下一档，不动 last_run/last_task/last_error——
            # 跳过不是失败，记成失败会污染调度中心的最终结果。
            skipped = current.model_copy(update={"next_run_at": self._safe_next_run(current, now), "updated_at": now})
            return await self._store.save(skipped)

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
        """返回（任务快照，部分失败说明）。多流程与"所有流程"模式并发启动目标流程，
        只要有一个成功就算触发成功，没起来的那些只能靠第二个返回值落进 last_error 才看得见。"""
        task_with_schedule_id = schedule.task.model_copy(update={"schedule_id": schedule.schedule_id})
        flow_id = schedule.task.flow_id
        selected_ids = schedule.task.flow_ids

        if (selected_ids or flow_id is None) and self._flow_service is not None and self._flow_run_service is not None:
            flows = await self._flow_service.list_flows()
            flows_by_id = {flow.flow_id: flow for flow in flows}
            # 每次触发都按流程「当时」的状态重新筛，草稿/停用/归档/暂停一律排除，且排除不算失败——
            # 只挑此刻真能跑的流程，绝不把草稿混进批次。
            schedulable: list[FlowSnapshot] = []
            blocked: list[tuple[str, str, str]] = []
            targets = [flows_by_id[flow_id] for flow_id in selected_ids if flow_id in flows_by_id] if selected_ids else flows
            if selected_ids:
                blocked.extend((flow_id, flow_id, "流程不存在") for flow_id in selected_ids if flow_id not in flows_by_id)
            for flow in targets:
                reason = flow_schedule_block_reason(flow)
                if reason is None:
                    schedulable.append(flow)
                else:
                    blocked.append((flow.flow_id, flow.name, reason))
            if not schedulable:
                detail = "；".join(f"{name}：{reason}" for _, name, reason in blocked) or "没有任何流程"
                raise ValueError(f"没有可运行的流程：{detail}")
            flow_requests = [
                (
                    flow,
                    task_with_schedule_id.model_copy(update={
                        "flow_id": flow.flow_id,
                        "flow_ids": [],
                        "flow_name": flow.name,
                        "browser_executor": flow.default_browser_executor,
                    }),
                )
                for flow in schedulable
            ]
            tasks = await asyncio.gather(
                *[
                    self._flow_run_service.run_flow(
                        flow,
                        mode=schedule.task.mode,
                        run_request=flow_request,
                        enforce_acceptance_contract=_has_acceptance_contract(flow),
                    )
                    for flow, flow_request in flow_requests
                ],
                return_exceptions=True,
            )
            last_ok = None
            failures: list[tuple[str, BaseException]] = []
            if selected_ids:
                for blocked_id, name, reason in blocked:
                    failures.append((name, ValueError(reason)))
                    await self._task_manager.record_schedule_start_failure(
                        task_with_schedule_id.model_copy(update={
                            "flow_id": blocked_id,
                            "flow_ids": [],
                            "flow_name": name,
                        }),
                        reason,
                    )
            for t, (f, flow_request) in zip(tasks, flow_requests):
                if isinstance(t, BaseException):
                    failures.append((f.name, t))
                    await self._task_manager.record_schedule_start_failure(
                        flow_request,
                        str(t) or t.__class__.__name__,
                    )
                else:
                    self._notify_task_started(t, f.flow_id, schedule.name)
                    last_ok = t
            if last_ok is None:
                raise ValueError(f"所有流程均启动失败：{_describe_flow_failures(failures)}")
            if not failures:
                return last_ok, None
            summary = _describe_flow_failures(failures)
            partial_error = f"{len(failures)}/{len(schedulable) + (len(blocked) if selected_ids else 0)} 个流程未启动：{summary}"
            # 永远起不来的流程每个 tick 都会失败一次，秒级 cron 下几分钟就能把日志淹掉。
            # last_error 已经承载了「现在是什么状态」，日志只需要记下「失败内容变了」这一刻。
            if partial_error != (schedule.last_error or ""):
                logger.warning("所有流程调度中部分流程启动失败：%s", summary)
            return last_ok, partial_error

        if selected_ids:
            raise ValueError("多流程调度需要流程服务")

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
        # 创建调度后流程可能被改成草稿/停用，或换上 requireConfirmation：触发时按当前状态重判，
        # 不合格就停在启动前并留下明确原因，不让它跑出无人负责的结果。
        reason = flow_schedule_block_reason(flow)
        if reason is not None:
            raise ValueError(reason)
        snapshot = await self._flow_run_service.run_flow(
            flow,
            mode=schedule.task.mode,
            run_request=task_with_schedule_id.model_copy(update={"browser_executor": flow.default_browser_executor}),
            enforce_acceptance_contract=_has_acceptance_contract(flow),
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
        next_run = self._safe_next_run(schedule, now)
        await self._store.save(schedule.model_copy(update={"next_run_at": next_run, "last_error": error, "updated_at": now}))

    def _safe_next_run(self, schedule: ScheduleSnapshot, now: datetime) -> datetime | None:
        """算不出（cron 已失效）或调度停用时给 None：让失效调度停在原地等人修，而不是每个 tick 空转重试。"""
        if schedule.status != "enabled":
            return None
        try:
            return self._compute_next_run(schedule.cron_expression, schedule.timezone, now)
        except Exception:
            return None

    async def _assert_bound_flow_schedulable(self, task: ScheduleTaskRequest, status: str) -> None:
        """create/update/enable 的前置资格校验，与触发时同一判据，两个入口给出一致错误。
        只在启用且明确绑定流程时校验；"所有流程"在触发时按当时状态过滤。"""
        if status != "enabled":
            return
        if task.flow_ids and (self._flow_service is None or self._flow_run_service is None):
            raise ValueError("多流程调度需要流程服务")
        if self._flow_service is None:
            return
        for flow_id in task.flow_ids or ([task.flow_id] if task.flow_id is not None else []):
            flow = await self._flow_service.get_flow(flow_id)
            if flow is None:
                raise ValueError(f"调度绑定的流程不存在: {flow_id}")
            reason = flow_schedule_block_reason(flow)
            if reason is not None:
                raise ValueError(f"{flow.name}：{reason}")

    async def _batch_in_flight(self, schedule: ScheduleSnapshot) -> bool:
        """本调度是否还有未结束的任务（含「所有流程」批次里的任一个）。靠 task 上的 schedule_id 关联，
        重启后残留的 running 任务已被 reconcile 落成 stopped，不会把「其实已死」误判成在飞。"""
        return await self._task_manager.has_unfinished_schedule_tasks(schedule.schedule_id)

    async def get_run_summary(self, schedule: ScheduleSnapshot) -> ScheduleRunSummary | None:
        """惰性从任务库聚合最近一次触发批次的真实终态：启动成功≠执行成功，「所有流程」更不能用
        最后一个 task 代表整批。以 last_run_at 为水位线 + schedule_id 圈定这一批（触发前捕获 now 作
        last_run_at，批次任务的 created_at 均不早于它，故用 >= 精确圈定、不会误纳上一批）。"""
        if schedule.last_run_at is None:
            return None
        batch = await self._task_manager.list_schedule_batch_tasks(schedule.schedule_id, schedule.last_run_at)
        if not batch:
            return None
        running = success = failed = stopped = 0
        for t in batch:
            if t.status in _IN_FLIGHT_TASK_STATUSES:
                running += 1
            elif t.status == "success":
                success += 1
            elif t.status == "error":
                failed += 1
            elif t.status == "stopped":
                stopped += 1
        return ScheduleRunSummary(
            schedule_id=schedule.schedule_id,
            run_at=schedule.last_run_at,
            total=len(batch),
            running=running,
            success=success,
            failed=failed,
            stopped=stopped,
            status=self._derive_batch_status(running, success, failed, stopped),
            task_ids=[t.task_id for t in batch],
        )

    async def run_summaries(self) -> dict[str, ScheduleRunSummary]:
        result: dict[str, ScheduleRunSummary] = {}
        for schedule in await self._store.list():
            summary = await self.get_run_summary(schedule)
            if summary is not None:
                result[schedule.schedule_id] = summary
        return result

    @staticmethod
    def _derive_batch_status(running: int, success: int, failed: int, stopped: int) -> str:
        if running > 0:
            return "running"
        terminal_kinds = sum(1 for count in (success, failed, stopped) if count > 0)
        if terminal_kinds == 0:
            return "empty"
        if terminal_kinds > 1:
            return "partial"
        if success > 0:
            return "success"
        if failed > 0:
            return "failed"
        return "stopped"

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

    def preview_next_runs(
        self, cron_expression: str, timezone: str, *, count: int = 5, now: datetime | None = None
    ) -> list[datetime]:
        cursor = now or datetime.now(UTC)
        runs: list[datetime] = []
        for _ in range(count):
            cursor = self._compute_next_run(cron_expression, timezone, cursor)
            runs.append(cursor)
        return runs


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
