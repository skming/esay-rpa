from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.models.schemas import RunTaskRequest, ScrapeResult
from app.services.artifact_store import LocalArtifactStore
from app.services.file_action_runner import FileActionRunner
from app.services.log_broker import LogBroker
from app.services.scrapling_runner import LogCallback
from app.services.script_action_runner import ScriptActionRunner
from app.services.task_manager import TaskManager


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

    async def create_context(self) -> object:
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


async def test_task_manager_runs_task_to_success(tmp_path) -> None:
    manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path))
    snapshot = await manager.start_task(
        RunTaskRequest(
            flowName="测试流程",
            targetUrl="https://example.com/",
            selector="h1::text",
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

    first = await run_flow("盈点通合约列表抓取")
    second = await run_flow("盈点通-合约列表抓取")

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
                    {"id": "disabled", "type": "control.step", "disabled": True},
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
                    {"id": "guard", "title": "判断是否有数据", "type": "control.step", "description": "row_count > 0"},
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
    assert any(log.node_id == "extract" and "截图已保存" in log.message for log in logs)


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
                    {"id": "input", "title": "输入弹窗", "type": "variable.input", "variableName": "manual_note", "message": "请输入备注", "defaultValue": "无需人工处理", "scope": "局部"},
                ],
                "edges": [
                    {"source": "start", "target": "set"},
                    {"source": "set", "target": "get"},
                    {"source": "get", "target": "log"},
                    {"source": "log", "target": "notify"},
                    {"source": "notify", "target": "clipboard"},
                    {"source": "clipboard", "target": "input"},
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
    assert variables["manual_note"].value == "无需人工处理"
    assert variables["manual_note"].scope == "局部"
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
    runner = SlowRunner()
    manager = TaskManager(runner=runner, broker=LogBroker(), artifact_store=LocalArtifactStore(artifact_root=tmp_path), concurrency=1)
    first = await manager.start_task(
        RunTaskRequest(flowName="任务一", targetUrl="https://example.com/", selector="h1::text")
    )
    second = await manager.start_task(
        RunTaskRequest(flowName="任务二", targetUrl="https://example.com/", selector="h1::text")
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
