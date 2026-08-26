from __future__ import annotations

import asyncio
import base64
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from app.core import storage
from app.models.schemas import ArtifactContent, ArtifactSnapshot, DebugControlCommand, QueueStats, RunConfigSnapshot, RunTaskRequest, RuntimeProgress, RuntimeVariableSnapshot, ScrapeResult, TaskLogEntry, TaskSnapshot, TaskStatus
from app.services.artifact_store import ArtifactStore, LocalArtifactStore
from app.services.browser_action_runner import BrowserActionContext, BrowserActionResult, BrowserActionRunner, OverlayInfo, apply_browser_result_variables, detect_blocking_overlay, is_browser_action_node, try_auto_dismiss_overlay
from app.services.browser_executor import BrowserExecutor
from app.services.control_action_runner import BreakLoopSignal, ControlActionRunner, apply_control_result_variables, is_control_action_node, is_human_takeover_node, is_subprocess_node
from app.services.extension_bridge_service import ExtensionBridgeService
from app.services.extension_executor import ExtensionExecutor
from app.services.execution_evidence import build_node_execution_evidence, definition_digest
from app.services.data_action_runner import DataActionRunner, apply_data_result_variables, is_data_action_node
from app.services.log_broker import LogBroker
from app.services.flow_definition import FlowDefinitionSelector
from app.services.flow_control import evaluate_condition_detail, is_condition_node, read_condition_expression, select_branch_edges
from app.services.flow_loop import (
    is_loop_node,
    is_repeat_until_node,
    materialize_loop_item,
    read_edge_target,
    read_loop_config,
    read_repeat_until_config,
    read_repeat_until_expression,
    split_loop_edges,
)
from app.services.file_action_runner import FileActionRunner, apply_file_result_variables, is_file_action_node
from app.services.http_action_runner import HttpActionRunner, apply_http_result_variables, is_http_action_node
from app.services.runtime_variables import RuntimeVariableStore, apply_fetch_result_variables
from app.services.scrapling_runner import RunnerProtocol
from app.services.script_action_runner import ScriptActionRunner, apply_script_result_variables, is_script_action_node
from app.services.task_store import InMemoryTaskStore, TaskStore
from app.services.task_queue import InMemoryTaskQueue, TaskQueue, TaskRunner
from app.services.variable_action_runner import VariableActionRunner, apply_variable_result_variables, is_variable_action_node


# AI 弹层分析置信度低于此阈值时，不用其结论覆盖启发式文案（宁可用不太具体的
# 兜底描述，也不要展示一个模型自己都不确定的猜测）。
_OVERLAY_ANALYSIS_CONFIDENCE_THRESHOLD = 0.5


def _timestamp_sort_key(value: datetime) -> float:
    """统一 naive/aware datetime，避免合并内存任务和持久化任务时排序崩溃。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _append_exc_context(exc: Exception, context_note: str) -> None:
    # 原地改写异常的 args 以附加排查线索（如失败时的页面 URL），调用方仍然
    # raise 同一个异常对象；改写失败（如 args 为空或不可变）时静默放弃，不能因为
    # 附加上下文本身出错而掩盖原始异常。
    try:
        base_message = str(exc.args[0]) if exc.args else str(exc)
        exc.args = (f"{base_message} {context_note}", *exc.args[1:])
    except Exception:
        pass


def _profile_owner_label(record: "TaskRecord") -> str:
    """占用方登记名要能让用户在界面上认出是哪一次运行，所以带流程名而不只是 task_id。"""
    name = (record.request.flow_name or "").strip() or "未命名流程"
    return f"{name} · 运行 {record.snapshot.task_id}"


def _resolve_output_slug(request: RunTaskRequest) -> str:
    """已保存流程用 flow_id 作产物目录键，避免重命名后产物拆到多个目录；
    未保存流程回退到流程名。"""
    if request.flow_id is not None and request.flow_id.strip():
        return storage.slugify(request.flow_id, fallback="flow")
    return storage.slugify(request.flow_name)


@dataclass
class TaskRecord:
    request: RunTaskRequest
    snapshot: TaskSnapshot
    executable_nodes: list[dict[str, object]] = field(default_factory=list)
    variables: RuntimeVariableStore = field(default_factory=RuntimeVariableStore)
    artifacts: list[ArtifactSnapshot] = field(default_factory=list)
    logs: list[TaskLogEntry] = field(default_factory=list)
    canceled: bool = False
    active_node_id: str | None = None
    debug_waiter: asyncio.Event = field(default_factory=asyncio.Event)
    debug_step_once: bool = False
    debug_resume_until_breakpoint: bool = False
    paused_node_id: str | None = None
    input_waiter: asyncio.Event = field(default_factory=asyncio.Event)
    input_prompt: str | None = None
    input_value: str = ""
    human_takeover_waiter: asyncio.Event = field(default_factory=asyncio.Event)
    human_takeover_message: str | None = None
    human_takeover_resume_mode: str = "next_node"
    # 惰性填充（见 _has_breakpoint），避免每次 debug step 都重建节点映射
    breakpoint_ids: frozenset[str] | None = field(default=None, init=False, repr=False, compare=False)


@dataclass
class FlowRunState:
    started: datetime
    total_steps: int
    results: list[ScrapeResult] = field(default_factory=list)
    browser_context: BrowserActionContext | None = None
    # fetch_attempts 只统计 browser.fetch 节点，executable_steps 统计所有已执行节点；
    # _run_flow_definition 结束时用两者区分"完全没有可执行节点" / "有节点跑了但没
    # fetch 结果（正常，比如纯控制流程）" / "fetch 节点都失败了" 三种收尾状态。
    fetch_attempts: int = 0
    executable_steps: int = 0


_SENTINEL = object()


class TaskManager:
    """协调任务生命周期：入队、执行、调试控制、结果持久化。是 HTTP API、任务队列、
    各 action runner 与日志中心之间的统一协调点。"""

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
        self._notifier: object | None = None
        self._overlay_analyzer: object | None = None
        self._extension_executor: ExtensionExecutor | None = None
        self._is_extension_enabled: Callable[[], bool] | None = None
        self._background_tasks: set[asyncio.Task] = set()

    def set_notifier(self, notifier: object) -> None:
        self._notifier = notifier

    def set_overlay_analyzer(self, analyzer: object) -> None:
        self._overlay_analyzer = analyzer

    def set_extension_bridge(self, bridge: ExtensionBridgeService, *, is_extension_enabled: Callable[[], bool]) -> None:
        """is_extension_enabled 是必传关键字参数：留默认值就等于默认放行，那样设置里关掉插件后，
        一条活着的 WebSocket 仍然能操作用户真实登录的浏览器。每次调用而不是缓存布尔值——开关可以在运行中被改。"""
        self._extension_executor = ExtensionExecutor(bridge=bridge)
        self._is_extension_enabled = is_extension_enabled

    def is_extension_enabled(self) -> bool:
        return self._is_extension_enabled is not None and self._is_extension_enabled()

    def is_extension_connected(self) -> bool:
        return self._extension_executor is not None and self._extension_executor.is_connected

    def _resolve_browser_executor(self, record: TaskRecord) -> BrowserExecutor:
        # 所有路由到插件执行器的路径（REST、AI 试跑、定时任务）都汇到这里，开关判定只放这一处。
        if record.request.browser_executor == "extension":
            if self._extension_executor is None:
                raise ConnectionError("插件执行器未启用：后端未注册 ExtensionBridgeService")
            if not self.is_extension_enabled():
                raise ConnectionError("插件执行器已在设置中关闭：请先在「设置 · 浏览器插件」里开启后再运行")
            return self._extension_executor
        return self._browser_action_runner

    def _spawn_background(self, coro: object) -> asyncio.Task:
        """asyncio 只持有 create_task() 结果的弱引用，没有强引用会在任务跑完前被 GC，
        故存入注册表直到完成。"""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def _notify_human_takeover(self, record: TaskRecord, *, node_title: str, message: str) -> None:
        # fire-and-forget：通知失败不应阻塞或中断流程执行
        if self._notifier is None:
            return
        self._spawn_background(
            self._notifier.notify_human_takeover(
                flow_name=record.snapshot.flow_name,
                node_title=node_title,
                message=message,
                task_id=record.snapshot.task_id,
            )
        )

    def start_workers(self) -> None:
        self._queue.start()

    async def stop_workers(self) -> None:
        await self._queue.stop()

    async def start_task(self, request: RunTaskRequest) -> TaskSnapshot:
        # TaskManager 只执行流程定义。flow_definition 在 schema 上仍是可选的，因为同一个
        # RunTaskRequest 也被当作定时任务的存量载荷持久化——那种载荷只带 flow_id，定义在触发时
        # 由 FlowRunner 现取，改成必填会让磁盘上已有的调度全部反序列化失败。所以门控放在执行
        # 入口：拿不到定义就直接拒，不建任务记录。
        if request.flow_definition is None:
            raise ValueError("缺少 flowDefinition：运行请求必须带流程定义，或改用 POST /api/flows/{flowId}/run 按已保存流程运行")
        executable_nodes = self._resolve_executable_nodes(request)
        task_id = f"t_{uuid4()}"
        now = datetime.now(UTC)
        # +2 为进度条预留"启动"与"保存结果"两个非节点步骤，与 _run_record/
        # _run_flow_definition 里重复计算的 total_steps 必须保持一致
        total_steps = max(len(executable_nodes), 1) + 2
        variables = RuntimeVariableStore.from_initial(
            request.variables,
            sensitive_names=set(request.sensitive_variables),
        )
        run_timestamp = now.astimezone().strftime("%Y%m%d_%H%M%S")
        flow_slug = _resolve_output_slug(request)
        variables.set("run_timestamp", run_timestamp, scope="全局")
        # 脚本/file.write 应写入 ${var.output_dir}/<name>.<ext>，保持产物隔离且可清理
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
            flowRevision=request.flow_revision,
            definitionDigest=request.definition_digest or definition_digest(request.flow_definition),
            acceptanceContract=request.acceptance_contract,
            executionEvidence=[],
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
        # 合并持久化快照与内存中活跃任务，确保调用方看到实时状态
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

    async def resume_human_takeover(self, task_id: str, resume_mode: str = "next_node") -> TaskSnapshot | None:
        record = self._tasks.get(task_id)
        if record is None or record.snapshot.status != "paused_for_human":
            return None
        record.human_takeover_resume_mode = resume_mode
        record.human_takeover_waiter.set()
        await self._update_snapshot(record, status="running", human_takeover_message=None, human_takeover_resume_mode=None)
        return record.snapshot

    async def stop_task(self, task_id: str) -> TaskSnapshot | None:
        record = self._tasks.get(task_id)
        if record is None:
            return None
        record.canceled = True
        record.input_waiter.set()  # unblock any waiting input node
        record.human_takeover_waiter.set()  # unblock any waiting human takeover node
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
            # detail 传 None：target_url 是遗留顶层字段，只作为各节点请求的兜底默认值，
            # 不代表这次运行真正访问的地址，展示出来会误导。
            await self._append_log(record, "info", f"任务启动 · {record.request.flow_name}", None, node_id="start")
            await self._append_log(record, "info", _build_run_config_message(record.request), None, node_id=record.request.start_node_id or "start")

            result = await self._run_flow_definition(record, started)

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
            pass  # 仅用于流程列表的"最近运行"展示；任务本身已经跑完，这里失败不应回抛

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
        human_takeover_message: str | None = _SENTINEL,
        human_takeover_resume_mode: str | None = _SENTINEL,
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
        if human_takeover_message is not _SENTINEL:
            update["human_takeover_message"] = human_takeover_message
        if human_takeover_resume_mode is not _SENTINEL:
            update["human_takeover_resume_mode"] = human_takeover_resume_mode
        record.snapshot = record.snapshot.model_copy(update=update)
        await self._task_store.save_task(record.snapshot, record.request)

    async def _append_log(self, record: TaskRecord, level: str, message: str, detail: str | None, *, node_id: str | None = None) -> None:
        entry = TaskLogEntry(task_id=record.snapshot.task_id, level=level, message=message, detail=detail, node_id=node_id)
        record.logs.append(entry)
        await self._task_store.append_log(entry)
        await self._broker.publish(entry)

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
            try:
                await self._resolve_browser_executor(record).close_context(state.browser_context)
            except Exception as exc:
                # finally 里抛出的异常会顶掉 try 里真正的失败原因。运行途中在设置里关掉插件开关
                # 就会走到这里：真正的节点报错会被改写成"插件已关闭"，用户照着去开开关也修不好。
                # 清理失败要留痕（上下文可能泄漏），但不能替换根因，也不能跳过下面的产物清理。
                await self._append_log(record, "warn", "浏览器上下文清理失败", str(exc), node_id=record.active_node_id or "end")
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

            if is_repeat_until_node(node):
                next_edges = await self._run_repeat_until_node(
                    record,
                    state,
                    loop_node_id=node_id,
                    node=node,
                    outgoing_edges=outgoing_edges,
                    node_by_id=node_by_id,
                    adjacency=adjacency,
                )
            elif is_loop_node(node):
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

    async def _run_repeat_until_node(
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
        """重复循环体直到退出条件成立——次数由运行时状态决定，而不是写死在流程里。

        退出条件在每轮**开始前**求值（while-not 语义）：目标状态已经达成时一次都不执行，
        避免「已经在目标月份还多翻一页」这类越过头的错误。
        """
        record.active_node_id = _read_node_id(node, fallback=loop_node_id)
        node_title = _read_node_title(node, fallback="重复直到")
        await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
        state.executable_steps += 1
        await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)

        config = read_repeat_until_config(node)
        expression = read_repeat_until_expression(node)
        condition_node = {**node, "condition": expression}
        body_edges, exit_edges = split_loop_edges(outgoing_edges, loop_node_id=loop_node_id, adjacency=adjacency)
        await self._append_log(
            record,
            "running",
            f"重复开始 · {node_title}",
            f"直到 {expression}（上限 {config.max_iterations} 轮）",
            node_id=record.active_node_id,
        )

        if not body_edges:
            await self._append_log(record, "warn", "重复节点缺少循环体出口", node_title, node_id=record.active_node_id)
            return exit_edges

        for index in range(config.max_iterations):
            if record.canceled:
                raise asyncio.CancelledError

            record.active_node_id = _read_node_id(node, fallback=loop_node_id)
            evaluation = evaluate_condition_detail(condition_node, record.variables)
            if evaluation.result:
                await self._append_log(
                    record,
                    "success",
                    f"重复完成 · {node_title}",
                    f"{index} 轮后满足 {evaluation.detail or expression}",
                    node_id=record.active_node_id,
                )
                return exit_edges

            record.variables.set(config.index_variable, index, scope="循环")
            await self._update_snapshot(record, variables=record.variables.snapshots())
            await self._append_log(
                record,
                "running",
                f"重复迭代 · {node_title} #{index + 1}",
                evaluation.detail or expression,
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
                        await self._append_log(record, "warn", f"重复中断 · {node_title}", expression, node_id=record.active_node_id)
                        return exit_edges

        record.active_node_id = _read_node_id(node, fallback=loop_node_id)
        # 跑满上限 = 退出条件始终没成立 = 目标状态没达成。默认让运行失败：
        # 静默继续会让后续节点在错误的页面状态上取数，且全程没有任何失败信号。
        message = f"重复达到上限 {config.max_iterations} 轮，退出条件仍未满足：{expression}"
        if config.fail_on_max:
            await self._append_log(record, "error", f"重复未达成 · {node_title}", message, node_id=record.active_node_id)
            raise RuntimeError(message)
        await self._append_log(record, "warn", f"重复达到上限 · {node_title}", message, node_id=record.active_node_id)
        return exit_edges

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
        before_variables = record.variables.raw_values()
        result_count_before = len(state.results)
        node_started = time.monotonic()
        node_type = node.get("type")
        record.active_node_id = _read_node_id(node, fallback="node")
        next_edges = outgoing_edges

        if _is_variable_node(node):
            node_title = _read_node_title(node, fallback="变量步骤")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            await self._run_variable_action_node(record, node, node_id=record.active_node_id, node_title=node_title)
        elif is_condition_node(node):
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
            if record.request.browser_executor == "extension":
                if state.browser_context is None:
                    state.browser_context = await self._resolve_browser_executor(record).create_context(headless=True, owner=_profile_owner_label(record))
                result = await self._run_fetch_node(
                    record,
                    step_request,
                    node=node,
                    node_id=record.active_node_id,
                    node_title=node_title,
                    context=state.browser_context,
                )
            else:
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
                needs_headed = any(is_human_takeover_node(n) for n in record.executable_nodes)
                state.browser_context = await self._resolve_browser_executor(record).create_context(headless=not needs_headed, owner=_profile_owner_label(record))
            browser_result = await self._run_browser_action_node(record, node, state.browser_context, node_id=record.active_node_id, node_title=node_title)
            if browser_result is not None and _is_collectable_result_node(node):
                state.results.append(browser_result)
        elif is_subprocess_node(node):
            node_title = _read_node_title(node, fallback=f"子流程 {state.executable_steps + 1}")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            await self._run_subprocess_node(record, state, node, node_id=record.active_node_id, node_title=node_title)
        elif is_human_takeover_node(node):
            node_title = _read_node_title(node, fallback=f"人工接管 {state.executable_steps + 1}")
            await self._pause_for_debug_if_needed(record, node_id=record.active_node_id, node_title=node_title)
            state.executable_steps += 1
            await self._update_step_progress(record, state.started, current_step=state.executable_steps, total_steps=state.total_steps)
            await self._run_human_takeover_node(record, node, state, node_id=record.active_node_id, node_title=node_title)
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

        node_results = state.results[result_count_before:]
        collectable_result = next((
            item for item in reversed(node_results)
            if isinstance(item, ScrapeResult | BrowserActionResult)
        ), None)
        evidence = build_node_execution_evidence(
            node,
            before_variables,
            record.variables,
            duration_ms=max(0, int((time.monotonic() - node_started) * 1000)),
            browser_url=(
                collectable_result.url
                if isinstance(collectable_result, ScrapeResult)
                else _get_browser_url(state)
            ),
            match_count=(
                collectable_result.count
                if isinstance(collectable_result, ScrapeResult)
                else len(collectable_result.values)
                if isinstance(collectable_result, BrowserActionResult)
                else None
            ),
        )
        record.snapshot = record.snapshot.model_copy(update={
            "execution_evidence": [*record.snapshot.execution_evidence, evidence],
        })
        await self._update_snapshot(record)
        return next_edges

    async def _run_human_takeover_node(
        self,
        record: TaskRecord,
        node: dict[str, object],
        state: FlowRunState,
        *,
        node_id: str,
        node_title: str,
    ) -> object:
        """暂停流程，等待用户完成手动操作后继续。"""
        from app.services.control_action_runner import ControlActionResult
        body = str(node.get("message") or node.get("humanTakeoverMessage") or node.get("description") or "")
        timeout_ms = int(node.get("timeoutMs") or 600_000)

        # Banner parseMessage expects: "{title}\n{body}\n⏱{timeoutMs}"
        if body:
            banner_message = f"{node_title}\n{body}\n⏱{timeout_ms}"
        else:
            banner_message = f"{node_title}\n⏱{timeout_ms}"

        browser_url = _get_browser_url(state)
        log_detail = f"{banner_message}\n{browser_url}" if browser_url else banner_message
        # 插件执行器的 context 是 ExtensionExecutionContext，没有 .page；用 getattr 兜底，
        # 否则人工接管节点在扩展执行器下会以 'object has no attribute page' 崩掉整个任务。
        page = getattr(state.browser_context, "page", None)
        completed = await self._wait_for_human(
            record,
            message=banner_message,
            timeout_seconds=timeout_ms / 1000,
            node_id=node_id,
            node_title=node_title,
            log_title=f"等待人工接管 · {node_title}",
            log_detail=log_detail,
            bring_to_front_page=page,
            on_pause=lambda: self._notify_human_takeover(record, node_title=node_title, message=banner_message),
        )
        if not completed:
            # 超时未完成人工操作不是流程缺陷，按"任务已停止"收尾而非报错，
            # 避免触发失败自愈诊断和误导性的错误统计。
            await self._append_log(
                record,
                "warn",
                f"人工接管超时（{timeout_ms // 1000}s），任务已停止 · {node_title}",
                "超时未完成人工操作。可在节点上调大 timeoutMs 后重新运行。",
                node_id=node_id,
            )
            raise asyncio.CancelledError

        resume_mode = record.human_takeover_resume_mode
        await self._append_log(record, "success", f"人工接管完成 · {node_title}", f"恢复模式: {resume_mode}", node_id=node_id)
        return ControlActionResult(action_type="control.human_takeover", detail=resume_mode, values=[resume_mode])

    async def _extension_show_takeover_banner(self, record: TaskRecord, message: str) -> None:
        """插件执行器下，人工接管提示要出现在用户正盯着的真实浏览器标签页里，
        而不是只出现在 Easy RPA 应用窗口——用户此时很可能根本没在看应用窗口。"""
        if record.request.browser_executor != "extension" or self._extension_executor is None:
            return
        try:
            await self._extension_executor.show_takeover_banner(record.snapshot.task_id, message)
        except Exception:
            pass

    async def _extension_hide_takeover_banner(self, record: TaskRecord) -> None:
        if record.request.browser_executor != "extension" or self._extension_executor is None:
            return
        try:
            await self._extension_executor.hide_takeover_banner()
        except Exception:
            pass

    async def _wait_for_human(
        self,
        record: TaskRecord,
        *,
        message: str,
        timeout_seconds: float,
        node_id: str,
        node_title: str,
        log_title: str,
        log_detail: str | None = None,
        bring_to_front_page: object | None = None,
        on_pause: Callable[[], None] | None = None,
    ) -> bool:
        """所有人工接管的共用等待通道：显式节点、敏感操作确认、验证码兜底都走这里。

        返回 True 表示用户完成并 resume；False 表示超时。用户中途 stop_task() 时抛
        CancelledError。三条路径此前各写一份，兜底路径漏掉了插件端横幅——统一后
        extension 执行器下任意暂停都会在用户真实盯着的标签页里弹出横幅。

        stop_task() 先 set() waiter 再 task.cancel()：waiter 对应的 future 已 done，
        协程在 wait_for 恢复时直接收 CancelledError，会跳过后续清理。用 try/finally
        保证插件横幅在完成、超时、被取消三种退出下都恰好隐藏一次。
        """
        record.human_takeover_message = message
        # 重建 Event 而非 clear()：复用可能已被上次 stop/resume set() 过的实例会导致 wait() 不挂起
        record.human_takeover_waiter = asyncio.Event()
        await self._update_snapshot(
            record, status="paused_for_human", human_takeover_message=message, human_takeover_resume_mode=None
        )
        await self._append_log(record, "input", log_title, log_detail if log_detail is not None else message, node_id=node_id)
        if on_pause is not None:
            on_pause()
        if bring_to_front_page is not None:
            try:
                await bring_to_front_page.bring_to_front()  # type: ignore[attr-defined]
            except Exception:
                pass
        await self._extension_show_takeover_banner(record, message)
        try:
            try:
                await asyncio.wait_for(record.human_takeover_waiter.wait(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                record.human_takeover_message = None
                await self._update_snapshot(record, human_takeover_message=None)
                return False
            if record.canceled:
                raise asyncio.CancelledError
            record.human_takeover_message = None
            return True
        finally:
            await self._extension_hide_takeover_banner(record)

    async def _maybe_confirm_sensitive_action(
        self, record: TaskRecord, node: dict[str, object], *, node_id: str, node_title: str
    ) -> None:
        """插件执行器操作的是用户真实登录态的浏览器（可能是转账、发起支付等敏感页面）。
        节点上显式打了 requireConfirmation 标记的，执行前先暂停等人工点"确认"——
        复用人工接管的等待/Banner 机制，不重复造一套新的暂停通道。"""
        if record.request.browser_executor != "extension" or self._extension_executor is None:
            return
        if node.get("requireConfirmation") is not True:
            return

        confirm_timeout_seconds = 120  # 2 分钟：给用户看清敏感操作详情再点确认的合理时长
        detail = _read_node_browser_detail(node) or ""
        message = f"{node_title}\n即将执行敏感操作：{node.get('type')} {detail}\n请确认后继续\n⏱{confirm_timeout_seconds * 1000}"
        completed = await self._wait_for_human(
            record,
            message=message,
            timeout_seconds=confirm_timeout_seconds,
            node_id=node_id,
            node_title=node_title,
            log_title=f"等待人工确认 · {node_title}",
        )
        if not completed:
            await self._append_log(record, "warn", f"人工确认超时，节点已中止 · {node_title}", None, node_id=node_id)
            raise asyncio.CancelledError

        await self._update_snapshot(record, status="running", human_takeover_message=None)
        await self._append_log(record, "success", f"人工已确认 · {node_title}", None, node_id=node_id)

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
        # "retry" 策略固定只重试一次（共 2 次尝试），不是可配置的重试次数
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
        context: BrowserActionContext | None = None,
    ) -> ScrapeResult | None:
        attempt_count = 0

        async def _execute():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count > 1:
                await self._append_log(record, "warn", f"重试节点 · {node_title}", request.selector, node_id=node_id)
            if record.request.browser_executor == "extension":
                return await self._run_extension_fetch_node(record, request, context, node_id=node_id, node_title=node_title)
            return await self._runner.run(
                record.snapshot.task_id,
                request,
                lambda level, message, detail=None, node_id=node_id: self._append_log(record, level, message, detail, node_id=node_id),
            )

        return await self._run_with_retry(record, node, node_id=node_id, node_title=node_title, label="节点", execute=_execute)

    async def _run_extension_fetch_node(
        self,
        record: TaskRecord,
        request: RunTaskRequest,
        context: BrowserActionContext | None,
        *,
        node_id: str,
        node_title: str,
    ) -> ScrapeResult:
        executor = self._resolve_browser_executor(record)
        owns_context = context is None
        active_context = context
        if active_context is None:
            active_context = await executor.create_context(headless=True, owner=_profile_owner_label(record))
        try:
            if request.target_url is not None:
                await self._append_log(record, "running", f"扩展打开网页 · {node_title}", str(request.target_url), node_id=node_id)
                await executor.run(
                    {"type": "browser.open", "targetUrl": str(request.target_url)},
                    record.variables,
                    active_context,
                    timeout_ms=request.timeout_ms,
                )
            await self._append_log(record, "running", f"扩展采集页面 · {node_title}", request.selector, node_id=node_id)
            extract_node: dict[str, object] = {
                "type": "browser.extract",
                "selector": request.selector,
                "extractMode": request.extract_mode,
            }
            if request.attribute is not None:
                extract_node["attribute"] = request.attribute
            browser_result = await executor.run(extract_node, record.variables, active_context, timeout_ms=request.timeout_ms)
            await self._append_log(record, "success", f"扩展采集完成 · {node_title}", browser_result.detail, node_id=node_id)
            return browser_result.to_scrape_result()
        finally:
            if owns_context:
                await executor.close_context(active_context)

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
        state: FlowRunState | None = None,
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
                variable_result = await self._run_user_input_node(record, resolved_node, state, node_id=node_id, node_title=node_title)
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
        state: FlowRunState | None,
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
        browser_url = _get_browser_url(state)
        detail = f"{prompt}\n{browser_url}" if browser_url else prompt
        await self._append_log(record, "input", f"等待用户输入 · {node_title}", detail, node_id=node_id)

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
            await self._maybe_confirm_sensitive_action(record, resolved_node, node_id=node_id, node_title=node_title)
            browser_result = await self._resolve_browser_executor(record).run(resolved_node, record.variables, context, timeout_ms=_read_node_timeout(resolved_node, default=record.request.timeout_ms))
            await self._append_log(record, "success", f"浏览器动作完成 · {node_title}", browser_result.detail, node_id=node_id)
            saved_names = apply_browser_result_variables(node, browser_result, record.variables)
            if saved_names:
                await self._append_log(record, "success", f"浏览器输出变量已更新 · {', '.join(saved_names)}", None, node_id=node_id)
                await self._update_snapshot(record, variables=record.variables.snapshots())
            await self._save_browser_screenshot(record, context, node_id=node_id, node_title=node_title)
            return browser_result.to_scrape_result()

        try:
            return await self._run_with_retry(record, node, node_id=node_id, node_title=node_title, label="浏览器动作", execute=_execute)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if await self._handle_browser_node_failure(record, context, exc, node=node, node_id=node_id, node_title=node_title):
                # 人工处理完阻断浮层后重试本节点一次。
                return await self._run_with_retry(record, node, node_id=node_id, node_title=node_title, label="浏览器动作", execute=_execute)
            raise

    async def _handle_browser_node_failure(
        self,
        record: TaskRecord,
        context: BrowserActionContext,
        exc: Exception,
        *,
        node: dict[str, object],
        node_id: str,
        node_title: str,
    ) -> bool:
        """浏览器节点最终失败时的现场处理。

        广告/隐私条款提示这类安全类别会先尝试自动关闭；验证码/未知弹层永远跳过自动关闭，
        直接转人工。返回 True 表示浮层已被自动关闭或人工已处理，调用方应重试该节点；
        返回 False 表示应继续抛出原始异常（此时已尽力留存失败现场证据）。
        """
        if record.canceled:
            return False

        if not hasattr(context, "page"):
            # 插件执行器驱动的是用户真实 Chrome 标签页，没有 Playwright page 句柄。
            # 阻断浮层检测/自动关闭依赖 Playwright DOM 与截图能力，这里只做统一失败留证，
            # 避免把原始节点错误覆盖成 "'ExtensionExecutionContext' object has no attribute 'page'"。
            await self._capture_failure_evidence(record, context, exc, node_id=node_id, node_title=node_title)
            return False

        target_selector = _read_optional_str(node, "selector")
        overlay = await detect_blocking_overlay(context.page, target_selector)
        if overlay is not None:
            if await self._try_auto_dismiss_overlay(record, context, overlay, target_selector, node_id=node_id, node_title=node_title):
                return True
            if context.headless:
                # 无头模式用户看不到浏览器，无法人工处理——补充错误上下文后按失败处理。
                _append_exc_context(exc, f"[检测到{overlay.label}，但当前为无头运行无法人工处理。{overlay.headless_advice}]")
            else:
                if await self._pause_for_runtime_overlay(record, context, overlay, node_id=node_id, node_title=node_title):
                    return True
                _append_exc_context(exc, f"[检测到{overlay.label}，等待人工处理超时]")

        await self._capture_failure_evidence(record, context, exc, node_id=node_id, node_title=node_title)
        return False

    async def _try_auto_dismiss_overlay(
        self,
        record: TaskRecord,
        context: BrowserActionContext,
        overlay: OverlayInfo,
        target_selector: str | None,
        *,
        node_id: str,
        node_title: str,
    ) -> bool:
        """对安全类别（广告/隐私条款提示）浮层尝试自动点击关闭，成功且复检确认消失才返回 True。

        验证码/未知弹层不会走到这里——安全类别的把关在 try_auto_dismiss_overlay 内部完成。
        """
        try:
            outcome = await try_auto_dismiss_overlay(context.page, overlay, target_selector)
        except Exception:
            outcome = None
        if outcome is None:
            return False

        await self._append_log(
            record, "success", f"自动关闭{overlay.label} · {node_title}", f"点击了「{outcome.button_text}」", node_id=node_id
        )
        try:
            still_present = await detect_blocking_overlay(context.page, target_selector)
        except Exception:
            still_present = overlay
        if still_present is not None:
            await self._append_log(record, "warn", f"自动关闭{overlay.label}未生效，转入人工处理 · {node_title}", None, node_id=node_id)
            return False

        await self._append_log(record, "success", f"{overlay.label}已自动关闭，重试节点 · {node_title}", None, node_id=node_id)
        return True

    async def _pause_for_runtime_overlay(
        self,
        record: TaskRecord,
        context: BrowserActionContext,
        overlay: OverlayInfo,
        *,
        node_id: str,
        node_title: str,
    ) -> bool:
        """运行时检测到阻断型浮层（验证码/广告/弹层等）：即使流程里没有 human_takeover 节点也转入人工等待。"""
        timeout_ms = 600_000
        banner_message = (
            f"检测到{overlay.label}\n"
            f"页面出现{overlay.label}，自动化无法通过。请在浏览器窗口中手动处理。\n"
            f"⏱{timeout_ms}"
        )
        completed = await self._wait_for_human(
            record,
            message=banner_message,
            timeout_seconds=timeout_ms / 1000,
            node_id=node_id,
            node_title=node_title,
            log_title=f"检测到{overlay.label}，等待人工完成 · {node_title}",
            bring_to_front_page=context.page,
            on_pause=lambda: self._enrich_overlay_and_notify(
                record, context, overlay, node_id=node_id, node_title=node_title, timeout_ms=timeout_ms, fallback_message=banner_message
            ),
        )
        # 超时和完成都回到 running：兜底暂停不终止任务，交由调用方决定重试/继续
        await self._update_snapshot(record, status="running", human_takeover_message=None)
        if not completed:
            return False
        await self._append_log(record, "success", f"人工验证完成，重试节点 · {node_title}", None, node_id=node_id)
        return True

    def _enrich_overlay_and_notify(
        self,
        record: TaskRecord,
        context: BrowserActionContext,
        overlay: OverlayInfo,
        *,
        node_id: str,
        node_title: str,
        timeout_ms: int,
        fallback_message: str,
    ) -> None:
        """Fire-and-forget：先用 AI 分析弹层具体原因，再发通知；分析失败/超时则直接用启发式文案发通知。"""
        self._spawn_background(
            self._run_overlay_enrichment(
                record, context, overlay, node_id=node_id, node_title=node_title, timeout_ms=timeout_ms, fallback_message=fallback_message
            )
        )

    async def _run_overlay_enrichment(
        self,
        record: TaskRecord,
        context: BrowserActionContext,
        overlay: OverlayInfo,
        *,
        node_id: str,
        node_title: str,
        timeout_ms: int,
        fallback_message: str,
    ) -> None:
        analysis = None
        screenshot_b64: str | None = None
        try:
            content = await context.page.screenshot(type="jpeg", quality=60, full_page=False)
            screenshot_b64 = base64.b64encode(content).decode("ascii")
        except Exception:
            pass

        if self._overlay_analyzer is not None:
            try:
                analysis = await self._overlay_analyzer.analyze(overlay.summary, screenshot_b64=screenshot_b64)
            except Exception:
                analysis = None

        message = fallback_message
        # 只有人工还没恢复、消息还没被其它状态变化覆盖时，才回填 AI 增强内容；
        # 置信度过低的猜测不如启发式文案可靠，直接丢弃。
        if (
            analysis is not None
            and analysis.confidence >= _OVERLAY_ANALYSIS_CONFIDENCE_THRESHOLD
            and record.human_takeover_message == fallback_message
        ):
            hint_line = f"建议：{analysis.human_action_hint}\n" if analysis.human_action_hint else ""
            enriched_message = (
                f"检测到{overlay.label}\n"
                f"{analysis.reason or ('页面出现' + overlay.label + '，自动化无法通过。')}\n"
                f"{hint_line}"
                f"⏱{timeout_ms}"
            )
            record.human_takeover_message = enriched_message
            await self._update_snapshot(record, human_takeover_message=enriched_message)
            await self._append_log(record, "input", f"AI 分析弹层原因 · {node_title}", enriched_message, node_id=node_id)
            message = enriched_message

        self._notify_human_takeover(record, node_title=node_title, message=message)

    async def _capture_failure_evidence(
        self,
        record: TaskRecord,
        context: BrowserActionContext,
        exc: Exception,
        *,
        node_id: str,
        node_title: str,
    ) -> None:
        """失败瞬间抓取截图与页面 URL，供 get_run_error 提供视觉证据。Best-effort。"""
        try:
            page = getattr(context, "page", None)
            page_url = getattr(page, "url", "") if page is not None else ""
            if page is not None:
                content = await page.screenshot(type="jpeg", quality=60, full_page=False)
                filename = f"failure_{_safe_artifact_name(node_id)}.jpg"
                content_type = "image/jpeg"
            else:
                content = await self._resolve_browser_executor(record).screenshot(context)
                filename = f"failure_{_safe_artifact_name(node_id)}.png"
                content_type = "image/png"
            artifact = await self._artifact_store.save_bytes(
                task_id=record.snapshot.task_id,
                artifact_type="screenshot",
                filename=filename,
                content=content,
                content_type=content_type,
                metadata={
                    "flow_name": record.request.flow_name,
                    "node_id": node_id,
                    "node_title": node_title,
                    "failure_evidence": True,
                    "page_url": page_url,
                    "error": str(exc)[:500],
                },
                flow_id=_resolve_output_slug(record.request),
            )
            record.artifacts.append(artifact)
            await self._update_snapshot(record, artifacts=record.artifacts)
            await self._append_log(record, "warn", f"失败现场已留存 · {node_title}", f"{page_url}\n{artifact.storage_url}", node_id=node_id)
            if page_url:
                _append_exc_context(exc, f"[失败时页面: {page_url}]")
        except Exception:
            pass

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
            # 512 字符是启发式上限：真实文件路径不会这么长，用它快速排除脚本
            # 把大段文本/JSON 打到 stdout 的情况，避免误当路径去做文件系统探测
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
        try:
            content = await self._resolve_browser_executor(record).screenshot(context)
        except NotImplementedError:
            # 插件执行器暂不支持截图（backlog #12），跳过而不是让整个节点失败。
            return
        except Exception as exc:
            # 截图是 best-effort：captureVisibleTab 超配额等瞬时失败不应把已经
            # 成功的浏览器动作节点判定为失败，降级为 warning 日志即可。
            await self._append_log(
                record,
                "warn",
                f"截图跳过 · {node_title}",
                str(exc),
                node_id=node_id,
            )
            return
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
                return  # 超过 10MB 静默跳过，避免大文件把 artifact 存储撑爆
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
    structured_values: list[object] = []
    has_structured = False
    for result in results:
        values.extend(result.values)
        if result.structured is not None:
            has_structured = True
            structured_values.extend(result.structured)
        else:
            # 用普通 values 回填，保持 structured_values 与合并后的 values 索引对齐，
            # 即便这一批结果本身没有 structured 数据
            structured_values.extend(result.values)

    return ScrapeResult(
        url=", ".join(dict.fromkeys(result.url for result in results)),
        selector=", ".join(result.selector for result in results),
        count=sum(result.count for result in results),
        values=values,
        structured=structured_values if has_structured else None,
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
        # 显式指定了起点但在当前定义里找不到时直接返回 None（触发"缺少起始节点"报错），
        # 不会静默回退到默认起点——避免调用方以为按指定节点跑了实际却跑错了地方
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
    # stack 是后进先出，倒序 push 才能让 edges 里排在前面的目标先被弹出/先执行，
    # 与 edges 声明顺序保持一致
    for edge in reversed(edges):
        target = edge.get("target")
        if isinstance(target, str) and target not in visited and target not in stops:
            stack.append(target)


def _resolve_node_variables(node: dict[str, object], variables: RuntimeVariableStore) -> dict[str, object]:
    resolved = dict(node)
    # 显式白名单而非遍历全部字符串字段：像 id/type 这类结构性字段绝不能被
    # ${var} 替换，否则会破坏节点识别或改变节点行为
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
    # bool 是 int 的子类，isinstance(True, int) 为真，故需显式排除，否则 True/False
    # 会被当成 1/0 毫秒超时
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _read_optional_str(node: dict[str, object], key: str) -> str | None:
    value = node.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


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
        "variable.set": "变量已更新",
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


def _get_browser_url(state: "FlowRunState | None") -> str | None:
    """Return the current Playwright page URL from the run state, or None if unavailable."""
    if state is None or state.browser_context is None:
        return None
    try:
        url = getattr(state.browser_context.page, "url", None)
        if isinstance(url, str) and url not in ("", "about:blank"):
            return url
    except Exception:
        pass
    return None
