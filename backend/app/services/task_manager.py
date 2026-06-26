from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core import storage
from app.models.schemas import ArtifactContent, ArtifactSnapshot, DebugControlCommand, QueueStats, RunConfigSnapshot, RunTaskRequest, RuntimeProgress, RuntimeVariableSnapshot, ScrapeResult, TaskLogEntry, TaskSnapshot, TaskStatus
from app.services.artifact_store import ArtifactStore, LocalArtifactStore
from app.services.browser_action_runner import BrowserActionContext, BrowserActionRunner, apply_browser_result_variables, is_browser_action_node
from app.services.control_action_runner import BreakLoopSignal, ControlActionRunner, apply_control_result_variables, is_control_action_node, is_subprocess_node
from app.services.data_action_runner import DataActionRunner, apply_data_result_variables, is_data_action_node
from app.services.log_broker import LogBroker
from app.services.flow_definition import FlowDefinitionSelector
from app.services.flow_control import evaluate_condition_detail, is_condition_node, read_condition_expression, select_branch_edges
from app.services.flow_loop import is_loop_node, materialize_loop_item, read_edge_target, read_loop_config, split_loop_edges
from app.services.file_action_runner import FileActionRunner, apply_file_result_variables, is_file_action_node
from app.services.http_action_runner import HttpActionRunner, apply_http_result_variables, is_http_action_node
from app.services.runtime_variables import RuntimeVariableStore, apply_fetch_result_variables
from app.services.scrapling_runner import RunnerProtocol
from app.services.script_action_runner import ScriptActionRunner, apply_script_result_variables, is_script_action_node
from app.services.task_store import InMemoryTaskStore, TaskStore
from app.services.task_queue import InMemoryTaskQueue, TaskQueue, TaskRunner
from app.services.variable_action_runner import VariableActionRunner, apply_variable_result_variables, is_variable_action_node


def _timestamp_sort_key(value: datetime) -> float:
    """统一 naive/aware datetime，避免合并内存任务和持久化任务时排序崩溃。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _resolve_output_slug(request: RunTaskRequest) -> str:
    """返回稳定的产物目录键。

    已保存流程优先使用 flow_id，避免用户重命名流程后把同一流程的产物拆到多个目录。
    仅临时/未保存流程回退到流程名，保证旧的临时运行仍有可读目录。
    """
    if request.flow_id is not None and request.flow_id.strip():
        return storage.slugify(request.flow_id, fallback="flow")
    return storage.slugify(request.flow_name)


@dataclass
class TaskRecord:
    """In-memory mutable state for a running or queued task.

    Kept in `TaskManager._tasks` for the lifetime of the task; persisted to
    `TaskStore` at key lifecycle transitions (queued → running → terminal).
    """

    request: RunTaskRequest
    snapshot: TaskSnapshot
    executable_nodes: list[dict[str, object]] = field(default_factory=list)
    variables: RuntimeVariableStore = field(default_factory=RuntimeVariableStore)
    artifacts: list[ArtifactSnapshot] = field(default_factory=list)
    logs: list[TaskLogEntry] = field(default_factory=list)
    canceled: bool = False
    active_node_id: str | None = None
    # asyncio.Event used to pause execution in debug mode between nodes.
    debug_waiter: asyncio.Event = field(default_factory=asyncio.Event)
    debug_step_once: bool = False
    debug_resume_until_breakpoint: bool = False
    paused_node_id: str | None = None
    # asyncio.Event and value storage for variable.input nodes that need user input.
    input_waiter: asyncio.Event = field(default_factory=asyncio.Event)
    input_prompt: str | None = None
    input_value: str = ""
    # Lazily populated in _has_breakpoint; avoids rebuilding the node map on every debug step.
    breakpoint_ids: frozenset[str] | None = field(default=None, init=False, repr=False, compare=False)


@dataclass
class FlowRunState:
    """Transient execution context passed through the node-execution loop."""

    started: datetime
    total_steps: int
    results: list[ScrapeResult] = field(default_factory=list)
    browser_context: BrowserActionContext | None = None
    fetch_attempts: int = 0
    executable_steps: int = 0


_SENTINEL = object()


class TaskManager:
    """Orchestrates task lifecycle: queuing, execution, debug control, and result persistence.

    Acts as the single point of coordination between the HTTP API layer, the
    async task queue, action runners (browser, script, data, …), and the log
    broker.
    """

    def __init__(
        self,
        runner: RunnerProtocol,
        broker: LogBroker,
        artifact_store: ArtifactStore | None = None,
        task_store: TaskStore | None = None,
        flow_service: object | None = None,
        concurrency: int = 2,
        queue_factory: Callable[[TaskRunner], TaskQueue] | None = None,
    ) -> None:
        self._runner = runner
        self._broker = broker
        self._artifact_store = artifact_store or LocalArtifactStore()
        self._task_store = task_store or InMemoryTaskStore()
        self._flow_service = flow_service
        self._http_runner = HttpActionRunner()
        self._control_action_runner = ControlActionRunner()
        self._data_action_runner = DataActionRunner()
        self._variable_action_runner = VariableActionRunner()
        _browser_session_dir = str(storage.resolve_browser_profile_dir())
        self._browser_action_runner = BrowserActionRunner(session_dir=_browser_session_dir)
        self._file_action_runner = FileActionRunner()
        self._script_action_runner = ScriptActionRunner()
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()
        self._queue = queue_factory(self._run_record) if queue_factory is not None else InMemoryTaskQueue(self._run_record, concurrency=concurrency)

    def start_workers(self) -> None:
        self._queue.start()

    async def stop_workers(self) -> None:
        await self._queue.stop()

    async def start_task(self, request: RunTaskRequest) -> TaskSnapshot:
        """Validate the request, create a TaskRecord, and enqueue it for execution."""
        executable_nodes = self._resolve_executable_nodes(request)
        task_id = f"t_{uuid4()}"
        now = datetime.now(UTC)
        total_steps = max(len(executable_nodes), 1) + 2
        variables = RuntimeVariableStore.from_initial(request.variables)
        run_timestamp = now.astimezone().strftime("%Y%m%d_%H%M%S")
        flow_slug = _resolve_output_slug(request)
        variables.set("run_timestamp", run_timestamp, scope="全局")
        # Canonical output location for this run; scripts/file.write should target
        # ${var.output_dir}/<name>.<ext> so outputs stay isolated and prunable.
        variables.set("flow_slug", flow_slug, scope="全局")
        variables.set("output_dir", f"{storage.RUN_OUTPUT_ROOT}/{flow_slug}/{task_id}", scope="全局")
        variables.set("output_prefix", f"{storage.RUN_OUTPUT_ROOT}/{flow_slug}/{task_id}/{run_timestamp}", scope="全局")
        storage.run_output_dir(flow_slug, task_id).mkdir(parents=True, exist_ok=True)
        snapshot = TaskSnapshot(
            task_id=task_id,
            flow_id=request.flow_id,
            schedule_id=request.schedule_id,
            flow_name=request.flow_name,
            status="queued",
            mode=request.mode,
            progress=RuntimeProgress(current_step=0, total_steps=total_steps, percent=0, elapsed_ms=0),
            variables=variables.snapshots(),
            run_config=RunConfigSnapshot.from_request(request),
            created_at=now,
            updated_at=now,
        )
        record = TaskRecord(request=request, snapshot=snapshot, executable_nodes=executable_nodes, variables=variables)
        async with self._lock:
            self._tasks[task_id] = record
            await self._task_store.save_task(snapshot, request)
            await self._queue.enqueue(task_id)
        return snapshot

    async def get_task(self, task_id: str) -> TaskSnapshot | None:
        record = self._tasks.get(task_id)
        if record is not None:
            return record.snapshot
        return await self._task_store.get_task(task_id)

    async def list_tasks(self, *, flow_id: str | None = None, schedule_id: str | None = None, limit: int = 50) -> list[TaskSnapshot]:
        # Merge persisted snapshots with the in-memory active set so callers always see live status.
        snapshots = await self._task_store.list_tasks(flow_id=flow_id, schedule_id=schedule_id, limit=limit)
        active_snapshots = [record.snapshot for record in self._tasks.values()]
        if flow_id is not None:
            active_snapshots = [s for s in active_snapshots if s.flow_id == flow_id]
        if schedule_id is not None:
            active_snapshots = [s for s in active_snapshots if s.schedule_id == schedule_id]
        merged = {snapshot.task_id: snapshot for snapshot in snapshots}
        merged.update({snapshot.task_id: snapshot for snapshot in active_snapshots})
        return sorted(
            merged.values(),
            key=lambda snapshot: _timestamp_sort_key(snapshot.updated_at),
            reverse=True,
        )[: max(1, min(limit, 200))]

    async def get_logs(self, task_id: str) -> list[TaskLogEntry] | None:
        record = self._tasks.get(task_id)
        if record is not None:
            return list(record.logs)
        return await self._task_store.list_logs(task_id)

    async def get_variables(self, task_id: str) -> list[RuntimeVariableSnapshot] | None:
        record = self._tasks.get(task_id)
        if record is not None:
            return list(record.snapshot.variables)
        return await self._task_store.list_variables(task_id)

    async def get_artifacts(self, task_id: str) -> list[ArtifactSnapshot] | None:
        snapshot = await self.get_task(task_id)
        if snapshot is None:
            return None
        return snapshot.artifacts or self._artifact_store.list_task_artifacts(task_id)

    async def get_artifact_content(self, task_id: str, artifact_id: str) -> ArtifactContent | None:
        if await self.get_task(task_id) is None:
            return None
        return self._artifact_store.read_artifact_content(task_id, artifact_id)

    async def queue_stats(self) -> QueueStats:
        snapshot = await self._queue.snapshot()
        return QueueStats(
            backend=snapshot.backend,
            concurrency=snapshot.concurrency,
            queued_count=snapshot.queued_count,
            active_count=snapshot.active_count,
            active_task_ids=snapshot.active_task_ids,
            started=snapshot.started,
        )

    async def provide_input(self, task_id: str, value: str) -> TaskSnapshot | None:
        record = self._tasks.get(task_id)
        if record is None or record.input_prompt is None:
            return None
        record.input_value = value
        record.input_waiter.set()
        return record.snapshot

    async def stop_task(self, task_id: str) -> TaskSnapshot | None:
        record = self._tasks.get(task_id)
        if record is None:
            return None
        record.canceled = True
        record.input_waiter.set()  # unblock any waiting input node
        record.debug_waiter.set()
        canceled_running_task = self._queue.cancel(task_id)
        await self._append_log(record, "warn", "用户请求停止任务", None, node_id="end")
        current_progress = record.snapshot.progress
        current_step = min(max(current_progress.current_step, 1), current_progress.total_steps)
        progress = RuntimeProgress(
            current_step=current_step,
            total_steps=current_progress.total_steps,
            percent=max(current_progress.percent, int((current_step / current_progress.total_steps) * 100)),
            elapsed_ms=current_progress.elapsed_ms,
        )
        if record.snapshot.status == "queued" or canceled_running_task:
            await self._update_snapshot(record, status="stopped", progress=progress)
        return record.snapshot

    async def debug_control(self, task_id: str, command: DebugControlCommand) -> TaskSnapshot | None:
        record = self._tasks.get(task_id)
        if record is None:
            return None
        if record.request.mode != "debug":
            raise ValueError("任务未以 debug 模式运行")
        if record.snapshot.status not in {"queued", "running"}:
            raise ValueError("任务已结束，无法发送调试命令")

        label = _DEBUG_CONTROL_LABELS[command]
        if command == "continue":
            record.debug_resume_until_breakpoint = True
            record.debug_step_once = False
        else:
            record.debug_resume_until_breakpoint = False
            record.debug_step_once = True

        record.variables.set("debug_command", label, scope="局部")
        await self._update_snapshot(record, variables=record.variables.snapshots())
        await self._append_log(record, "running", f"调试控制 · {label}", None, node_id=record.paused_node_id or record.active_node_id)
        record.debug_waiter.set()
        return record.snapshot

    async def _run_record(self, task_id: str) -> None:
        record = self._tasks[task_id]
        if record.canceled:
            await self._update_snapshot(record, status="stopped")
            await self._append_log(record, "warn", "任务已在排队阶段停止", None, node_id="start")
            return
        started = datetime.now(UTC)
        try:
            total_steps = max(len(record.executable_nodes), 1) + 2
            await self._update_snapshot(record, status="running", progress=RuntimeProgress(current_step=1, total_steps=total_steps, percent=10, elapsed_ms=0))
            # For flow-based runs, target_url is a legacy scraper field that carries an unrelated default URL
            startup_detail = None if record.request.flow_definition is not None else str(record.request.target_url)
            await self._append_log(record, "info", f"任务启动 · {record.request.flow_name}", startup_detail, node_id="start")
            await self._append_log(record, "info", _build_run_config_message(record.request), None, node_id=record.request.start_node_id or "start")

            result = await self._run_fetch_nodes(record, started)

            elapsed_ms = max(int((datetime.now(UTC) - started).total_seconds() * 1000), 0)
            artifact = await self._artifact_store.save_json(
                task_id=task_id,
                artifact_type="dataset",
                filename="scrape-result.json",
                payload={
                    "result": result.model_dump(mode="json", by_alias=True),
                    "variables": [variable.model_dump(mode="json", by_alias=True) for variable in record.variables.snapshots()],
                },
                metadata={
                    "flow_name": record.request.flow_name,
                    "selector": result.selector,
                    "count": result.count,
                    "run_scope": record.request.scope,
                    "start_node_id": record.request.start_node_id,
                    "failure_strategy": record.request.failure_strategy,
                    "screenshot": record.request.screenshot,
                    "concurrency": record.request.concurrency,
                    "variables_count": len(record.variables.snapshots()),
                },
                flow_id=_resolve_output_slug(record.request),
            )
            artifacts = [*record.artifacts, artifact]
            await self._update_snapshot(
                record,
                status="success",
                result=result,
                artifacts=artifacts,
                variables=record.variables.snapshots(),
                progress=RuntimeProgress(current_step=total_steps, total_steps=total_steps, percent=100, elapsed_ms=elapsed_ms),
            )
            await self._append_log(record, "success", "结果已保存", artifact.storage_url, node_id=record.active_node_id or "end")
            await self._append_log(record, "success", "任务完成", f"命中 {result.count} 条", node_id="end")
            await self._notify_flow_run_complete(record, "success")
        except asyncio.CancelledError:
            await self._update_snapshot(record, status="stopped")
            await self._append_log(record, "warn", "任务已停止", None, node_id="end")
            await self._notify_flow_run_complete(record, "stopped")
        except Exception as exc:
            elapsed_ms = max(int((datetime.now(UTC) - started).total_seconds() * 1000), 0)
            current_progress = record.snapshot.progress
            await self._update_snapshot(
                record,
                status="error",
                error=str(exc),
                progress=RuntimeProgress(
                    current_step=current_progress.current_step,
                    total_steps=current_progress.total_steps,
                    percent=min(current_progress.percent, 99),
                    elapsed_ms=elapsed_ms,
                ),
            )
            await self._append_log(record, "error", "任务失败", str(exc), node_id=record.active_node_id or "n1")
            await self._notify_flow_run_complete(record, "error")

    async def _notify_flow_run_complete(self, record: TaskRecord, status: str) -> None:
        if self._flow_service is None or record.snapshot.flow_id is None:
            return
        try:
            await self._flow_service.update_last_run(record.snapshot.flow_id, status, datetime.now(UTC))
        except Exception:
            pass

    async def _update_snapshot(
        self,
        record: TaskRecord,
        *,
        status: TaskStatus | None = None,
        progress: RuntimeProgress | None = None,
        result: ScrapeResult | None = None,
        artifacts: list[ArtifactSnapshot] | None = None,
        variables: list[RuntimeVariableSnapshot] | None = None,
        error: str | None = None,
        input_prompt: str | None = _SENTINEL,
    ) -> None:
        update: dict[str, object] = {
            "status": status or record.snapshot.status,
            "progress": progress or record.snapshot.progress,
            "result": result if result is not None else record.snapshot.result,
            "artifacts": artifacts if artifacts is not None else record.snapshot.artifacts,
            "variables": variables if variables is not None else record.variables.snapshots(),
            "error": error,
            "updated_at": datetime.now(UTC),
        }
        if input_prompt is not _SENTINEL:
            update["input_prompt"] = input_prompt
        record.snapshot = record.snapshot.model_copy(update=update)
        await self._task_store.save_task(record.snapshot, record.request)

    async def _append_log(self, record: TaskRecord, level: str, message: str, detail: str | None, *, node_id: str | None = None) -> None:
        entry = TaskLogEntry(task_id=record.snapshot.task_id, level=level, message=message, detail=detail, node_id=node_id)
        record.logs.append(entry)
        await self._task_store.append_log(entry)
        await self._broker.publish(entry)

    async def _run_fetch_nodes(self, record: TaskRecord, started: datetime) -> ScrapeResult:
        if record.request.flow_definition is not None:
            return await self._run_flow_definition(record, started)

        if not record.executable_nodes:
            record.active_node_id = record.request.start_node_id or "n1"
            request = FlowDefinitionSelector.build_request_for_fetch_node(
                record.request,
                _resolve_node_variables(_build_request_node(record.request), record.variables),
            )
            return await self._runner.run(
                record.snapshot.task_id,
                request,
                lambda level, message, detail=None: self._append_log(record, level, message, detail, node_id=record.active_node_id or "n1"),
            )

        results: list[ScrapeResult] = []
        total_steps = len(record.executable_nodes) + 2
        for index, node in enumerate(record.executable_nodes, start=1):
            if record.canceled:
                raise asyncio.CancelledError

            node_id = _read_node_id(node, fallback=f"n{index}")
            record.active_node_id = node_id
            node_title = _read_node_title(node, fallback=f"采集步骤 {index}")
            await self._update_snapshot(
                record,
                progress=RuntimeProgress(
                    current_step=index + 1,
                    total_steps=total_steps,
                    percent=min(95, max(10, int(((index + 1) / total_steps) * 100))),
                    elapsed_ms=max(int((datetime.now(UTC) - started).total_seconds() * 1000), 0),
                ),
            )
            if _is_variable_node(node):
                await self._pause_for_debug_if_needed(record, node_id=node_id, node_title=node_title)
                await self._run_variable_action_node(record, node, node_id=node_id, node_title=node_title)
                continue

            await self._pause_for_debug_if_needed(record, node_id=node_id, node_title=node_title)
            step_request = FlowDefinitionSelector.build_request_for_fetch_node(record.request, _resolve_node_variables(node, record.variables))
            await self._append_log(record, "running", f"执行节点 · {node_title}", step_request.selector, node_id=node_id)
            result = await self._run_fetch_node(record, step_request, node=node, node_id=node_id, node_title=node_title)
            if result is not None:
                results.append(result)
                saved_names = apply_fetch_result_variables(node, result, record.variables)
                if saved_names:
                    await self._append_log(record, "success", f"输出变量已更新 · {', '.join(saved_names)}", None, node_id=node_id)
                    await self._update_snapshot(record, variables=record.variables.snapshots())

        if not results:
            raise RuntimeError("所有 browser.fetch 节点执行失败")
        return _merge_scrape_results(results)

    async def _run_flow_definition(self, record: TaskRecord, started: datetime) -> ScrapeResult:
        definition = record.request.flow_definition
        if definition is None:
            raise RuntimeError("流程定义不能为空")

        node_by_id = _build_node_map(definition)
        if not node_by_id:
            raise RuntimeError("流程定义缺少节点")

        start_node_id = _select_run_start_node_id(record.request, node_by_id)
        if start_node_id is None:
            raise RuntimeError("流程定义缺少起始节点")

        adjacency = FlowDefinitionSelector.build_edge_adjacency(definition, node_by_id)
        should_follow_edges = record.request.scope != "selected-only"
        total_steps = max(len(record.executable_nodes), 1) + 2
        state = FlowRunState(started=started, total_steps=total_steps)

        try:
            if should_follow_edges:
                await self._run_flow_path(record, state, start_node_id=start_node_id, node_by_id=node_by_id, adjacency=adjacency)
            else:
                node = node_by_id.get(start_node_id)
                if node is not None and node.get("disabled") is not True:
                    await self._execute_flow_node(record, state, node, outgoing_edges=[], should_follow_edges=False)
        finally:
            await _export_browser_cookies(state.browser_context)
            await self._browser_action_runner.close_context(state.browser_context)
            self._prune_run_outputs(record)

        if state.results:
            return _merge_scrape_results(state.results)
        if state.fetch_attempts > 0:
            raise RuntimeError("所有 browser.fetch 节点执行失败")
        if state.executable_steps > 0:
            return ScrapeResult(url="", selector="", count=0, values=[])
        raise RuntimeError("流程定义未找到可执行节点")

    def _prune_run_outputs(self, record: TaskRecord) -> None:
        """Enforce per-flow output retention after a run finishes (best-effort)."""
        try:
            flow_slug = _resolve_output_slug(record.request)
            storage.prune_run_outputs(flow_slug)
        except Exception:  # retention must never fail a run
            pass

    async def _run_flow_path(
        self,
        record: TaskRecord,
        state: FlowRunState,
        *,
        start_node_id: str,
        node_by_id: dict[str, dict[str, object]],
        adjacency: dict[str, list[dict[str, object]]],
        stop_node_ids: set[str] | None = None,
    ) -> None:
        stack = [start_node_id]
        visited: set[str] = set()
        stops = stop_node_ids or set()

        while stack:
            if record.canceled:
                raise asyncio.CancelledError

            node_id = stack.pop()
            if node_id in stops or node_id in visited:
                continue
            visited.add(node_id)

            node = node_by_id.get(node_id)
            if node is None:
                continue

            outgoing_edges = adjacency.get(node_id, [])
            if node.get("disabled") is True:
                _push_edge_targets(stack, outgoing_edges, visited, stop_node_ids=stops)
                continue

            if is_loop_node(node):
                next_edges = await self._run_loop_node(
                    record,
                    state,
                    loop_node_id=node_id,
                    node=node,
                    outgoing_edges=outgoing_edges,
                    node_by_id=node_by_id,
                    adjacency=adjacency,
                )
            else:
                next_edges = await self._execute_flow_node(record, state, node, outgoing_edges=outgoing_edges, should_follow_edges=True)

            _push_edge_targets(stack, next_edges, visited, stop_node_ids=stops)

    async def _run_loop_node(
        self,
        record: TaskRecord,
        state: FlowRunState,
        *,
        loop_node_id: str,
        node: dict[str, object],
        outgoing_edges: list[dict[str, object]],
        node_by_id: dict[str, dict[str, object]],
        adjacency: dict[str, list[dict[str, object]]],
    ) -> list[dict[str, object]]:
        record.active_node_id = _read_node_id(node, fallback=loop_node_id)
        node_title = _read_node_title(node, fallback="循环节点")
        await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
        state.executable_steps += 1
        await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)

        config = read_loop_config(node, record.variables)
        body_edges, exit_edges = split_loop_edges(
            outgoing_edges,
            loop_node_id=loop_node_id,
            adjacency=adjacency,
        )
        await self._append_log(
            record,
            "running",
            f"循环开始 · {node_title}",
            f"{config.items_variable} → {config.planned_iterations} 次",
            node_id=record.active_node_id,
        )

        if not body_edges:
            await self._append_log(record, "warn", "循环节点缺少循环体出口", node_title, node_id=record.active_node_id)
            return exit_edges

        for index, raw_item in enumerate(config.items[: config.max_iterations]):
            if record.canceled:
                raise asyncio.CancelledError

            record.active_node_id = _read_node_id(node, fallback=loop_node_id)
            item = materialize_loop_item(raw_item)
            record.variables.set(config.item_variable, item, scope="循环")
            record.variables.set(config.index_variable, index, scope="循环")
            await self._update_snapshot(record, variables=record.variables.snapshots())
            await self._append_log(
                record,
                "running",
                f"循环迭代 · {node_title} #{index + 1}/{config.planned_iterations}",
                config.item_variable,
                node_id=record.active_node_id,
            )

            for edge in body_edges:
                target = read_edge_target(edge)
                if target is not None:
                    try:
                        await self._run_flow_path(
                            record,
                            state,
                            start_node_id=target,
                            node_by_id=node_by_id,
                            adjacency=adjacency,
                            stop_node_ids={loop_node_id},
                        )
                    except BreakLoopSignal:
                        await self._append_log(record, "warn", f"循环中断 · {node_title}", config.item_variable, node_id=record.active_node_id)
                        return exit_edges

        record.active_node_id = _read_node_id(node, fallback=loop_node_id)
        if config.truncated:
            await self._append_log(record, "warn", f"循环达到上限 · {config.max_iterations} 次", config.items_variable, node_id=record.active_node_id)
        await self._append_log(record, "success", f"循环完成 · {node_title}", f"{config.planned_iterations} 次", node_id=record.active_node_id)
        return exit_edges

    async def _execute_flow_node(
        self,
        record: TaskRecord,
        state: FlowRunState,
        node: dict[str, object],
        *,
        outgoing_edges: list[dict[str, object]],
        should_follow_edges: bool,
    ) -> list[dict[str, object]]:
        node_type = node.get("type")
        record.active_node_id = _read_node_id(node, fallback="node")
        next_edges = outgoing_edges

        if _is_variable_node(node):
            node_title = _read_node_title(node, fallback="变量步骤")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            await self._run_variable_action_node(record, node, node_id=record.active_node_id, node_title=node_title)
        elif is_condition_node(node, outgoing_edges):
            node_title = _read_node_title(node, fallback="条件节点")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            evaluation = evaluate_condition_detail(node, record.variables)
            condition_result = evaluation.result
            next_edges = select_branch_edges(outgoing_edges, condition_result)
            expression = read_condition_expression(node) or ""
            result_label = "是" if condition_result else "否"
            await self._append_log(record, "success", f"条件判断 · {node_title} → {result_label}", evaluation.detail or expression, node_id=record.active_node_id)
            if should_follow_edges and outgoing_edges and not next_edges:
                await self._append_log(record, "warn", "条件分支未匹配，流程在此节点停止", expression, node_id=record.active_node_id)
        elif node_type == "browser.fetch":
            node_title = _read_node_title(node, fallback=f"采集步骤 {state.executable_steps + 1}")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            state.fetch_attempts += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            step_request = FlowDefinitionSelector.build_request_for_fetch_node(record.request, _resolve_node_variables(node, record.variables))
            await self._append_log(record, "running", f"执行节点 · {node_title}", step_request.selector, node_id=record.active_node_id)
            result = await self._run_fetch_node(record, step_request, node=node, node_id=record.active_node_id, node_title=node_title)
            if result is not None:
                state.results.append(result)
                saved_names = apply_fetch_result_variables(node, result, record.variables)
                if saved_names:
                    await self._append_log(record, "success", f"输出变量已更新 · {', '.join(saved_names)}", None, node_id=record.active_node_id)
                    await self._update_snapshot(record, variables=record.variables.snapshots())
        elif is_http_action_node(node):
            node_title = _read_node_title(node, fallback=f"HTTP 请求 {state.executable_steps + 1}")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            http_result = await self._run_http_node(record, node, node_id=record.active_node_id, node_title=node_title)
            if http_result is not None:
                state.results.append(http_result)
        elif is_browser_action_node(node):
            node_title = _read_node_title(node, fallback=f"浏览器动作 {state.executable_steps + 1}")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            if state.browser_context is None:
                state.browser_context = await self._browser_action_runner.create_context()
            browser_result = await self._run_browser_action_node(record, node, state.browser_context, node_id=record.active_node_id, node_title=node_title)
            if browser_result is not None and _is_collectable_result_node(node):
                state.results.append(browser_result)
        elif is_subprocess_node(node):
            node_title = _read_node_title(node, fallback=f"子流程 {state.executable_steps + 1}")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            await self._run_subprocess_node(record, state, node, node_id=record.active_node_id, node_title=node_title)
        elif is_control_action_node(node):
            node_title = _read_node_title(node, fallback=f"控制动作 {state.executable_steps + 1}")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            control_result = await self._run_control_action_node(record, node, node_id=record.active_node_id, node_title=node_title)
            if control_result is not None:
                state.results.append(control_result)
        elif is_file_action_node(node):
            node_title = _read_node_title(node, fallback=f"文件节点 {state.executable_steps + 1}")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            await self._run_file_action_node(record, node, node_id=record.active_node_id, node_title=node_title)
        elif is_script_action_node(node):
            node_title = _read_node_title(node, fallback=f"脚本节点 {state.executable_steps + 1}")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            await self._run_script_action_node(record, node, node_id=record.active_node_id, node_title=node_title)
        elif is_data_action_node(node):
            node_title = _read_node_title(node, fallback=f"数据处理 {state.executable_steps + 1}")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            data_result = await self._run_data_action_node(record, node, node_id=record.active_node_id, node_title=node_title)
            if data_result is not None:
                state.results.append(data_result)

        return next_edges

    async def _pause_for_debug_if_needed(self, record: TaskRecord, *, node_id: str, node_title: str) -> None:
        if record.request.mode != "debug":
            return
        if record.debug_resume_until_breakpoint and not record.debug_step_once and not _has_breakpoint(record, node_id):
            return
        if not record.debug_step_once and not _has_breakpoint(record, node_id):
            return

        record.paused_node_id = node_id
        record.debug_step_once = False
        record.debug_waiter = asyncio.Event()
        record.variables.set("debug_paused_node", node_id, scope="局部")
        await self._update_snapshot(record, variables=record.variables.snapshots())
        await self._append_log(record, "warn", f"命中断点 · {node_title}", "等待调试命令", node_id=node_id)
        await record.debug_waiter.wait()
        record.paused_node_id = None
        if record.canceled:
            raise asyncio.CancelledError

    async def _update_step_progress(self, record: TaskRecord, started: datetime, *, current_step: int, total_steps: int) -> None:
        display_step = min(current_step + 1, max(total_steps - 1, 1))
        await self._update_snapshot(
            record,
            progress=RuntimeProgress(
                current_step=display_step,
                total_steps=total_steps,
                percent=min(95, max(10, int((display_step / total_steps) * 100))),
                elapsed_ms=max(int((datetime.now(UTC) - started).total_seconds() * 1000), 0),
            ),
        )

    async def _run_with_retry(
        self,
        record: TaskRecord,
        node: dict[str, object] | None,
        *,
        node_id: str,
        node_title: str,
        label: str,
        execute: Callable[[], object],
        extra_reraise: tuple[type[BaseException], ...] = (),
    ) -> object:
        """Run *execute* with the flow-level retry / continue-on-error policy applied.

        *execute* must be an async callable (coroutine function) that returns the
        action result on success and raises on failure.  ``asyncio.CancelledError``
        and any types in *extra_reraise* propagate immediately without retrying.
        """
        attempts = 2 if record.request.failure_strategy == "retry" else 1
        for attempt in range(1, attempts + 1):
            try:
                return await execute()
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError) or (extra_reraise and isinstance(exc, extra_reraise)):
                    raise
                if attempt < attempts:
                    await self._append_log(record, "warn", f"重试{label} · {node_title}", str(exc), node_id=node_id)
                    continue
                if record.request.failure_strategy == "continue" or _node_continue_on_error(node):
                    await self._append_log(record, "error", f"{label}失败，继续执行 · {node_title}", str(exc), node_id=node_id)
                    return None
                raise
        return None

    async def _run_fetch_node(
        self,
        record: TaskRecord,
        request: RunTaskRequest,
        *,
        node: dict[str, object] | None = None,
        node_id: str,
        node_title: str,
    ) -> ScrapeResult | None:
        attempt_count = 0

        async def _execute():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count > 1:
                await self._append_log(record, "warn", f"重试节点 · {node_title}", request.selector, node_id=node_id)
            return await self._runner.run(
                record.snapshot.task_id,
                request,
                lambda level, message, detail=None, node_id=node_id: self._append_log(record, level, message, detail, node_id=node_id),
            )

        return await self._run_with_retry(record, node, node_id=node_id, node_title=node_title, label="节点", execute=_execute)

    async def _run_control_action_node(
        self,
        record: TaskRecord,
        node: dict[str, object],
        *,
        node_id: str,
        node_title: str,
    ) -> ScrapeResult | None:
        attempt_count = 0

        async def _execute():
            nonlocal attempt_count
            attempt_count += 1
            resolved_node = _resolve_node_variables(node, record.variables)
            detail = _read_control_detail(resolved_node)
            if attempt_count > 1:
                await self._append_log(record, "warn", f"重试控制动作 · {node_title}", detail, node_id=node_id)
            else:
                await self._append_log(record, "running", f"执行控制动作 · {node_title}", detail, node_id=node_id)
            try:
                control_result = await self._control_action_runner.run(resolved_node, record.variables, timeout_ms=_read_node_timeout(resolved_node, default=record.request.timeout_ms))
            except BreakLoopSignal:
                await self._append_log(record, "warn", f"触发中断循环 · {node_title}", None, node_id=node_id)
                raise
            await self._append_log(record, "success", f"控制动作完成 · {node_title}", control_result.detail, node_id=node_id)
            saved_names = apply_control_result_variables(node, control_result, record.variables)
            if saved_names:
                await self._append_log(record, "success", f"控制输出变量已更新 · {', '.join(saved_names)}", None, node_id=node_id)
                await self._update_snapshot(record, variables=record.variables.snapshots())
            return control_result.to_scrape_result()

        return await self._run_with_retry(record, node, node_id=node_id, node_title=node_title, label="控制动作", execute=_execute, extra_reraise=(BreakLoopSignal,))

    async def _run_variable_action_node(
        self,
        record: TaskRecord,
        node: dict[str, object],
        *,
        node_id: str,
        node_title: str,
    ) -> ScrapeResult | None:
        attempt_count = 0

        async def _execute():
            nonlocal attempt_count
            attempt_count += 1
            resolved_node = _resolve_node_variables(node, record.variables)
            detail = _read_variable_detail(resolved_node)
            if attempt_count > 1:
                await self._append_log(record, "warn", f"重试变量 / 消息 · {node_title}", detail, node_id=node_id)
            else:
                await self._append_log(record, "running", f"执行变量 / 消息 · {node_title}", detail, node_id=node_id)
            action_type = str(resolved_node.get("type", ""))
            if action_type == "variable.input":
                variable_result = await self._run_user_input_node(record, resolved_node, node_id=node_id, node_title=node_title)
            else:
                variable_result = await self._variable_action_runner.run(resolved_node, record.variables, timeout_ms=_read_node_timeout(resolved_node, default=record.request.timeout_ms))
            message = _build_variable_result_message(variable_result.action_type, node_title)
            await self._append_log(record, variable_result.log_level, message, variable_result.detail, node_id=node_id)
            saved_names = apply_variable_result_variables(node, variable_result, record.variables)
            if saved_names:
                await self._append_log(record, "success", f"变量输出已更新 · {', '.join(saved_names)}", None, node_id=node_id)
                await self._update_snapshot(record, variables=record.variables.snapshots())
            return variable_result.to_scrape_result()

        return await self._run_with_retry(record, node, node_id=node_id, node_title=node_title, label="变量 / 消息", execute=_execute)

    async def _run_user_input_node(
        self,
        record: TaskRecord,
        node: dict[str, object],
        *,
        node_id: str,
        node_title: str,
    ):
        from app.services.variable_action_runner import VariableActionResult
        prompt = str(node.get("message") or node.get("description") or node_title or "请输入")
        has_default = any(
            key in node and str(node.get(key) if node.get(key) is not None else "").strip()
            for key in ("defaultValue", "value", "inputValue")
        )
        default_value = str(node.get("defaultValue") or node.get("value") or node.get("inputValue") or "")
        timeout_ms = _read_node_timeout(node, default=300_000)  # default 5 min

        if has_default:
            await self._append_log(record, "info", f"使用默认输入 · {node_title}", prompt, node_id=node_id)
            return VariableActionResult(action_type="variable.input", detail=prompt, values=[default_value])

        record.input_prompt = prompt
        record.input_value = default_value
        record.input_waiter.clear()
        await self._update_snapshot(record, input_prompt=prompt)
        await self._append_log(record, "input", f"等待用户输入 · {node_title}", prompt, node_id=node_id)

        try:
            await asyncio.wait_for(record.input_waiter.wait(), timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            record.input_prompt = None
            await self._update_snapshot(record, input_prompt=None)
            raise RuntimeError(f"等待用户输入超时（{timeout_ms // 1000}s）: {prompt}")

        value = record.input_value
        record.input_prompt = None
        await self._update_snapshot(record, input_prompt=None)
        return VariableActionResult(action_type="variable.input", detail=prompt, values=[value])

    async def _run_http_node(
        self,
        record: TaskRecord,
        node: dict[str, object],
        *,
        node_id: str,
        node_title: str,
    ) -> ScrapeResult | None:
        attempt_count = 0

        async def _execute():
            nonlocal attempt_count
            attempt_count += 1
            resolved_node = _resolve_node_variables(node, record.variables)
            if attempt_count > 1:
                await self._append_log(record, "warn", f"重试 HTTP 节点 · {node_title}", _read_node_url(resolved_node, record.variables), node_id=node_id)
            else:
                await self._append_log(record, "running", f"执行 HTTP 节点 · {node_title}", _read_node_url(resolved_node, record.variables), node_id=node_id)
            http_result = await self._http_runner.run(resolved_node, record.variables, timeout_ms=_read_node_timeout(resolved_node, default=record.request.timeout_ms))
            await self._append_log(record, "success", f"HTTP 请求完成 · {http_result.status_code}", http_result.url, node_id=node_id)
            saved_names = apply_http_result_variables(node, http_result, record.variables)
            if saved_names:
                await self._append_log(record, "success", f"HTTP 输出变量已更新 · {', '.join(saved_names)}", None, node_id=node_id)
                await self._update_snapshot(record, variables=record.variables.snapshots())
            return http_result.to_scrape_result()

        return await self._run_with_retry(record, node, node_id=node_id, node_title=node_title, label="HTTP 节点", execute=_execute)

    async def _run_browser_action_node(
        self,
        record: TaskRecord,
        node: dict[str, object],
        context: BrowserActionContext,
        *,
        node_id: str,
        node_title: str,
    ) -> ScrapeResult | None:
        attempt_count = 0

        async def _execute():
            nonlocal attempt_count
            attempt_count += 1
            resolved_node = _resolve_node_variables(node, record.variables)
            if attempt_count > 1:
                await self._append_log(record, "warn", f"重试浏览器动作 · {node_title}", _read_node_browser_detail(resolved_node), node_id=node_id)
            else:
                await self._append_log(record, "running", f"执行浏览器动作 · {node_title}", _read_node_browser_detail(resolved_node), node_id=node_id)
            browser_result = await self._browser_action_runner.run(resolved_node, record.variables, context, timeout_ms=_read_node_timeout(resolved_node, default=record.request.timeout_ms))
            await self._append_log(record, "success", f"浏览器动作完成 · {node_title}", browser_result.detail, node_id=node_id)
            saved_names = apply_browser_result_variables(node, browser_result, record.variables)
            if saved_names:
                await self._append_log(record, "success", f"浏览器输出变量已更新 · {', '.join(saved_names)}", None, node_id=node_id)
                await self._update_snapshot(record, variables=record.variables.snapshots())
            await self._save_browser_screenshot(record, context, node_id=node_id, node_title=node_title)
            return browser_result.to_scrape_result()

        return await self._run_with_retry(record, node, node_id=node_id, node_title=node_title, label="浏览器动作", execute=_execute)

    async def _run_file_action_node(
        self,
        record: TaskRecord,
        node: dict[str, object],
        *,
        node_id: str,
        node_title: str,
    ) -> ScrapeResult | None:
        attempt_count = 0

        async def _execute():
            nonlocal attempt_count
            attempt_count += 1
            resolved_node = _resolve_node_variables(node, record.variables)
            detail = _read_file_detail(resolved_node)
            if attempt_count > 1:
                await self._append_log(record, "warn", f"重试文件节点 · {node_title}", detail, node_id=node_id)
            else:
                await self._append_log(record, "running", f"执行文件节点 · {node_title}", detail, node_id=node_id)
            file_result = await self._file_action_runner.run(resolved_node, record.variables, timeout_ms=_read_node_timeout(resolved_node, default=record.request.timeout_ms))
            await self._append_log(record, "success", f"文件节点完成 · {node_title}", file_result.path, node_id=node_id)
            saved_names = apply_file_result_variables(node, file_result, record.variables)
            if saved_names:
                await self._append_log(record, "success", f"文件输出变量已更新 · {', '.join(saved_names)}", None, node_id=node_id)
                await self._update_snapshot(record, variables=record.variables.snapshots())
            # excel.addrow 常在循环中执行，避免每行重复注册；由 excel.save 统一登记最终工作簿。
            action_type = resolved_node.get("type", "")
            if action_type in ("file.write", "excel.write", "excel.save", "file.copy", "file.move", "file.rename", "file.compress") and file_result.path:
                await self._save_file_artifact(record, file_result.path, node_id=node_id, node_title=node_title)
            return file_result.to_scrape_result()

        return await self._run_with_retry(record, node, node_id=node_id, node_title=node_title, label="文件节点", execute=_execute)

    async def _run_script_action_node(
        self,
        record: TaskRecord,
        node: dict[str, object],
        *,
        node_id: str,
        node_title: str,
    ) -> ScrapeResult | None:
        attempt_count = 0

        async def _execute():
            nonlocal attempt_count
            attempt_count += 1
            resolved_node = _resolve_node_variables(node, record.variables)
            detail = _read_script_detail(resolved_node)
            if attempt_count > 1:
                await self._append_log(record, "warn", f"重试脚本节点 · {node_title}", detail, node_id=node_id)
            else:
                await self._append_log(record, "running", f"执行脚本节点 · {node_title}", detail, node_id=node_id)
            script_result = await self._script_action_runner.run(resolved_node, record.variables, timeout_ms=_read_node_timeout(resolved_node, default=record.request.timeout_ms))
            if script_result.exit_code != 0:
                raise RuntimeError(f"脚本退出码 {script_result.exit_code}: {script_result.stderr or script_result.stdout}")
            await self._append_log(record, "success", f"脚本节点完成 · {node_title}", script_result.stdout or detail, node_id=node_id)
            saved_names = apply_script_result_variables(node, script_result, record.variables)
            if saved_names:
                await self._append_log(record, "success", f"脚本输出变量已更新 · {', '.join(saved_names)}", None, node_id=node_id)
                await self._update_snapshot(record, variables=record.variables.snapshots())
            # 若脚本 stdout 是工作区内存在的文件路径，注册为采集结果 artifact
            stdout_path = (script_result.stdout or "").strip()
            if stdout_path and len(stdout_path) < 512:
                try:
                    workspace = storage.resolve_workspace_root()
                    candidate = (workspace / stdout_path).resolve()
                    if candidate.is_file() and str(candidate).startswith(str(workspace)):
                        await self._save_file_artifact(record, str(candidate), node_id=node_id, node_title=node_title)
                except OSError:
                    pass
            return script_result.to_scrape_result()

        return await self._run_with_retry(record, node, node_id=node_id, node_title=node_title, label="脚本节点", execute=_execute)

    async def _run_data_action_node(
        self,
        record: TaskRecord,
        node: dict[str, object],
        *,
        node_id: str,
        node_title: str,
    ) -> ScrapeResult | None:
        attempt_count = 0

        async def _execute():
            nonlocal attempt_count
            attempt_count += 1
            resolved_node = _resolve_node_variables(node, record.variables)
            detail = _read_data_detail(resolved_node)
            if attempt_count > 1:
                await self._append_log(record, "warn", f"重试数据处理 · {node_title}", detail, node_id=node_id)
            else:
                await self._append_log(record, "running", f"执行数据处理 · {node_title}", detail, node_id=node_id)
            data_result = await self._data_action_runner.run(resolved_node, record.variables, timeout_ms=_read_node_timeout(resolved_node, default=record.request.timeout_ms))
            await self._append_log(record, "success", f"数据处理完成 · {node_title}", f"{data_result.count} 条", node_id=node_id)
            saved_names = apply_data_result_variables(node, data_result, record.variables)
            if saved_names:
                await self._append_log(record, "success", f"数据输出变量已更新 · {', '.join(saved_names)}", None, node_id=node_id)
                await self._update_snapshot(record, variables=record.variables.snapshots())
            return data_result.to_scrape_result()

        return await self._run_with_retry(record, node, node_id=node_id, node_title=node_title, label="数据处理", execute=_execute)

    async def _run_subprocess_node(
        self,
        record: TaskRecord,
        state: FlowRunState,
        node: dict[str, object],
        *,
        node_id: str,
        node_title: str,
    ) -> None:
        flow_id = str(node.get("flowId") or "").strip()
        if not flow_id:
            raise ValueError("control.subprocess 节点缺少 flowId")
        if self._flow_service is None:
            raise RuntimeError("子流程节点需要 FlowService，当前服务不可用")

        await self._append_log(record, "running", f"进入子流程 · {node_title}", flow_id, node_id=node_id)
        sub_flow = await self._flow_service.get_flow(flow_id)
        if sub_flow is None:
            raise ValueError(f"子流程不存在: {flow_id}")

        sub_definition = getattr(sub_flow, "definition", None)
        if sub_definition is None:
            raise RuntimeError(f"子流程 {flow_id} 无有效定义")

        sub_node_map = _build_node_map(sub_definition)
        sub_adjacency = FlowDefinitionSelector.build_edge_adjacency(sub_definition, sub_node_map)

        start_node_id: str | None = None
        for nid, n in sub_node_map.items():
            if nid == "start" or n.get("type") == "start":
                start_node_id = nid
                break
        start_node_id = start_node_id or next(iter(sub_node_map), None)
        if start_node_id is None:
            raise RuntimeError(f"子流程 {flow_id} 没有可执行节点")

        await self._run_flow_path(record, state, start_node_id=start_node_id, node_by_id=sub_node_map, adjacency=sub_adjacency)
        await self._append_log(record, "success", f"子流程完成 · {node_title}", flow_id, node_id=node_id)

        output_variable = str(node.get("outputVariable") or node.get("responseVariable") or "").strip() or None
        if output_variable:
            record.variables.set(output_variable, flow_id, scope="局部")
            await self._append_log(record, "success", f"子流程输出变量已更新 · {output_variable}", None, node_id=node_id)
            await self._update_snapshot(record, variables=record.variables.snapshots())

    async def _save_browser_screenshot(
        self,
        record: TaskRecord,
        context: BrowserActionContext,
        *,
        node_id: str,
        node_title: str,
    ) -> None:
        if not record.request.screenshot:
            return
        content = await self._browser_action_runner.screenshot(context)
        artifact = await self._artifact_store.save_bytes(
            task_id=record.snapshot.task_id,
            artifact_type="screenshot",
            filename=f"{_safe_artifact_name(node_id)}.png",
            content=content,
            content_type="image/png",
            metadata={
                "flow_name": record.request.flow_name,
                "node_id": node_id,
                "node_title": node_title,
                "run_scope": record.request.scope,
            },
            flow_id=_resolve_output_slug(record.request),
        )
        record.artifacts.append(artifact)
        await self._update_snapshot(record, artifacts=record.artifacts)
        await self._append_log(record, "success", f"截图已保存 · {node_title}", artifact.storage_url, node_id=node_id)

    async def _save_file_artifact(
        self,
        record: TaskRecord,
        file_path: str,
        *,
        node_id: str,
        node_title: str,
    ) -> None:
        import mimetypes
        from pathlib import Path as _Path
        try:
            p = _Path(file_path)
            if not p.exists() or not p.is_file():
                return
            size = p.stat().st_size
            if size > 10 * 1024 * 1024:
                return
            content_type, _ = mimetypes.guess_type(file_path)
            _md_exts = {".md", ".markdown", ".mdown", ".mkd"}
            is_markdown = p.suffix.lower() in _md_exts
            if content_type is None and is_markdown:
                # mimetypes 在多数系统上不识别 .md，显式标注为 text/markdown，
                # 以便读取时按原始文本返回（而非 base64），前端可渲染富文本
                content_type = "text/markdown"
            content_type = content_type or "application/octet-stream"
            _ooxml_exts = {".xlsx", ".xls", ".xlsm", ".docx", ".doc", ".pptx", ".ppt"}
            is_ooxml = p.suffix.lower() in _ooxml_exts
            is_text = content_type.startswith("text/") or content_type in ("application/json", "application/xml")
            artifact_type = "dataset" if content_type == "application/json" or is_ooxml else "report"
            meta = {"flow_name": record.request.flow_name, "node_id": node_id, "node_title": node_title, "source_path": file_path}
            if is_ooxml:
                # 保存原始字节，由前端 @silurus/ooxml 负责渲染预览
                raw = p.read_bytes()
                artifact = await self._artifact_store.save_bytes(
                    task_id=record.snapshot.task_id,
                    artifact_type=artifact_type,
                    filename=p.name,
                    content=raw,
                    content_type=content_type,
                    metadata=meta,
                    flow_id=_resolve_output_slug(record.request),
                )
            elif is_text and not is_markdown:
                payload_text = p.read_text("utf-8", errors="replace")
                artifact = await self._artifact_store.save_json(
                    task_id=record.snapshot.task_id,
                    artifact_type=artifact_type,
                    filename=p.name,
                    payload={"content": payload_text},
                    metadata=meta,
                    flow_id=_resolve_output_slug(record.request),
                )
            else:
                raw = p.read_bytes()
                artifact = await self._artifact_store.save_bytes(
                    task_id=record.snapshot.task_id,
                    artifact_type=artifact_type,
                    filename=p.name,
                    content=raw,
                    content_type=content_type,
                    metadata=meta,
                    flow_id=_resolve_output_slug(record.request),
                )
            record.artifacts.append(artifact)
            await self._update_snapshot(record, artifacts=record.artifacts)
            await self._append_log(record, "success", f"文件已注册为采集结果 · {node_title}", artifact.storage_url, node_id=node_id)
        except Exception:
            pass

    def _resolve_executable_nodes(self, request: RunTaskRequest) -> list[dict[str, object]]:
        if request.flow_definition is None:
            return []
        return FlowDefinitionSelector.select_executable_nodes(
            request.flow_definition,
            scope=request.scope,
            start_node_id=request.start_node_id,
        )


def _build_run_config_message(request: RunTaskRequest) -> str:
    scope_label = _RUN_SCOPE_LABELS[request.scope]
    failure_label = _RUN_FAILURE_STRATEGY_LABELS[request.failure_strategy]
    screenshot_label = "开启" if request.screenshot else "关闭"
    start_node_text = f" · 起点 {request.start_node_id}" if request.start_node_id is not None else ""
    return f"运行配置 · 范围 {scope_label} · 并发 {request.concurrency} · 失败策略 {failure_label} · 截图 {screenshot_label}{start_node_text}"


def _node_continue_on_error(node: dict[str, object] | None) -> bool:
    if node is None:
        return False
    value = node.get("continueOnError")
    return isinstance(value, bool) and value


def _merge_scrape_results(results: list[ScrapeResult]) -> ScrapeResult:
    if not results:
        return ScrapeResult(url="", selector="", count=0, values=[])
    if len(results) == 1:
        return results[0]

    values: list[str] = []
    for result in results:
        values.extend(result.values)

    return ScrapeResult(
        url=", ".join(dict.fromkeys(result.url for result in results)),
        selector=", ".join(result.selector for result in results),
        count=sum(result.count for result in results),
        values=values,
    )


def _is_collectable_result_node(node: dict[str, object]) -> bool:
    """判断节点结果是否应进入最终采集结果。

    很多浏览器节点会返回执行摘要，诊断抽取也会返回 ScrapeResult。
    最终结果只应包含用户关心的数据节点，辅助节点可显式设置
    includeInResult=false，避免把登录检测、表头探测等过程数据混入输出。
    """
    if node.get("includeInResult") is False:
        return False
    return node.get("type") in {"browser.extract", "ui.extract", "browser.scrape", "browser.fetch"}


def _read_node_id(node: dict[str, object], *, fallback: str) -> str:
    value = node.get("id")
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _read_node_title(node: dict[str, object], *, fallback: str) -> str:
    value = node.get("title")
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _is_variable_node(node: dict[str, object]) -> bool:
    return is_variable_action_node(node)


def _has_breakpoint(record: TaskRecord, node_id: str) -> bool:
    if record.breakpoint_ids is None:
        definition = record.request.flow_definition
        if definition is None:
            record.breakpoint_ids = frozenset()
        else:
            node_map = _build_node_map(definition)
            record.breakpoint_ids = frozenset(
                nid for nid, n in node_map.items() if n.get("breakpoint") is True
            )
    return node_id in record.breakpoint_ids


def _build_node_map(definition: dict[str, object]) -> dict[str, dict[str, object]]:
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return {}
    return {node["id"]: node for node in nodes if isinstance(node, dict) and isinstance(node.get("id"), str)}


def _select_run_start_node_id(request: RunTaskRequest, node_by_id: dict[str, dict[str, object]]) -> str | None:
    if request.start_node_id is not None:
        return request.start_node_id if request.start_node_id in node_by_id else None

    for node in node_by_id.values():
        node_id = node.get("id")
        if node_id == "start" or node.get("type") == "start":
            return node_id if isinstance(node_id, str) else None
    return next(iter(node_by_id), None)


def _push_edge_targets(
    stack: list[str],
    edges: list[dict[str, object]],
    visited: set[str],
    *,
    stop_node_ids: set[str] | None = None,
) -> None:
    stops = stop_node_ids or set()
    for edge in reversed(edges):
        target = edge.get("target")
        if isinstance(target, str) and target not in visited and target not in stops:
            stack.append(target)


def _resolve_node_variables(node: dict[str, object], variables: RuntimeVariableStore) -> dict[str, object]:
    resolved = dict(node)
    for key in (
        "targetUrl",
        "selector",
        "attribute",
        "url",
        "endpoint",
        "requestBody",
        "body",
        "inputValue",
        "value",
        "path",
        "scriptPath",
        "filePath",
        "targetPath",
        "column",
        "content",
        "defaultValue",
        "message",
        "channel",
        "rows",
        "inputVariable",
        "pattern",
        "operation",
        "search",
        "replacement",
        "delimiter",
        "left",
        "right",
        "leftVariable",
        "rightVariable",
        "operator",
        "delayMs",
        "durationMs",
        "distance",
        "index",
        "targetSelector",
        "target",
        "command",
        "flowId",
    ):
        value = resolved.get(key)
        if isinstance(value, str):
            resolved[key] = variables.resolve_text(value)
    return resolved


def _read_node_timeout(node: dict[str, object], *, default: int) -> int:
    value = node.get("timeoutMs")
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _read_node_url(node: dict[str, object], variables: RuntimeVariableStore) -> str | None:
    for key in ("url", "targetUrl", "endpoint"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return variables.resolve_text(value.strip())
    return None


def _read_node_browser_detail(node: dict[str, object]) -> str | None:
    for key in ("targetUrl", "url", "selector", "targetSelector", "target", "distance", "index"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def _read_control_detail(node: dict[str, object]) -> str | None:
    for key in ("delayMs", "durationMs", "timeoutMs", "description"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def _read_variable_detail(node: dict[str, object]) -> str | None:
    for key in ("variableName", "name", "outputVariable", "inputVariable", "message", "content", "value", "description", "channel"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _build_variable_result_message(action_type: str, node_title: str) -> str:
    labels = {
        "variable.step": "变量已更新",
        "variable.set": "变量已更新",
        "variable.assign": "变量已更新",
        "variable.get": "变量已读取",
        "variable.input": "输入弹窗已记录",
        "variable.log": "流程日志",
        "variable.notify": "消息通知已记录",
        "variable.clipboard": "剪贴板已更新",
    }
    return f"{labels.get(action_type, '变量 / 消息完成')} · {node_title}"


def _read_file_detail(node: dict[str, object]) -> str | None:
    for key in ("path", "filePath", "targetPath", "targetUrl"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _read_script_detail(node: dict[str, object]) -> str | None:
    for key in ("path", "scriptPath", "filePath", "targetPath"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _read_data_detail(node: dict[str, object]) -> str | None:
    for key in ("inputVariable", "inputValue", "operation", "pattern", "operator"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _safe_artifact_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return normalized or "screenshot"


def _build_request_node(request: RunTaskRequest) -> dict[str, object]:
    return {
        "type": "browser.fetch",
        "targetUrl": str(request.target_url),
        "selector": request.selector,
        "fetcher": request.fetcher,
        "extractMode": request.extract_mode,
        "attribute": request.attribute,
        "adaptive": request.adaptive,
        "autoSave": request.auto_save,
        "timeoutMs": request.timeout_ms,
    }


_RUN_SCOPE_LABELS = {
    "full": "完整运行",
    "from-selection": "从选中步骤运行",
    "selected-only": "仅运行选中步骤",
}

_RUN_FAILURE_STRATEGY_LABELS = {
    "stop": "停止运行",
    "continue": "继续执行",
    "retry": "重试当前步骤",
}

_DEBUG_CONTROL_LABELS = {
    "continue": "继续执行",
    "step-over": "单步越过",
    "step-into": "单步进入",
}


async def _export_browser_cookies(context: BrowserActionContext | None) -> None:
    """Persist browser cookies to a JSON file so the element picker can inject them.

    Uses storage_state() which returns plaintext-decrypted cookies — avoids the
    SafeStorage encryption mismatch between Playwright's Chromium and Electron's Chromium.
    """
    if context is None:
        return
    try:
        import json
        state = await context.browser.storage_state()
        cookies = state.get("cookies", [])
        if not cookies:
            return
        out = storage.resolve_browser_cookies_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
