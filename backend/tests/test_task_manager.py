from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from app.models.schemas import RunTaskRequest, RuntimeProgress, ScrapeResult, TaskSnapshot
from app.services import browser_profile_lock
from app.services.artifact_store import LocalArtifactStore
from app.services.file_action_runner import FileActionRunner
from app.services.log_broker import LogBroker
from app.services.scrapling_runner import LogCallback
from app.services.script_action_runner import ScriptActionRunner
from app.services.task_manager import TaskManager
from app.services.task_store import InMemoryTaskStore


class LocalApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        payload = json.loads(body)
        response = {
            "path": self.path,
            "name": payload["name"],
            "trace": self.headers.get("x-trace-id"),
        }
        self._send_json(201, response)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalApiServer:
    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), LocalApiHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> "LocalApiServer":
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


class FakeRunner:
    async def run(self, task_id: str, request: RunTaskRequest, on_log: LogCallback) -> ScrapeResult:
        await on_log("running", "模拟采集中", request.selector)
        await asyncio.sleep(0)
        return ScrapeResult(url=str(request.target_url), selector=request.selector, count=1, values=["hello"])


class RecordingRunner:
    def __init__(self) -> None:
        self.requests: list[RunTaskRequest] = []

    async def run(self, task_id: str, request: RunTaskRequest, on_log: LogCallback) -> ScrapeResult:
        self.requests.append(request)
        await on_log("running", "按节点采集中", request.selector)
        await asyncio.sleep(0)
        return ScrapeResult(url=str(request.target_url), selector=request.selector, count=1, values=[request.selector])


class FailingFirstRunner:
    def __init__(self) -> None:
        self.requests: list[RunTaskRequest] = []

    async def run(self, task_id: str, request: RunTaskRequest, on_log: LogCallback) -> ScrapeResult:
        self.requests.append(request)
        await asyncio.sleep(0)
        if request.selector == ".first::text":
            raise RuntimeError("first failed")
        await on_log("running", "失败后继续采集", request.selector)
        return ScrapeResult(url=str(request.target_url), selector=request.selector, count=1, values=["second"])


class AlwaysFailingRunner:
    async def run(self, task_id: str, request: RunTaskRequest, on_log: LogCallback) -> ScrapeResult:
        await asyncio.sleep(0)
        raise RuntimeError("upstream fetch failed")


async def test_terminal_status_is_persisted_after_final_log(tmp_path) -> None:
    class ObservedStore(InMemoryTaskStore):
        def __init__(self) -> None:
            super().__init__()
            self.terminal_log_seen: list[bool] = []

        async def save_task(self, task: TaskSnapshot, request: RunTaskRequest) -> TaskSnapshot:
            if task.status in {"success", "error"}:
                logs = await self.list_logs(task.task_id) or []
                expected = "任务完成" if task.status == "success" else "任务失败"
                self.terminal_log_seen.append(any(log.message == expected for log in logs))
            return await super().save_task(task, request)

    for runner, status in ((FakeRunner(), "success"), (AlwaysFailingRunner(), "error")):
        store = ObservedStore()
        manager = TaskManager(
            runner=runner,
            broker=LogBroker(),
            artifact_store=LocalArtifactStore(artifact_root=tmp_path),
            task_store=store,
        )
        try:
            task = await manager.start_task(RunTaskRequest(
                flowName="日志提交顺序",
                targetUrl="https://example.com/",
                selector=".item::text",
                flowDefinition={
                    "nodes": [{"id": "fetch", "type": "browser.fetch", "targetUrl": "https://example.com/", "selector": ".item::text"}],
                    "edges": [],
                },
            ))
            await wait_for_status(manager, task.task_id, {status})
            assert store.terminal_log_seen == [True]
        finally:
            await manager.stop_workers()


class SlowRunner:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.release = asyncio.Event()

    async def run(self, task_id: str, request: RunTaskRequest, on_log: LogCallback) -> ScrapeResult:
        self.started.append(task_id)
        await on_log("running", "等待释放", None)
        await self.release.wait()
        return ScrapeResult(url=str(request.target_url), selector=request.selector, count=1, values=[task_id])


class FakeBrowserActionRunner:
    def __init__(self, extract_values_by_selector: dict[str, list[str]] | None = None) -> None:
        self.actions: list[dict[str, object]] = []
        self._extract_values_by_selector = extract_values_by_selector or {}

    async def create_context(self, *, headless: bool = True, owner: str | None = None) -> object:
        return object()

    async def close_context(self, context: object | None) -> None:
        return None

    async def screenshot(self, context: object) -> bytes:
        return b"\x89PNG\r\n\x1a\nfake-png"

    async def run(self, node: dict[str, object], variables, context: object, *, timeout_ms: int):
        from app.services.browser_action_runner import BrowserActionResult

        self.actions.append(dict(node))
        action_type = str(node["type"])
        if action_type in {"browser.extract", "ui.extract"}:
            selector = str(node["selector"])
            return BrowserActionResult(action_type=action_type, detail=selector, values=self._extract_values_by_selector.get(selector, ["提交成功"]))
        return BrowserActionResult(action_type=action_type, detail=str(node.get("selector", node.get("targetUrl", ""))), values=[action_type])


class FakeExtensionExecutor(FakeBrowserActionRunner):
    def __init__(self, extract_values_by_selector: dict[str, list[str]] | None = None) -> None:
        super().__init__(extract_values_by_selector)
        # 记录敏感操作确认横幅的显示/隐藏。
        self.banner_calls: list[tuple[str, object]] = []

    @property
    def is_connected(self) -> bool:
        return True

    async def show_confirmation_banner(self, task_id: str, message: str) -> None:
        self.banner_calls.append(("show", message))

    async def hide_confirmation_banner(self) -> None:
        self.banner_calls.append(("hide", None))

    async def run(self, node: dict[str, object], variables, context: object, *, timeout_ms: int):
        from app.services.browser_action_runner import BrowserActionResult

        self.actions.append(dict(node))
        action_type = str(node["type"])
        if action_type == "browser.extract":
            selector = str(node["selector"])
            rows = self._extract_values_by_selector.get(selector, [{"姓名": "张三", "金额": "100"}])
            values = [json.dumps(row, ensure_ascii=False) if isinstance(row, dict) else str(row) for row in rows]
            return BrowserActionResult(action_type=action_type, detail=selector, values=values, structured=list(rows))
        return BrowserActionResult(action_type=action_type, detail=str(node.get("selector", node.get("targetUrl", ""))), values=[action_type])


class ScreenshotQuotaExceededBrowserActionRunner(FakeBrowserActionRunner):
    """screenshot() 模拟 captureVisibleTab 超配额报错，用于验证截图失败降级为 best-effort。"""

    async def screenshot(self, context: object) -> bytes:
        raise RuntimeError("MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND quota exceeded")


class FakeOverlayPage:
    """probe_results 供 detect_blocking_overlay 依次消费（探测脚本用非 dict 参数调用）；
    耗尽后返回 None（视为浮层已消失）。dismiss_result 供 try_auto_dismiss_overlay 消费
    （关闭脚本用 dict 参数调用），按 evaluate 调用参数类型区分两者。"""

    def __init__(self, probe_results: dict[str, object] | None | list[object], dismiss_result: dict[str, object] | None = None) -> None:
        if isinstance(probe_results, list):
            self._probe_queue: list[object] | None = list(probe_results)
            self._fixed_probe_result = None
        else:
            self._probe_queue = None
            self._fixed_probe_result = probe_results
        self.dismiss_result = dismiss_result
        self.probe_calls: list[object] = []
        self.dismiss_calls: list[object] = []
        self.brought_to_front = False

    async def evaluate(self, script: str, arg: object = None) -> object:
        if isinstance(arg, dict):
            self.dismiss_calls.append(arg)
            return self.dismiss_result
        self.probe_calls.append(arg)
        if self._probe_queue is not None:
            return self._probe_queue.pop(0) if self._probe_queue else None
        return self._fixed_probe_result

    async def bring_to_front(self) -> None:
        self.brought_to_front = True

    async def screenshot(self, **kwargs: object) -> bytes:
        return b"\x89PNG\r\n\x1a\nfake-png"


class FakeOverlayContext:
    def __init__(self, page: FakeOverlayPage, *, headless: bool) -> None:
        self.playwright: object = None
        self.browser: object = None
        self.page = page
        self.persistent = False
        self.headless = headless


class OverlayBrowserActionRunner(FakeBrowserActionRunner):
    """模拟浏览器动作节点在指定 node 上首次失败，用于驱动 detect_blocking_overlay 路径。"""

    def __init__(
        self,
        *,
        overlay_result: dict[str, object] | None | list[object],
        headless: bool = False,
        fail_node_id: str = "click",
        dismiss_result: dict[str, object] | None = None,
    ) -> None:
        super().__init__()
        self.page = FakeOverlayPage(overlay_result, dismiss_result)
        self._headless = headless
        self._fail_node_id = fail_node_id
        self._call_counts: dict[str, int] = {}

    async def create_context(self, *, headless: bool = True, owner: str | None = None) -> object:
        return FakeOverlayContext(self.page, headless=self._headless)

    async def run(self, node: dict[str, object], variables, context: object, *, timeout_ms: int):
        from app.services.browser_action_runner import BrowserActionResult

        self.actions.append(dict(node))
        node_id = str(node.get("id"))
        self._call_counts[node_id] = self._call_counts.get(node_id, 0) + 1
        action_type = str(node["type"])
        if node_id == self._fail_node_id and self._call_counts[node_id] == 1:
            raise RuntimeError("目标元素被遮挡，点击超时")
        return BrowserActionResult(action_type=action_type, detail=str(node.get("selector", "")), values=[action_type])


_OVERLAY_RESULT_SLIDER_CAPTCHA: dict[str, object] = {
    "reason": "target-obscured",
    "vendor": None,
    "tag": "div",
    "id": "captcha-mask",
    "className": "verify-mask",
    "text": "请完成验证后继续 拖动滑块完成拼图",
    "interactive": [],
    "hasIframe": False,
}


def _overlay_flow_definition() -> dict[str, object]:
    return {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "click", "title": "点击登录", "type": "browser.click", "selector": "#submit"},
        ],
        "edges": [{"source": "start", "target": "click"}],
    }


async def test_task_manager_reports_runtime_overlay_without_pausing(tmp_path) -> None:
    fake_browser = OverlayBrowserActionRunner(overlay_result=_OVERLAY_RESULT_SLIDER_CAPTCHA, headless=False)
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="遮挡弹层流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition=_overlay_flow_definition(),
        )
    )

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error"})
    assert done.status == "error"
    assert [action["id"] for action in fake_browser.actions] == ["click"]
    assert not fake_browser.page.brought_to_front
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any("运行时无法可靠接管" in (log.detail or "") for log in logs)


async def test_task_manager_confirms_sensitive_extension_action(tmp_path) -> None:
    fake_extension = FakeExtensionExecutor()
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._extension_executor = fake_extension  # type: ignore[assignment]
    manager._is_extension_enabled = lambda: True  # type: ignore[assignment]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="敏感操作确认流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            browserExecutor="extension",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "submit", "title": "提交支付", "type": "browser.click", "selector": "#submit", "requireConfirmation": True},
                ],
                "edges": [
                    {"source": "start", "target": "submit"},
                ],
            },
        )
    )

    paused = await wait_for_status(manager, snapshot.task_id, {"awaiting_confirmation"})
    assert paused.confirmation_message is not None
    assert fake_extension.banner_calls[0][0] == "show"
    assert "即将执行敏感操作" in str(fake_extension.banner_calls[0][1])

    resumed = await manager.resume_confirmation(snapshot.task_id)
    assert resumed is not None

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error"})
    assert done.status == "success"
    assert [action["id"] for action in fake_extension.actions] == ["submit"]
    assert fake_extension.banner_calls[-1] == ("hide", None)


class FailOnceExtensionExecutor(FakeExtensionExecutor):
    """首次调用失败、之后成功。用于证明"已确认动作失败即停、绝不重跑"。"""

    def __init__(self) -> None:
        super().__init__()
        self.run_attempts = 0

    async def run(self, node, variables, context, *, timeout_ms):
        self.run_attempts += 1
        if self.run_attempts == 1:
            raise RuntimeError("首次点击瞬时失败")
        return await super().run(node, variables, context, timeout_ms=timeout_ms)


def _single_confirmation_flow() -> dict[str, object]:
    return {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "submit", "title": "提交支付", "type": "browser.click", "selector": "#submit", "requireConfirmation": True},
        ],
        "edges": [{"source": "start", "target": "submit"}],
    }


def _confirmation_then_next_flow() -> dict[str, object]:
    return {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "submit", "title": "提交支付", "type": "browser.click", "selector": "#submit", "requireConfirmation": True},
            {"id": "verify", "title": "读取回执", "type": "browser.extract", "selector": "#receipt"},
        ],
        "edges": [
            {"source": "start", "target": "submit"},
            {"source": "submit", "target": "verify"},
        ],
    }


def _confirming_manager(executor: FakeExtensionExecutor, tmp_path) -> TaskManager:
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._extension_executor = executor  # type: ignore[assignment]
    manager._is_extension_enabled = lambda: True  # type: ignore[assignment]
    return manager


@pytest.mark.parametrize("failure_strategy", ["retry", "continue"])
async def test_confirmed_sensitive_action_runs_once_and_stops_on_failure(tmp_path, failure_strategy) -> None:
    """已确认的敏感动作一次确认最多一次实际调用：即便 failureStrategy=retry/continue，
    首次失败也不得重跑或静默继续，任务落 error，后续节点不执行，并留下失败现场。"""
    fake_extension = FailOnceExtensionExecutor()
    manager = _confirming_manager(fake_extension, tmp_path)

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="敏感操作失败即停流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            browserExecutor="extension",
            failureStrategy=failure_strategy,
            flowDefinition=_confirmation_then_next_flow(),
        )
    )

    await wait_for_status(manager, snapshot.task_id, {"awaiting_confirmation"})
    assert await manager.resume_confirmation(snapshot.task_id) is not None

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error", "stopped"})
    assert done.status == "error"
    # 首次调用即抛错（FailOnce 在失败分支未记录 action），run_attempts==1 直接证明：
    # 确认后只调用一次，retry 不重跑、continue 不吞错继续；verify 若执行计数会变 2。
    assert fake_extension.run_attempts == 1
    assert all(action["id"] != "verify" for action in fake_extension.actions)
    assert [call[0] for call in fake_extension.banner_calls].count("show") == 1
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any("按确认契约不重试" in log.message for log in logs)


async def test_confirmed_sensitive_action_continues_to_next_node_on_success(tmp_path) -> None:
    """确认成功后正常执行本节点并继续后续节点。"""
    fake_extension = FakeExtensionExecutor()
    manager = _confirming_manager(fake_extension, tmp_path)

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="确认后继续流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            browserExecutor="extension",
            flowDefinition=_confirmation_then_next_flow(),
        )
    )

    await wait_for_status(manager, snapshot.task_id, {"awaiting_confirmation"})
    assert await manager.resume_confirmation(snapshot.task_id) is not None

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error", "stopped"})
    assert done.status == "success"
    assert [action["id"] for action in fake_extension.actions] == ["submit", "verify"]
    assert [call[0] for call in fake_extension.banner_calls].count("show") == 1


async def test_confirmation_timeout_stops_task_without_running_action(tmp_path) -> None:
    """确认超时：动作从不执行，任务落 stopped（不确定时停止并留证）。"""
    fake_extension = FakeExtensionExecutor()
    manager = _confirming_manager(fake_extension, tmp_path)
    manager._confirm_timeout_seconds = 0.05  # 缩短仅为覆盖超时分支

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="确认超时流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            browserExecutor="extension",
            failureStrategy="retry",
            flowDefinition=_single_confirmation_flow(),
        )
    )

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error", "stopped"})
    assert done.status == "stopped"
    assert fake_extension.actions == []  # 敏感动作从未真正调用
    assert [call[0] for call in fake_extension.banner_calls].count("show") == 1


async def test_confirmation_cancel_propagates_and_stops(tmp_path) -> None:
    """awaiting_confirmation 期间取消：取消异常传播，任务落 stopped，动作不执行。"""
    fake_extension = FakeExtensionExecutor()
    manager = _confirming_manager(fake_extension, tmp_path)

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="确认中取消流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            browserExecutor="extension",
            flowDefinition=_single_confirmation_flow(),
        )
    )

    await wait_for_status(manager, snapshot.task_id, {"awaiting_confirmation"})
    await manager.stop_task(snapshot.task_id)

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error", "stopped"})
    assert done.status == "stopped"
    assert fake_extension.actions == []


async def test_reconcile_interrupted_awaiting_confirmation_after_restart(tmp_path) -> None:
    """重启对账：持久化的 awaiting_confirmation 在新进程既无内存 record 也无确认通道，
    必须被落成 stopped 并留痕，且 /resume 明确失效，避免 UI 显示"可继续"却接不上。"""
    store = InMemoryTaskStore()
    request = RunTaskRequest(
        flowName="重启前待确认流程",
        targetUrl="https://example.com/fallback",
        selector=".fallback::text",
        browserExecutor="extension",
        flowDefinition=_single_confirmation_flow(),
    )
    now = datetime.now(UTC)
    stale = TaskSnapshot(
        task_id="t_stale_await",
        flow_name="重启前待确认流程",
        status="awaiting_confirmation",
        mode="run",
        progress=RuntimeProgress(current_step=1, total_steps=3, percent=10, elapsed_ms=0),
        created_at=now,
        updated_at=now,
        confirmation_message="即将执行敏感操作：browser.click #submit",
    )
    await store.save_task(stale, request)

    # 模拟重启：全新 TaskManager，_tasks 为空，仅共享持久化 store
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path), task_store=store)
    assert await manager.reconcile_interrupted_tasks() == 1

    snap = await manager.get_task("t_stale_await")
    assert snap is not None and snap.status == "stopped"
    assert snap.error and "重启" in snap.error
    assert snap.confirmation_message is None
    assert await manager.resume_confirmation("t_stale_await") is None  # 死状态不可继续
    logs = await manager.get_logs("t_stale_await")
    assert logs is not None and any("任务已终止" in log.message for log in logs)

    # 已终态的任务不再被二次对账
    assert await manager.reconcile_interrupted_tasks() == 0



async def test_resume_confirmation_is_noop_after_active_cleared(tmp_path) -> None:
    """超时清位与 resume 抢跑：confirmation_active 清零后 resume 不得把任务翻回 running。"""
    fake_extension = FakeExtensionExecutor()
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._extension_executor = fake_extension  # type: ignore[assignment]
    manager._is_extension_enabled = lambda: True  # type: ignore[assignment]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="确认竞态流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            browserExecutor="extension",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "submit", "title": "提交支付", "type": "browser.click", "selector": "#submit", "requireConfirmation": True},
                ],
                "edges": [{"source": "start", "target": "submit"}],
            },
        )
    )

    await wait_for_status(manager, snapshot.task_id, {"awaiting_confirmation"})
    # 模拟超时分支已清位（发生在其落终态的 await 之前）
    manager._tasks[snapshot.task_id].confirmation_active = False

    assert await manager.resume_confirmation(snapshot.task_id) is None
    still = await manager.get_task(snapshot.task_id)
    assert still is not None and still.status == "awaiting_confirmation"

    await manager.stop_task(snapshot.task_id)  # 收尾：让挂起的确认等待退出，避免事件循环拆除时挂死
    assert await manager.resume_confirmation(snapshot.task_id) is None


async def test_task_manager_propagates_failure_when_no_overlay_detected(tmp_path) -> None:
    fake_browser = OverlayBrowserActionRunner(overlay_result=None, headless=False)
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="无遮挡失败流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition=_overlay_flow_definition(),
        )
    )

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error"})
    assert done.status == "error"
    assert [action["id"] for action in fake_browser.actions] == ["click"]
    assert not fake_browser.page.brought_to_front


async def test_task_manager_annotates_overlay_error_when_headless(tmp_path) -> None:
    fake_browser = OverlayBrowserActionRunner(overlay_result=_OVERLAY_RESULT_SLIDER_CAPTCHA, headless=True)
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="无头遮挡流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition=_overlay_flow_definition(),
        )
    )

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error"})
    assert done.status == "error"
    assert done.confirmation_message is None
    assert not fake_browser.page.brought_to_front
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any("运行时无法可靠接管" in (log.detail or "") for log in logs)


_OVERLAY_RESULT_AD_POPUP: dict[str, object] = {
    "reason": "fullscreen_overlay",
    "vendor": None,
    "tag": "div",
    "id": "ad-modal",
    "className": "promo-modal",
    "text": "限时优惠，立即领取红包",
    "interactive": [{"tag": "button", "text": "关闭"}],
    "hasIframe": False,
}

_OVERLAY_RESULT_PRIVACY_CONSENT: dict[str, object] = {
    "reason": "fullscreen_overlay",
    "vendor": None,
    "tag": "div",
    "id": "cookie-banner",
    "className": "consent-banner",
    "text": "我们使用 cookie 以改善您的体验，请同意我们的隐私条款",
    "interactive": [{"tag": "button", "text": "同意"}],
    "hasIframe": False,
}


async def test_task_manager_auto_dismisses_ad_popup_without_pausing(tmp_path) -> None:
    fake_browser = OverlayBrowserActionRunner(
        overlay_result=[_OVERLAY_RESULT_AD_POPUP, None],
        dismiss_result={"clicked": True, "category": "close", "buttonText": "关闭"},
        headless=False,
    )
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="广告弹窗自动关闭流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition=_overlay_flow_definition(),
        )
    )

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error", "awaiting_confirmation"})
    assert done.status == "success"
    assert [action["id"] for action in fake_browser.actions] == ["click", "click"]
    assert not fake_browser.page.brought_to_front
    dismiss_call = fake_browser.page.dismiss_calls[0]
    assert dismiss_call["allowConsent"] is False
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any("已自动关闭" in log.message for log in logs)


async def test_task_manager_auto_dismisses_privacy_consent_popup(tmp_path) -> None:
    fake_browser = OverlayBrowserActionRunner(
        overlay_result=[_OVERLAY_RESULT_PRIVACY_CONSENT, None],
        dismiss_result={"clicked": True, "category": "consent", "buttonText": "同意"},
        headless=False,
    )
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="隐私条款自动关闭流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition=_overlay_flow_definition(),
        )
    )

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error", "awaiting_confirmation"})
    assert done.status == "success"
    dismiss_call = fake_browser.page.dismiss_calls[0]
    assert dismiss_call["allowConsent"] is True


async def test_task_manager_fails_when_auto_dismiss_does_not_stick(tmp_path) -> None:
    fake_browser = OverlayBrowserActionRunner(
        overlay_result=[_OVERLAY_RESULT_AD_POPUP, _OVERLAY_RESULT_AD_POPUP],
        dismiss_result={"clicked": True, "category": "close", "buttonText": "关闭"},
        headless=False,
    )
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="关闭未生效流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition=_overlay_flow_definition(),
        )
    )

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error"})
    assert done.status == "error"
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any("自动关闭疑似广告弹窗未生效" in log.message for log in logs)


async def test_task_manager_never_auto_dismisses_captcha_overlay(tmp_path) -> None:
    fake_browser = OverlayBrowserActionRunner(overlay_result=_OVERLAY_RESULT_SLIDER_CAPTCHA, headless=False)
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="验证码不自动关闭流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition=_overlay_flow_definition(),
        )
    )

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error"})
    assert done.status == "error"
    assert fake_browser.page.dismiss_calls == []


async def test_task_manager_auto_dismisses_ad_popup_even_when_headless(tmp_path) -> None:
    fake_browser = OverlayBrowserActionRunner(
        overlay_result=[_OVERLAY_RESULT_AD_POPUP, None],
        dismiss_result={"clicked": True, "category": "close", "buttonText": "关闭"},
        headless=True,
    )
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="无头广告自动关闭流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition=_overlay_flow_definition(),
        )
    )

    done = await wait_for_status(manager, snapshot.task_id, {"success", "error"})
    assert done.status == "success"


async def test_task_manager_runs_task_to_success(tmp_path) -> None:
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="测试流程",
            targetUrl="https://example.com/",
            selector="h1::text",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "fetch", "type": "browser.fetch", "targetUrl": "https://example.com/", "selector": "h1::text"},
                ],
                "edges": [{"source": "start", "target": "fetch"}],
            },
        )
    )

    for _ in range(20):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "success":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "success"
    assert current.result is not None
    assert current.result.values == ["hello"]
    assert len(current.artifacts) == 1
    assert current.artifacts[0].artifact_type == "dataset"
    assert current.artifacts[0].size_bytes > 0
    assert (tmp_path / "测试流程" / snapshot.task_id / "artifacts" / "scrape-result.json").exists()
    assert len(await manager.get_artifacts(snapshot.task_id) or []) == 1
    artifact_content = await manager.get_artifact_content(snapshot.task_id, current.artifacts[0].artifact_id)
    assert artifact_content is not None
    assert '"values"' in artifact_content.content
    assert len(await manager.get_logs(snapshot.task_id) or []) >= 3


async def test_task_manager_uses_flow_id_for_stable_output_variables(tmp_path) -> None:
    fake_browser = FakeBrowserActionRunner()
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]
    flow_id = "2b36c13c-d502-4937-b97a-1e8c513f1c3f"

    async def run_flow(flow_name: str) -> dict[str, str]:
        snapshot = await manager.start_task(
            RunTaskRequest(
                flowId=flow_id,
                flowName=flow_name,
                targetUrl="https://example.com/fallback",
                selector=".fallback::text",
                flowDefinition={
                    "nodes": [
                        {"id": "start", "type": "start"},
                            {
                                "id": "read_output_vars",
                                "title": "读取产物变量",
                                "type": "variable.log",
                                "message": "${var.flow_slug}|${var.output_dir}|${var.output_prefix}",
                                "outputVariable": "output_vars",
                            },
                    ],
                    "edges": [{"source": "start", "target": "read_output_vars"}],
                },
            )
        )
        current = await wait_for_status(manager, snapshot.task_id, {"success"})
        variables = {variable.name: variable.value for variable in current.variables}
        return {
            "task_id": current.task_id,
            "flow_slug": variables["flow_slug"],
            "output_dir": variables["output_dir"],
            "output_prefix": variables["output_prefix"],
        }

    first = await run_flow("示例合约列表抓取")
    second = await run_flow("示例-合约列表抓取")

    assert first["flow_slug"] == flow_id
    assert second["flow_slug"] == flow_id
    assert first["output_dir"] == f"runs/{flow_id}/{first['task_id']}"
    assert second["output_dir"] == f"runs/{flow_id}/{second['task_id']}"
    assert first["output_prefix"].startswith(f"runs/{flow_id}/{first['task_id']}/")
    assert second["output_prefix"].startswith(f"runs/{flow_id}/{second['task_id']}/")
    assert first["output_dir"] != second["output_dir"]


async def test_task_manager_runs_flow_definition_fetch_nodes_in_order(tmp_path) -> None:
    runner = RecordingRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="多节点流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "first",
                        "title": "采集标题",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/first",
                        "selector": ".first::text",
                        "fetcher": "static",
                        "extractMode": "text",
                    },
                    {"id": "disabled", "type": "control.condition", "disabled": True},
                    {
                        "id": "second",
                        "title": "采集作者",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/second",
                        "selector": ".second::text",
                        "fetcher": "static",
                        "extractMode": "text",
                    },
                    {
                        "id": "detached",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/detached",
                        "selector": ".detached::text",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "first"},
                    {"source": "first", "target": "disabled"},
                    {"source": "disabled", "target": "second"},
                ],
            },
        )
    )

    for _ in range(20):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "success":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "success"
    assert current.result is not None
    assert [request.selector for request in runner.requests] == [".first::text", ".second::text"]
    assert current.result.selector == ".first::text, .second::text"
    assert current.result.count == 2
    assert current.result.values == [".first::text", ".second::text"]

    artifacts = await manager.get_artifacts(snapshot.task_id)
    assert artifacts is not None
    assert artifacts[0].metadata["selector"] == ".first::text, .second::text"
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "first" and "采集标题" in log.message for log in logs)
    assert any(log.node_id == "second" and "采集作者" in log.message for log in logs)


async def test_task_manager_routes_fetch_node_to_extension_and_keeps_structured_rows(tmp_path) -> None:
    runner = RecordingRunner()
    fake_extension = FakeExtensionExecutor({"table.orders": [{"姓名": "张三", "金额": "100"}, {"姓名": "李四", "金额": "200"}]})
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._extension_executor = fake_extension  # type: ignore[assignment]
    manager._is_extension_enabled = lambda: True  # type: ignore[assignment]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="插件表格采集",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            browserExecutor="extension",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "fetch-table",
                        "title": "采集订单表",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/orders",
                        "selector": "table.orders",
                        "extractMode": "table",
                        "outputVariable": "orders",
                        "firstValueVariable": "first_order",
                    },
                ],
                "edges": [{"source": "start", "target": "fetch-table"}],
            },
        )
    )

    current = await wait_for_status(manager, snapshot.task_id, {"success"})

    assert runner.requests == []
    assert [action["type"] for action in fake_extension.actions] == ["browser.open", "browser.extract"]
    # 带 id：这两个节点是取数路径临时拼的，不带的话桥接审计日志只剩运行标签、认不出是哪个节点动的手
    assert fake_extension.actions[-1] == {
        "type": "browser.extract", "id": "fetch-table", "selector": "table.orders", "extractMode": "table"
    }
    assert current.result is not None
    assert current.result.values == ['{"姓名": "张三", "金额": "100"}', '{"姓名": "李四", "金额": "200"}']
    variables = {variable.name: variable.value for variable in current.variables}
    assert variables["orders"] == '[{"姓名": "张三", "金额": "100"}, {"姓名": "李四", "金额": "200"}]'
    assert variables["first_order"] == '{"姓名": "张三", "金额": "100"}'


async def test_task_manager_refuses_extension_executor_when_disabled_in_settings(tmp_path) -> None:
    """设置里关掉插件后，一条活着的 WebSocket 不能再成为操作用户真实登录浏览器的入口。

    这是所有路由到插件执行器的路径（REST / AI 试跑 / 定时任务）共用的唯一闸门。
    """
    runner = RecordingRunner()
    fake_extension = FakeExtensionExecutor({".target::text": ["不该被抓到"]})
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._extension_executor = fake_extension  # type: ignore[assignment]
    manager._is_extension_enabled = lambda: False  # type: ignore[assignment]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="插件被关闭",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            browserExecutor="extension",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "fetch",
                        "title": "采集订单",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/orders",
                        "selector": ".target::text",
                    },
                ],
                "edges": [{"source": "start", "target": "fetch"}],
            },
        )
    )
    current = await wait_for_status(manager, snapshot.task_id, {"error"})

    assert fake_extension.actions == []
    assert runner.requests == []
    assert current.error is not None
    assert "关闭" in current.error


async def test_task_manager_rejects_run_request_without_flow_definition(tmp_path) -> None:
    """TaskManager 只执行流程定义：没有 flowDefinition 的请求当场拒掉，不建任务记录。

    以前这种请求会从遗留顶层字段拼一个临时节点直接跑 Playwright，而且那条路径绕过了执行器选择——
    选了插件也照样静默换成 Playwright，抓的是没有登录态的页面，交出空数据而不是报错。
    """
    runner = RecordingRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))

    with pytest.raises(ValueError, match="flowDefinition"):
        await manager.start_task(
            RunTaskRequest(
                flowName="缺少流程定义",
                targetUrl="https://example.com/orders",
                selector=".target::text",
                browserExecutor="extension",
            )
        )

    assert runner.requests == []
    assert await manager.list_tasks() == []


async def test_task_manager_excludes_auxiliary_browser_extract_from_final_result(tmp_path) -> None:
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = FakeBrowserActionRunner(
        {
            "input[type='password']": ["password"],
            ".table-row": ["row-1", "row-2"],
        }
    )
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="辅助抽取流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "check-login",
                        "type": "browser.extract",
                        "selector": "input[type='password']",
                        "includeInResult": False,
                        "countVariable": "login_count",
                    },
                    {
                        "id": "extract-table",
                        "type": "browser.extract",
                        "selector": ".table-row",
                        "outputVariable": "rows",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "check-login"},
                    {"source": "check-login", "target": "extract-table"},
                ],
            },
        )
    )

    for _ in range(20):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "success":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "success"
    assert current.result is not None
    assert current.result.selector == "browser.extract"
    assert current.result.count == 2
    assert current.result.values == ["row-1", "row-2"]
    variables = {variable.name: variable for variable in current.variables}
    assert variables["login_count"].value == "1"
    assert variables["rows"].value == '["row-1", "row-2"]'


async def test_task_manager_continues_after_failed_fetch_node_when_configured(tmp_path) -> None:
    runner = FailingFirstRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="失败继续流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            failureStrategy="continue",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "first",
                        "title": "失败节点",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/first",
                        "selector": ".first::text",
                    },
                    {
                        "id": "second",
                        "title": "成功节点",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/second",
                        "selector": ".second::text",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "first"},
                    {"source": "first", "target": "second"},
                ],
            },
        )
    )

    for _ in range(20):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "success":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "success"
    assert current.result is not None
    assert [request.selector for request in runner.requests] == [".first::text", ".second::text"]
    assert current.result.selector == ".second::text"
    assert current.result.values == ["second"]
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "first" and "继续执行" in log.message for log in logs)


async def test_task_manager_preserves_fetch_node_error_when_stop_strategy(tmp_path) -> None:
    manager = TaskManager(runner=AlwaysFailingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="fetch 失败流程",
            targetUrl="https://quotes.toscrape.com/",
            selector=".quote .text::text",
            failureStrategy="stop",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "n1",
                        "title": "采集步骤 1",
                        "type": "browser.fetch",
                        "targetUrl": "https://quotes.toscrape.com/",
                        "selector": ".quote .text::text",
                    },
                ],
                "edges": [{"source": "start", "target": "n1"}],
            },
        )
    )

    for _ in range(20):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "error":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "error"
    assert current.error == "upstream fetch failed"
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "n1" and log.detail == "upstream fetch failed" for log in logs)
    assert not any(log.detail == "name 'node' is not defined" for log in logs)


async def test_startup_failure_leaves_task_error_node_empty(tmp_path) -> None:
    # 首个节点执行前失败（这里是空定义），任务级"任务失败"日志必须留空节点，
    # 不能硬塞 n1——否则 get_run_error 会把启动阶段的错误反推成第一个节点的锅。
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="空定义流程",
            targetUrl="https://example.com/",
            selector=".x::text",
            flowDefinition={"nodes": [], "edges": []},
        )
    )

    for _ in range(20):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "error":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "error"
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    failure_logs = [log for log in logs if log.message == "任务失败"]
    assert failure_logs, "应记录任务失败日志"
    assert all(log.node_id is None for log in failure_logs)
    assert not any(log.node_id == "n1" for log in logs)


async def test_task_manager_resolves_runtime_variables_between_nodes(tmp_path) -> None:
    runner = RecordingRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="变量流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            variables={"base_url": "https://example.com"},
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "set-selector",
                        "title": "设置选择器",
                        "type": "variable.set",
                        "variableName": "selector_name",
                        "value": ".first::text",
                        "scope": "全局",
                    },
                    {
                        "id": "first",
                        "title": "采集标题",
                        "type": "browser.fetch",
                        "targetUrl": "${var.base_url}/first",
                        "selector": "${var.selector_name}",
                        "outputVariable": "first_values",
                        "countVariable": "first_count",
                        "firstValueVariable": "first_value",
                    },
                    {
                        "id": "second",
                        "title": "复用结果变量",
                        "type": "browser.fetch",
                        "targetUrl": "${var.base_url}/second",
                        "selector": "${var.first_value}",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "set-selector"},
                    {"source": "set-selector", "target": "first"},
                    {"source": "first", "target": "second"},
                ],
            },
        )
    )

    for _ in range(20):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "success":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "success"
    assert current.result is not None
    assert [(str(request.target_url), request.selector) for request in runner.requests] == [
        ("https://example.com/first", ".first::text"),
        ("https://example.com/second", ".first::text"),
    ]
    variables = {variable.name: variable for variable in current.variables}
    assert variables["base_url"].value == "https://example.com"
    assert variables["selector_name"].value == ".first::text"
    assert variables["first_count"].value == "1"
    assert variables["first_value"].value == ".first::text"
    assert variables["first_values"].type == "List"
    assert variables["first_values"].value == '[".first::text"]'

    artifacts = await manager.get_artifacts(snapshot.task_id)
    assert artifacts is not None
    artifact_content = await manager.get_artifact_content(snapshot.task_id, artifacts[0].artifact_id)
    assert artifact_content is not None
    assert '"variables"' in artifact_content.content


async def test_task_manager_selects_true_condition_branch(tmp_path) -> None:
    runner = RecordingRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="条件真分支流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            variables={"row_count": 2},
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "guard", "title": "判断是否有数据", "type": "control.condition", "description": "row_count > 0"},
                    {
                        "id": "yes",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/yes",
                        "selector": ".yes::text",
                    },
                    {
                        "id": "no",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/no",
                        "selector": ".no::text",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "guard"},
                    {"source": "guard", "target": "yes", "label": "是"},
                    {"source": "guard", "target": "no", "label": "否"},
                ],
            },
        )
    )

    for _ in range(20):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "success":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "success"
    assert [request.selector for request in runner.requests] == [".yes::text"]
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "guard" and "→ 是" in log.message for log in logs)


async def test_task_manager_selects_false_condition_branch_from_variable_value(tmp_path) -> None:
    runner = RecordingRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="条件假分支流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            variables={"row_count": 0},
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "guard", "type": "control.condition", "condition": "row_count > 0"},
                    {
                        "id": "yes",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/yes",
                        "selector": ".yes::text",
                    },
                    {
                        "id": "no",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/no",
                        "selector": ".no::text",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "guard"},
                    {"source": "guard", "target": "yes", "sourceHandle": "true"},
                    {"source": "guard", "target": "no", "sourceHandle": "false"},
                ],
            },
        )
    )

    for _ in range(20):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "success":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "success"
    assert [request.selector for request in runner.requests] == [".no::text"]
    assert current.result is not None
    assert current.result.selector == ".no::text"


async def test_task_manager_selected_only_does_not_follow_condition_edges(tmp_path) -> None:
    runner = RecordingRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="仅运行条件节点",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            scope="selected-only",
            startNodeId="guard",
            variables={"ready": True},
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "guard", "type": "control.condition", "condition": "ready"},
                    {"id": "yes", "type": "browser.fetch", "targetUrl": "https://example.com/yes", "selector": ".yes::text"},
                ],
                "edges": [
                    {"source": "start", "target": "guard"},
                    {"source": "guard", "target": "yes", "label": "是"},
                ],
            },
        )
    )

    for _ in range(20):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "success":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "success"
    assert runner.requests == []
    assert current.result is not None
    assert current.result.count == 0


async def test_task_manager_runs_http_request_node_and_writes_variables(tmp_path) -> None:
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    with LocalApiServer() as server:
        snapshot = await manager.start_task(
            RunTaskRequest(
                flowName="HTTP 节点流程",
                targetUrl="https://example.com/fallback",
                selector=".fallback::text",
                variables={"api_base": server.url, "username": "alice", "trace_id": "trace-42"},
                flowDefinition={
                    "nodes": [
                        {"id": "start", "type": "start"},
                        {
                            "id": "api",
                            "title": "创建订单",
                            "type": "http.request",
                            "method": "POST",
                            "url": "${var.api_base}/orders",
                            "headers": {"content-type": "application/json", "x-trace-id": "${var.trace_id}"},
                            "requestBody": '{"name":"${var.username}"}',
                            "responseVariable": "api_response",
                            "statusVariable": "api_status",
                            "jsonVariable": "api_json",
                        },
                    ],
                    "edges": [{"source": "start", "target": "api"}],
                },
            )
        )

        for _ in range(20):
            current = await manager.get_task(snapshot.task_id)
            assert current is not None
            if current.status == "success":
                break
            await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "success"
    assert current.result is not None
    assert current.result.selector == "POST 201"
    assert current.result.count == 1
    assert '"name": "alice"' in current.result.values[0]
    variables = {variable.name: variable for variable in current.variables}
    assert variables["api_status"].value == "201"
    assert variables["api_response"].type == "String"
    assert variables["api_json"].type == "Dict"
    assert '"trace": "trace-42"' in variables["api_response"].value
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "api" and "HTTP 请求完成" in log.message for log in logs)


async def test_task_manager_runs_browser_action_nodes_and_writes_variables(tmp_path) -> None:
    fake_browser = FakeBrowserActionRunner()
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="浏览器动作流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            variables={"username": "alice"},
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "open", "title": "打开页面", "type": "browser.open", "targetUrl": "https://example.com/login"},
                    {"id": "fill", "title": "输入用户名", "type": "browser.fill", "selector": "#username", "inputValue": "${var.username}"},
                    {"id": "click", "title": "点击提交", "type": "browser.click", "selector": "#submit"},
                    {
                        "id": "extract",
                        "title": "获取结果",
                        "type": "browser.extract",
                        "selector": ".result",
                        "outputVariable": "browser_texts",
                        "firstValueVariable": "browser_text",
                        "countVariable": "browser_count",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "open"},
                    {"source": "open", "target": "fill"},
                    {"source": "fill", "target": "click"},
                    {"source": "click", "target": "extract"},
                ],
            },
        )
    )

    for _ in range(20):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "success":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "success"
    assert [action["type"] for action in fake_browser.actions] == ["browser.open", "browser.fill", "browser.click", "browser.extract"]
    assert fake_browser.actions[1]["inputValue"] == "alice"
    variables = {variable.name: variable for variable in current.variables}
    assert variables["browser_text"].value == "提交成功"
    assert variables["browser_count"].value == "1"
    assert variables["browser_texts"].type == "List"
    screenshots = [artifact for artifact in current.artifacts if artifact.artifact_type == "screenshot"]
    assert [artifact.metadata["node_id"] for artifact in screenshots] == ["open", "fill", "click", "extract"]
    screenshot_content = await manager.get_artifact_content(snapshot.task_id, screenshots[-1].artifact_id)
    assert screenshot_content is not None
    assert screenshot_content.content.startswith("data:image/png;base64,")
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "extract" and "浏览器动作完成" in log.message for log in logs)


async def test_screenshot_failure_degrades_to_best_effort_without_failing_node(tmp_path) -> None:
    fake_browser = ScreenshotQuotaExceededBrowserActionRunner()
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="截图配额超限流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "open", "title": "打开页面", "type": "browser.open", "targetUrl": "https://example.com/login"},
                ],
                "edges": [{"source": "start", "target": "open"}],
            },
        )
    )

    for _ in range(20):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "success":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "success"
    screenshots = [artifact for artifact in current.artifacts if artifact.artifact_type == "screenshot"]
    assert screenshots == []
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "open" and log.level == "warn" and "截图跳过" in log.message for log in logs)


async def test_task_manager_runs_browser_extract_links_foreach_detail_flow(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fake_browser = FakeBrowserActionRunner(
        {
            "a.article-link": ["https://example.com/a", "https://example.com/b"],
            "article": ["详情正文"],
        }
    )
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]
    manager._file_action_runner = FileActionRunner(workspace)  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="列表详情链路",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "open-list", "title": "打开列表页", "type": "browser.open", "targetUrl": "https://example.com/articles"},
                    {
                        "id": "extract-links",
                        "title": "提取详情链接",
                        "type": "browser.extract",
                        "selector": "a.article-link",
                        "extractMode": "attribute",
                        "attribute": "href",
                        "outputVariable": "detail_links",
                        "firstValueVariable": "first_detail_link",
                        "countVariable": "detail_link_count",
                    },
                    {
                        "id": "foreach",
                        "title": "遍历详情链接",
                        "type": "control.foreach",
                        "itemsVariable": "detail_links",
                        "itemVariable": "detail_url",
                        "indexVariable": "detail_index",
                        "maxIterations": 10,
                    },
                    {"id": "open-detail", "title": "打开详情页", "type": "browser.open", "targetUrl": "${var.detail_url}"},
                    {
                        "id": "extract-detail",
                        "title": "提取详情正文",
                        "type": "browser.extract",
                        "selector": "article",
                        "outputVariable": "detail_texts",
                        "firstValueVariable": "last_detail_text",
                    },
                    {
                        "id": "write",
                        "title": "写入最后详情",
                        "type": "file.write",
                        "path": "last-detail.txt",
                        "content": "${var.detail_index}:${var.detail_url}\n${var.last_detail_text}",
                        "outputVariable": "detail_report_path",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "open-list"},
                    {"source": "open-list", "target": "extract-links"},
                    {"source": "extract-links", "target": "foreach"},
                    {"source": "foreach", "target": "open-detail", "label": "循环体"},
                    {"source": "open-detail", "target": "extract-detail"},
                    {"source": "extract-detail", "target": "foreach"},
                    {"source": "foreach", "target": "write", "label": "完成"},
                ],
            },
        )
    )

    current = await wait_for_status(manager, snapshot.task_id, {"success"})
    assert current.status == "success"
    assert [action["type"] for action in fake_browser.actions] == [
        "browser.open",
        "browser.extract",
        "browser.open",
        "browser.extract",
        "browser.open",
        "browser.extract",
    ]
    assert fake_browser.actions[2]["targetUrl"] == "https://example.com/a"
    assert fake_browser.actions[4]["targetUrl"] == "https://example.com/b"
    assert (workspace / "last-detail.txt").read_text(encoding="utf-8") == "1:https://example.com/b\n详情正文"
    variables = {variable.name: variable for variable in current.variables}
    assert variables["detail_link_count"].value == "2"
    assert variables["first_detail_link"].value == "https://example.com/a"
    assert variables["detail_url"].value == "https://example.com/b"
    assert variables["last_detail_text"].value == "详情正文"


async def test_task_manager_runs_excel_and_file_nodes_with_variables(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "orders.csv").write_text("order_id,total\nA001,42\nA002,64\n", encoding="utf-8")
    runner = RecordingRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._file_action_runner = FileActionRunner(workspace)  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="Excel 文件流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "read-orders",
                        "title": "读取订单 CSV",
                        "type": "excel.read",
                        "path": "orders.csv",
                        "column": "order_id",
                        "outputVariable": "order_ids",
                        "firstValueVariable": "first_order_id",
                        "countVariable": "row_count",
                    },
                    {"id": "guard", "type": "control.condition", "condition": "row_count > 0"},
                    {
                        "id": "write-report",
                        "title": "写入报告",
                        "type": "file.write",
                        "path": "report.txt",
                        "content": "first=${var.first_order_id}; rows=${var.row_count}",
                        "outputVariable": "report_path",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "read-orders"},
                    {"source": "read-orders", "target": "guard"},
                    {"source": "guard", "target": "write-report", "label": "是"},
                ],
            },
        )
    )

    for _ in range(30):
        current = await manager.get_task(snapshot.task_id)
        assert current is not None
        if current.status == "success":
            break
        await asyncio.sleep(0.01)

    current = await manager.get_task(snapshot.task_id)
    assert current is not None
    assert current.status == "success"
    assert (workspace / "report.txt").read_text(encoding="utf-8") == "first=A001; rows=2"
    variables = {variable.name: variable for variable in current.variables}
    assert variables["row_count"].value == "2"
    assert variables["first_order_id"].value == "A001"
    assert variables["report_path"].type == "List"
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "read-orders" and "文件节点完成" in log.message for log in logs)
    assert any(log.node_id == "guard" and "→ 是" in log.message for log in logs)


async def test_task_manager_runs_file_list_and_copy_nodes(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "input.txt").write_text("done", encoding="utf-8")
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._file_action_runner = FileActionRunner(workspace)  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="文件目录流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "list",
                        "title": "遍历文件夹",
                        "type": "file.list",
                        "path": ".",
                        "pattern": "*.txt",
                        "outputVariable": "file_paths",
                        "countVariable": "file_count",
                    },
                    {
                        "id": "copy",
                        "title": "复制文件",
                        "type": "file.copy",
                        "path": "input.txt",
                        "targetPath": "archive/input.txt",
                        "outputVariable": "copied_path",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "list"},
                    {"source": "list", "target": "copy"},
                ],
            },
        )
    )

    current = await wait_for_status(manager, snapshot.task_id, {"success"})
    assert current.status == "success"
    assert (workspace / "archive/input.txt").read_text(encoding="utf-8") == "done"
    variables = {variable.name: variable for variable in current.variables}
    assert variables["file_count"].value == "1"
    assert variables["file_paths"].value == '["input.txt"]'
    assert variables["copied_path"].type == "List"
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "list" and "文件节点完成" in log.message for log in logs)


async def test_task_manager_runs_foreach_loop_body_with_current_item(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "orders.csv").write_text("order_id,total\nA001,42\nA002,64\n", encoding="utf-8")
    runner = RecordingRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._file_action_runner = FileActionRunner(workspace)  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="循环处理 CSV",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "read-orders",
                        "title": "读取订单 CSV",
                        "type": "excel.read",
                        "path": "orders.csv",
                        "outputVariable": "excel_rows",
                        "countVariable": "row_count",
                    },
                    {
                        "id": "foreach",
                        "title": "遍历每一行",
                        "type": "control.foreach",
                        "itemsVariable": "excel_rows",
                        "itemVariable": "current_row",
                        "indexVariable": "loop_index",
                        "maxIterations": 10,
                    },
                    {
                        "id": "fetch",
                        "title": "按订单采集",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/order/${var.current_row.order_id}",
                        "selector": ".order-${var.loop_index}::text",
                        "appendVariable": "all_order_details",
                        "appendMode": "record",
                    },
                    {
                        "id": "write-last",
                        "title": "写入最后订单",
                        "type": "file.write",
                        "path": "last-order.txt",
                        "content": "${var.current_row.order_id}:${var.loop_index}",
                        "outputVariable": "last_report_path",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "read-orders"},
                    {"source": "read-orders", "target": "foreach"},
                    {"source": "foreach", "target": "fetch", "label": "循环体"},
                    {"source": "fetch", "target": "foreach"},
                    {"source": "foreach", "target": "write-last", "label": "完成"},
                ],
            },
        )
    )

    current = await wait_for_status(manager, snapshot.task_id, {"success"})
    assert current.status == "success"
    assert [str(request.target_url) for request in runner.requests] == [
        "https://example.com/order/A001",
        "https://example.com/order/A002",
    ]
    assert [request.selector for request in runner.requests] == [".order-0::text", ".order-1::text"]
    assert (workspace / "last-order.txt").read_text(encoding="utf-8") == "A002:1"
    variables = {variable.name: variable for variable in current.variables}
    assert variables["current_row"].type == "Dict"
    assert variables["loop_index"].value == "1"
    assert variables["loop_index"].scope == "循环"
    assert variables["all_order_details"].type == "List"
    assert json.loads(variables["all_order_details"].value) == [
        {"count": 1, "first": ".order-0::text", "values": [".order-0::text"]},
        {"count": 1, "first": ".order-1::text", "values": [".order-1::text"]},
    ]
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert sum(1 for log in logs if log.node_id == "foreach" and "循环迭代" in log.message) == 2
    assert any(log.node_id == "foreach" and "循环完成" in log.message for log in logs)


async def test_task_manager_runs_python_script_node_and_writes_variables(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "clean.py").write_text("import os\nprint('cleaned=' + os.environ['RPA_VARIABLES_JSON'])\n", encoding="utf-8")
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._script_action_runner = ScriptActionRunner(workspace)  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="脚本流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            variables={"order_id": "A001"},
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "clean",
                        "title": "清洗脚本",
                        "type": "script.python",
                        "path": "clean.py",
                        "outputVariable": "script_stdout",
                        "statusVariable": "script_exit_code",
                        "stderrVariable": "script_stderr",
                    },
                ],
                "edges": [{"source": "start", "target": "clean"}],
            },
        )
    )

    current = await wait_for_status(manager, snapshot.task_id, {"success"})
    assert current.status == "success"
    variables = {variable.name: variable for variable in current.variables}
    assert variables["script_exit_code"].value == "0"
    assert "cleaned=" in variables["script_stdout"].value
    assert '"order_id":"A001"' in variables["script_stdout"].value
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "clean" and "脚本节点完成" in log.message for log in logs)


async def test_task_manager_runs_data_action_nodes_and_writes_variables(tmp_path) -> None:
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="数据处理流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            variables={"raw_numbers": "A001,A002", "left": 7, "right": 5},
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "match",
                        "title": "提取编号",
                        "type": "data.regex.match",
                        "inputVariable": "raw_numbers",
                        "pattern": "A(\\d+)",
                        "outputVariable": "matches",
                        "firstValueVariable": "first_match",
                        "countVariable": "match_count",
                    },
                    {
                        "id": "math",
                        "title": "计算总数",
                        "type": "data.math.compute",
                        "leftVariable": "left",
                        "rightVariable": "right",
                        "operator": "add",
                        "outputVariable": "sum_value",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "match"},
                    {"source": "match", "target": "math"},
                ],
            },
        )
    )

    current = await wait_for_status(manager, snapshot.task_id, {"success"})
    assert current.status == "success"
    variables = {variable.name: variable for variable in current.variables}
    assert variables["first_match"].value == "001"
    assert variables["match_count"].value == "2"
    assert variables["sum_value"].value == "12"
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "match" and "数据处理完成" in log.message for log in logs)
    assert any(log.node_id == "math" and "数据输出变量已更新" in log.message for log in logs)


async def test_task_manager_runs_ui_action_aliases_with_browser_context(tmp_path) -> None:
    fake_browser = FakeBrowserActionRunner()
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="UI 自动化流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            variables={"username": "alice"},
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "fill", "title": "输入文字", "type": "ui.fill", "selector": "#username", "inputValue": "${var.username}"},
                    {"id": "click", "title": "点击控件", "type": "ui.click", "selector": "#submit"},
                    {"id": "extract", "title": "获取属性", "type": "ui.extract", "selector": ".result", "outputVariable": "ui_values", "firstValueVariable": "ui_value"},
                ],
                "edges": [
                    {"source": "start", "target": "fill"},
                    {"source": "fill", "target": "click"},
                    {"source": "click", "target": "extract"},
                ],
            },
        )
    )

    current = await wait_for_status(manager, snapshot.task_id, {"success"})
    assert current.status == "success"
    assert [action["type"] for action in fake_browser.actions] == ["ui.fill", "ui.click", "ui.extract"]
    assert fake_browser.actions[0]["inputValue"] == "alice"
    variables = {variable.name: variable for variable in current.variables}
    assert variables["ui_value"].value == "提交成功"
    assert variables["ui_values"].type == "List"


async def test_task_manager_runs_delay_control_node_and_writes_variable(tmp_path) -> None:
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="等待控制流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "delay", "title": "等待延时", "type": "control.delay", "delayMs": 1, "outputVariable": "delay_ms"},
                ],
                "edges": [{"source": "start", "target": "delay"}],
            },
        )
    )

    current = await wait_for_status(manager, snapshot.task_id, {"success"})
    assert current.status == "success"
    variables = {variable.name: variable for variable in current.variables}
    assert variables["delay_ms"].value == "1"
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "delay" and "控制动作完成" in log.message for log in logs)


async def test_task_manager_breaks_foreach_loop_and_runs_exit_edge(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._file_action_runner = FileActionRunner(workspace)  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="循环中断流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            variables={"items": ["A001", "A002"]},
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "foreach", "title": "遍历列表", "type": "control.foreach", "itemsVariable": "items", "itemVariable": "current_item", "indexVariable": "loop_index"},
                    {"id": "break", "title": "中断循环", "type": "control.break"},
                    {"id": "done", "title": "写入完成标记", "type": "file.write", "path": "done.txt", "content": "${var.current_item}:${var.loop_index}", "outputVariable": "done_path"},
                ],
                "edges": [
                    {"source": "start", "target": "foreach"},
                    {"source": "foreach", "target": "break", "label": "循环体"},
                    {"source": "break", "target": "foreach"},
                    {"source": "foreach", "target": "done", "label": "完成"},
                ],
            },
        )
    )

    current = await wait_for_status(manager, snapshot.task_id, {"success"})
    assert current.status == "success"
    assert (workspace / "done.txt").read_text(encoding="utf-8") == "A001:0"
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert sum(1 for log in logs if log.node_id == "foreach" and "循环迭代" in log.message) == 1
    assert any(log.node_id == "break" and "触发中断循环" in log.message for log in logs)


async def test_task_contract_keeps_initial_input_after_node_overwrite(tmp_path) -> None:
    from app.services.acceptance_audit import audit_acceptance_contract

    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    request = RunTaskRequest(
        targetUrl="https://example.com", selector="table", variables={"kind": "采购"},
        acceptanceContract={"deliverables": [{
            "id": "rows", "variable": "rows", "kind": "table",
            "allowedValues": [{"field": "kind", "valueVariables": ["kind"]}],
        }]},
        flowDefinition={"nodes": [
            {"id": "start", "type": "start"},
            {"id": "set", "type": "variable.set", "variableName": "kind", "value": "销售"},
        ], "edges": [{"source": "start", "target": "set"}]},
    )
    try:
        snapshot = await manager.start_task(request)
        done = await wait_for_status(manager, snapshot.task_id, {"success", "error"})
        assert done.status == "success"
        assert next(v.value for v in done.variables if v.name == "kind") == "销售"
        persisted = await manager._task_store.get_task(snapshot.task_id)
        result = audit_acceptance_contract(
            persisted.acceptance_contract, {"kind": "销售", "rows": [{"kind": "销售"}]},
            [], workspace_root=tmp_path,
        )
        assert result["passed"] is False
        assert result["issues"][0]["issue"] == "allowed_values_violation"
        assert request.acceptance_contract.deliverables[0].allowed_values[0].value_variables == ["kind"]
    finally:
        await manager.stop_workers()


async def test_task_manager_runs_variable_message_actions(tmp_path) -> None:
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="变量消息流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            variables={"order_id": "A001"},
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "set", "title": "赋值变量", "type": "variable.set", "variableName": "result_status", "value": "done:${var.order_id}", "scope": "全局"},
                    {"id": "get", "title": "获取变量", "type": "variable.get", "variableName": "result_status", "outputVariable": "status_copy"},
                    {"id": "log", "title": "输出日志", "type": "variable.log", "message": "状态 ${var.status_copy}", "logLevel": "warn"},
                    {"id": "notify", "title": "消息通知", "type": "variable.notify", "channel": "企业微信", "message": "订单 ${var.order_id} 已完成", "outputVariable": "notification_message"},
                    {"id": "clipboard", "title": "剪贴板", "type": "variable.clipboard", "content": "${var.status_copy}", "outputVariable": "clipboard_text"},
                ],
                "edges": [
                    {"source": "start", "target": "set"},
                    {"source": "set", "target": "get"},
                    {"source": "get", "target": "log"},
                    {"source": "log", "target": "notify"},
                    {"source": "notify", "target": "clipboard"},
                ],
            },
        )
    )

    current = await wait_for_status(manager, snapshot.task_id, {"success"})
    assert current.status == "success"
    assert current.result is not None
    assert current.result.count == 0
    variables = {variable.name: variable for variable in current.variables}
    assert variables["result_status"].value == "done:A001"
    assert variables["result_status"].scope == "全局"
    assert variables["status_copy"].value == "done:A001"
    assert variables["notification_message"].value == "订单 A001 已完成"
    assert variables["clipboard_text"].value == "done:A001"
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert any(log.node_id == "log" and log.level == "warn" and log.detail == "状态 done:A001" for log in logs)
    assert any(log.node_id == "notify" and "消息通知已记录" in log.message and log.detail == "企业微信: 订单 A001 已完成" for log in logs)
    assert any(log.node_id == "clipboard" and "剪贴板已更新" in log.message for log in logs)


async def test_task_manager_pauses_on_debug_breakpoint_and_continues(tmp_path) -> None:
    runner = RecordingRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="断点调试流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            mode="debug",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "first",
                        "title": "断点采集",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/first",
                        "selector": ".first::text",
                        "breakpoint": True,
                    },
                    {
                        "id": "second",
                        "title": "继续采集",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/second",
                        "selector": ".second::text",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "first"},
                    {"source": "first", "target": "second"},
                ],
            },
        )
    )

    await wait_for_log(manager, snapshot.task_id, "命中断点")
    paused = await manager.get_task(snapshot.task_id)
    assert paused is not None
    assert paused.status == "running"
    assert runner.requests == []
    variables = {variable.name: variable for variable in paused.variables}
    assert variables["debug_paused_node"].value == "first"

    continued = await manager.debug_control(snapshot.task_id, "continue")
    assert continued is not None
    variables = {variable.name: variable for variable in continued.variables}
    assert variables["debug_command"].value == "继续执行"

    done = await wait_for_status(manager, snapshot.task_id, {"success"})
    assert done.status == "success"
    assert [request.selector for request in runner.requests] == [".first::text", ".second::text"]


async def test_task_manager_step_over_pauses_again_before_next_node(tmp_path) -> None:
    runner = RecordingRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="单步调试流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            mode="debug",
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "first",
                        "title": "第一个节点",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/first",
                        "selector": ".first::text",
                        "breakpoint": True,
                    },
                    {
                        "id": "second",
                        "title": "第二个节点",
                        "type": "browser.fetch",
                        "targetUrl": "https://example.com/second",
                        "selector": ".second::text",
                    },
                ],
                "edges": [
                    {"source": "start", "target": "first"},
                    {"source": "first", "target": "second"},
                ],
            },
        )
    )

    await wait_for_log(manager, snapshot.task_id, "命中断点")
    stepped = await manager.debug_control(snapshot.task_id, "step-over")
    assert stepped is not None
    await wait_for_log(manager, snapshot.task_id, "命中断点", node_id="second")

    paused = await manager.get_task(snapshot.task_id)
    assert paused is not None
    assert paused.status == "running"
    assert [request.selector for request in runner.requests] == [".first::text"]

    await manager.debug_control(snapshot.task_id, "continue")
    done = await wait_for_status(manager, snapshot.task_id, {"success"})
    assert done.status == "success"
    assert [request.selector for request in runner.requests] == [".first::text", ".second::text"]


async def test_task_manager_respects_queue_concurrency(tmp_path) -> None:
    def _single_fetch_definition() -> dict[str, object]:
        return {
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "fetch", "type": "browser.fetch", "targetUrl": "https://example.com/", "selector": "h1::text"},
            ],
            "edges": [{"source": "start", "target": "fetch"}],
        }

    runner = SlowRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path), concurrency=1)
    first = await manager.start_task(
        RunTaskRequest(flowName="任务一", targetUrl="https://example.com/", selector="h1::text", flowDefinition=_single_fetch_definition())
    )
    second = await manager.start_task(
        RunTaskRequest(flowName="任务二", targetUrl="https://example.com/", selector="h1::text", flowDefinition=_single_fetch_definition())
    )

    for _ in range(20):
        if runner.started:
            break
        await asyncio.sleep(0.01)

    assert runner.started == [first.task_id]
    queued = await manager.get_task(second.task_id)
    assert queued is not None
    assert queued.status == "queued"
    stats = await manager.queue_stats()
    assert stats.concurrency == 1
    assert stats.active_count == 1
    assert stats.queued_count == 1
    assert stats.active_task_ids == [first.task_id]

    runner.release.set()
    for _ in range(40):
        current = await manager.get_task(second.task_id)
        assert current is not None
        if current.status == "success":
            break
        await asyncio.sleep(0.01)

    first_done = await manager.get_task(first.task_id)
    second_done = await manager.get_task(second.task_id)
    assert first_done is not None
    assert second_done is not None
    assert first_done.status == "success"
    assert second_done.status == "success"


async def wait_for_status(manager: TaskManager, task_id: str, statuses: set[str]) -> object:
    for _ in range(80):
        current = await manager.get_task(task_id)
        assert current is not None
        if current.status in statuses:
            return current
        await asyncio.sleep(0.01)
    raise AssertionError(f"任务未进入预期状态: {statuses}")


async def wait_for_log(manager: TaskManager, task_id: str, message: str, *, node_id: str | None = None) -> None:
    for _ in range(80):
        logs = await manager.get_logs(task_id)
        assert logs is not None
        if any(message in log.message and (node_id is None or log.node_id == node_id) for log in logs):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"任务未产生预期日志: {message}")


async def test_task_manager_repeat_until_stops_when_condition_holds(tmp_path) -> None:
    """次数由运行时状态决定：循环体每轮推进计数，条件成立即退出，不靠写死次数。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._file_action_runner = FileActionRunner(workspace)  # type: ignore[attr-defined]

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="重复直到流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            variables={"panel_month": "3", "target_month": "6"},
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "repeat", "title": "翻到目标月份", "type": "control.repeat_until",
                        "condition": "panel_month == target_month", "indexVariable": "repeat_index", "maxIterations": 10,
                    },
                    # 模拟「点下月 + 回读面板标题」：每轮把面板月份推进一个月
                    {
                        "id": "advance", "title": "推进面板月份", "type": "data.math.compute",
                        "left": "${var.panel_month}", "right": "1", "operator": "add", "outputVariable": "panel_month",
                    },
                    {"id": "done", "title": "写入结果", "type": "file.write", "path": "done.txt", "content": "${var.panel_month}:${var.repeat_index}", "outputVariable": "done_path"},
                ],
                "edges": [
                    {"source": "start", "target": "repeat"},
                    {"source": "repeat", "target": "advance", "label": "body"},
                    {"source": "advance", "target": "repeat"},
                    {"source": "repeat", "target": "done", "label": "exit"},
                ],
            },
        )
    )

    current = await wait_for_status(manager, snapshot.task_id, {"success", "error"})
    assert current.status == "success"
    # 3 轮后 3 → 6，退出条件成立；最后一轮的 repeat_index 是 2
    assert (workspace / "done.txt").read_text(encoding="utf-8") == "6:2"
    logs = await manager.get_logs(snapshot.task_id)
    assert logs is not None
    assert sum(1 for log in logs if log.node_id == "repeat" and "重复迭代" in log.message) == 3


async def test_task_manager_repeat_until_fails_when_max_iterations_exhausted(tmp_path) -> None:
    """跑满上限 = 目标状态没达成。默认必须失败，否则后续节点会在错误的页面状态上取数。"""
    manager = TaskManager(runner=RecordingRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))

    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="重复上限流程",
            targetUrl="https://example.com/fallback",
            selector=".fallback::text",
            variables={"panel_month": "2026-03", "target_month": "2099-01"},
            flowDefinition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "repeat", "title": "翻到目标月份", "type": "control.repeat_until",
                        "condition": "panel_month == target_month", "maxIterations": 3,
                    },
                    {"id": "noop", "title": "空转", "type": "control.noop"},
                ],
                "edges": [
                    {"source": "start", "target": "repeat"},
                    {"source": "repeat", "target": "noop", "label": "body"},
                    {"source": "noop", "target": "repeat"},
                ],
            },
        )
    )

    current = await wait_for_status(manager, snapshot.task_id, {"error", "success"})
    assert current.status == "error"


class LockingBlockingBrowserRunner(FakeBrowserActionRunner):
    """create_context 登记 profile 锁、动作节点阻塞、close_context 释放锁——复刻停止时的解锁路径。"""

    def __init__(self, profile_dir: str) -> None:
        super().__init__()
        self._profile_dir = profile_dir
        self.acquired = asyncio.Event()
        self._owner: str | None = None

    async def create_context(self, *, headless: bool = True, owner: str | None = None) -> object:
        self._owner = owner or "另一个运行"
        browser_profile_lock.acquire(self._profile_dir, self._owner)
        self.acquired.set()
        return object()

    async def close_context(self, context: object | None) -> None:
        if self._owner is not None:
            browser_profile_lock.release(self._profile_dir, self._owner)

    async def run(self, node, variables, context, *, timeout_ms):
        await asyncio.Event().wait()  # 停在动作节点上占着锁，直到 stop_task 取消运行协程
        raise AssertionError("unreachable")


async def test_stop_releases_browser_profile_lock_before_returning(tmp_path) -> None:
    profile_dir = str(tmp_path / "profile")
    fake_browser = LockingBlockingBrowserRunner(profile_dir)
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]
    try:
        snapshot = await manager.start_task(
            RunTaskRequest(
                flowName="停止解锁流程",
                targetUrl="https://example.com/",
                selector=".item::text",
                flowDefinition={
                    "nodes": [
                        {"id": "start", "type": "start"},
                        {"id": "click", "title": "点击", "type": "browser.click", "selector": "#submit"},
                    ],
                    "edges": [{"source": "start", "target": "click"}],
                },
            )
        )
        # 等到浏览器上下文建立、profile 锁登记，运行协程停在动作节点上
        await asyncio.wait_for(fake_browser.acquired.wait(), timeout=2)
        assert browser_profile_lock.holder(profile_dir) is not None

        stopped = await manager.stop_task(snapshot.task_id)
        assert stopped is not None
        # stop_task 返回时锁必须已释放：早返回（收尾还没跑）会让此断言失败，正是本次修复点
        assert browser_profile_lock.holder(profile_dir) is None
    finally:
        current = browser_profile_lock.holder(profile_dir)
        if current is not None:
            browser_profile_lock.release(profile_dir, current)
        await manager.stop_workers()


class LockingBrowserRunner(FakeBrowserActionRunner):
    """create_context 登记锁、动作节点正常返回、close_context 释放锁并留痕——
    用来验证收尾一定会关浏览器（放锁），无论导出 Cookie 是否半途出事。"""

    def __init__(self, profile_dir: str) -> None:
        super().__init__()
        self._profile_dir = profile_dir
        self._owner: str | None = None
        self.closed = asyncio.Event()

    async def create_context(self, *, headless: bool = True, owner: str | None = None) -> object:
        self._owner = owner or "另一个运行"
        browser_profile_lock.acquire(self._profile_dir, self._owner)
        return object()

    async def close_context(self, context: object | None) -> None:
        if self._owner is not None:
            browser_profile_lock.release(self._profile_dir, self._owner)
        self.closed.set()


async def test_cancel_during_cookie_export_still_closes_the_browser(tmp_path, monkeypatch) -> None:
    """收尾里先 await _export_browser_cookies()（内部 await storage_state()），取消恰好落在这个
    await 上时 CancelledError 会逃出该函数（它只吞 Exception）。close_context 必须仍然执行，
    否则浏览器进程与 profile 独占锁永久悬挂。"""
    profile_dir = str(tmp_path / "profile")
    fake_browser = LockingBrowserRunner(profile_dir)
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    manager._browser_action_runner = fake_browser  # type: ignore[attr-defined]

    async def _cancelled_export(context: object | None) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("app.services.task_manager._export_browser_cookies", _cancelled_export)

    try:
        await manager.start_task(
            RunTaskRequest(
                flowName="导出取消收尾流程",
                targetUrl="https://example.com/",
                selector=".item::text",
                flowDefinition={
                    "nodes": [
                        {"id": "start", "type": "start"},
                        {"id": "click", "title": "点击", "type": "browser.click", "selector": "#submit"},
                    ],
                    "edges": [{"source": "start", "target": "click"}],
                },
            )
        )
        # 导出 Cookie 抛 CancelledError 后，close_context 仍须跑完——否则这里超时
        await asyncio.wait_for(fake_browser.closed.wait(), timeout=2)
        assert browser_profile_lock.holder(profile_dir) is None
    finally:
        current = browser_profile_lock.holder(profile_dir)
        if current is not None:
            browser_profile_lock.release(profile_dir, current)
        await manager.stop_workers()


async def test_get_artifact_content_falls_back_to_persisted_snapshot(tmp_path) -> None:
    """历史任务/重启后内存里已无产物路径映射，get_artifact_content 必须按持久化快照的 storage_url 回读，
    而非退回 404——否则运行详情里的产物预览对所有非本次运行的任务都失效。"""
    saved = await LocalArtifactStore(artifact_root=tmp_path).save_bytes(
        task_id="t_hist",
        artifact_type="screenshot",
        filename="shot.png",
        content=b"\x89PNG\r\n\x1a\nfake",
        content_type="image/png",
    )
    store = InMemoryTaskStore()
    now = datetime.now(UTC)
    await store.save_task(
        TaskSnapshot(
            task_id="t_hist",
            flow_name="历史任务",
            status="success",
            mode="run",
            progress=RuntimeProgress(current_step=1, total_steps=1, percent=100, elapsed_ms=0),
            created_at=now,
            updated_at=now,
            artifacts=[saved],
        ),
        RunTaskRequest(
            flowName="历史任务",
            targetUrl="https://example.com/",
            selector=".item::text",
            flowDefinition={"nodes": [{"id": "fetch", "type": "browser.fetch", "targetUrl": "https://example.com/", "selector": ".item::text"}], "edges": []},
        ),
    )

    # 模拟重启：全新 TaskManager + 全新 store 实例，内存映射为空，仅共享落盘产物与持久化 store
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path), task_store=store)
    content = await manager.get_artifact_content("t_hist", saved.artifact_id)
    assert content is not None
    assert content.content.startswith("data:image/png;base64,")
    assert await manager.get_artifact_content("t_hist", "missing") is None
