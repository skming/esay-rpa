from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.schemas import FlowRunRequest, FlowSnapshot, RunTaskRequest, RuntimeProgress, ScrapeResult, TaskSnapshot
from app.services.flow_control import evaluate_condition
from app.services.flow_definition import FlowDefinitionSelector, is_executable_node
from app.services.flow_runner import FlowRunService
from app.services.log_broker import LogBroker
from app.services.runtime_variables import RuntimeVariableStore
from app.services.scrapling_runner import LogCallback
from app.services.task_manager import TaskManager


class FakeRunner:
    def __init__(self) -> None:
        self.requests: list[RunTaskRequest] = []

    async def run(self, task_id: str, request: RunTaskRequest, on_log: LogCallback) -> ScrapeResult:
        self.requests.append(request)
        await on_log("running", "按流程定义采集", request.selector)
        return ScrapeResult(url=str(request.target_url), selector=request.selector, count=1, values=["flow-result"])


class RecordingTaskManager:
    def __init__(self) -> None:
        self.requests: list[RunTaskRequest] = []

    async def start_task(self, request: RunTaskRequest) -> TaskSnapshot:
        self.requests.append(request)
        now = datetime.now(UTC)
        return TaskSnapshot(
            taskId="task-1",
            flowId=request.flow_id,
            flowName=request.flow_name,
            status="queued",
            mode=request.mode,
            progress=RuntimeProgress(currentStep=0, totalSteps=3, percent=0, elapsedMs=0),
            createdAt=now,
            updatedAt=now,
        )


def build_flow(definition: dict[str, object] | None = None) -> FlowSnapshot:
    now = datetime.now(UTC)
    return FlowSnapshot(
        flowId="flow-1",
        name="流程运行测试",
        version="v1.0.0",
        status="active",
        inputVariables=[],
        definition=definition
        or {
            "nodes": [
                {"id": "start", "type": "start"},
                {
                    "id": "n1",
                    "type": "browser.fetch",
                    "targetUrl": "https://quotes.toscrape.com/",
                    "selector": ".quote .text::text",
                    "fetcher": "static",
                    "extractMode": "text",
                    "timeoutMs": 1000,
                },
            ],
            "edges": [{"source": "start", "target": "n1"}],
        },
        createdAt=now,
        updatedAt=now,
    )


def test_flow_template_action_types_are_backend_executable() -> None:
    template_action_types = {
        "browser.fetch",
        "browser.open",
        "browser.extract",
        "browser.scroll",
        "browser.wait",
        "control.foreach",
        "data.json.parse",
        "excel.read",
        "file.write",
        "http.request",
        "variable.log",
    }

    unsupported = sorted(action_type for action_type in template_action_types if not is_executable_node({"type": action_type}))

    assert unsupported == []


async def test_flow_run_service_starts_task_from_browser_fetch_node() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    service = FlowRunService(task_manager=task_manager)

    snapshot = await service.run_flow(build_flow(), mode="debug")

    assert snapshot.flow_id == "flow-1"
    assert snapshot.flow_name == "流程运行测试"
    assert snapshot.mode == "debug"
    task = await task_manager.get_task(snapshot.task_id)
    assert task is not None
    assert task.flow_id == "flow-1"


async def test_flow_run_service_rejects_missing_executable_node() -> None:
    task_manager = TaskManager(runner=FakeRunner(), broker=LogBroker())
    service = FlowRunService(task_manager=task_manager)

    try:
        await service.run_flow(build_flow({"nodes": [{"id": "start", "type": "start"}], "edges": []}))
    except ValueError as exc:
        assert "可执行节点" in str(exc)
    else:
        raise AssertionError("缺少可执行节点时应该失败")


async def test_flow_run_service_starts_non_fetch_flow_definition() -> None:
    task_manager = RecordingTaskManager()
    service = FlowRunService(task_manager=task_manager)  # type: ignore[arg-type]
    definition = {
        "nodes": [
            {"id": "start", "type": "start"},
            {
                "id": "api",
                "title": "读取接口数据",
                "type": "http.request",
                "url": "https://api.example.com/orders",
                "method": "GET",
                "responseVariable": "api_response",
            },
        ],
        "edges": [{"source": "start", "target": "api"}],
    }

    snapshot = await service.run_flow(build_flow(definition), mode="run")

    assert snapshot.flow_id == "flow-1"
    request = task_manager.requests[0]
    assert request.flow_definition == definition
    assert request.selector == ".quote .text::text"
    assert str(request.target_url) == "https://quotes.toscrape.com/"


async def test_flow_run_service_uses_edge_order_for_fetch_node() -> None:
    task_manager = RecordingTaskManager()
    service = FlowRunService(task_manager=task_manager)  # type: ignore[arg-type]
    definition = {
        "nodes": [
            {
                "id": "detached",
                "type": "browser.fetch",
                "targetUrl": "https://example.com/detached",
                "selector": ".detached::text",
            },
            {"id": "start", "type": "start"},
            {"id": "guard", "type": "control.step"},
            {
                "id": "reachable",
                "type": "browser.fetch",
                "targetUrl": "https://quotes.toscrape.com/",
                "selector": ".quote .text::text",
            },
        ],
        "edges": [
            {"source": "start", "target": "guard"},
            {"source": "guard", "target": "reachable"},
        ],
    }

    snapshot = await service.run_flow(build_flow(definition), mode="run")

    assert snapshot.flow_id == "flow-1"
    assert task_manager.requests[0].selector == ".quote .text::text"
    assert str(task_manager.requests[0].target_url) == "https://quotes.toscrape.com/"


async def test_flow_run_service_selects_fetch_node_from_scope_and_skips_disabled() -> None:
    definition = {
        "nodes": [
            {"id": "start", "type": "start"},
            {
                "id": "first",
                "type": "browser.fetch",
                "targetUrl": "https://example.com/disabled",
                "selector": ".disabled::text",
                "disabled": True,
            },
            {"id": "middle", "type": "control.step"},
            {
                "id": "second",
                "type": "browser.fetch",
                "targetUrl": "https://quotes.toscrape.com/",
                "selector": ".quote .author::text",
            },
        ],
        "edges": [
            {"source": "start", "target": "first"},
            {"source": "first", "target": "middle"},
            {"source": "middle", "target": "second"},
        ],
    }

    nodes = FlowDefinitionSelector.select_executable_fetch_nodes(definition, scope="from-selection", start_node_id="middle")

    assert [node["id"] for node in nodes] == ["second"]
    assert nodes[0]["selector"] == ".quote .author::text"


async def test_flow_run_service_selected_only_requires_selected_fetch_node() -> None:
    definition = {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "middle", "type": "control.step"},
            {
                "id": "fetch",
                "type": "browser.fetch",
                "targetUrl": "https://quotes.toscrape.com/",
                "selector": ".quote .text::text",
            },
        ],
        "edges": [
            {"source": "start", "target": "middle"},
            {"source": "middle", "target": "fetch"},
        ],
    }

    assert FlowDefinitionSelector.select_executable_fetch_nodes(definition, scope="selected-only", start_node_id="middle") == []
    assert FlowDefinitionSelector.select_executable_fetch_nodes(definition, scope="selected-only", start_node_id="fetch")[0]["selector"] == ".quote .text::text"


async def test_flow_run_service_uses_saved_fetch_configuration_and_input_variables() -> None:
    task_manager = RecordingTaskManager()
    service = FlowRunService(task_manager=task_manager)  # type: ignore[arg-type]
    flow = build_flow(
        {
            "nodes": [
                {"id": "start", "type": "start"},
                {
                    "id": "n1",
                    "type": "browser.fetch",
                    "targetUrl": "https://example.com/orders",
                    "selector": ".order-row",
                    "fetcher": "dynamic",
                    "extractMode": "html",
                    "attribute": "data-id",
                    "adaptive": False,
                    "autoSave": False,
                    "timeoutMs": 45_000,
                },
            ],
            "edges": [{"source": "start", "target": "n1"}],
        }
    )
    flow.input_variables = [
        {"name": "username", "type": "String", "scope": "全局", "value": "zhang.san"},
        {"name": "retry_count", "type": "Integer", "scope": "全局", "value": "3"},
        {"name": "headless", "type": "Boolean", "scope": "全局", "value": "true"},
        {"name": "payload", "type": "Dict", "scope": "全局", "value": "{\"env\":\"prod\"}"},
    ]

    await service.run_flow(flow, mode="run")

    request = task_manager.requests[0]
    assert str(request.target_url) == "https://example.com/orders"
    assert request.selector == ".order-row"
    assert request.fetcher == "dynamic"
    assert request.extract_mode == "html"
    assert request.attribute == "data-id"
    assert request.adaptive is False
    assert request.auto_save is False
    assert request.timeout_ms == 45_000
    assert request.variables == {
        "username": "zhang.san",
        "retry_count": 3,
        "headless": True,
        "payload": {"env": "prod"},
    }


async def test_flow_run_service_respects_run_request_options_and_variable_overrides() -> None:
    task_manager = RecordingTaskManager()
    service = FlowRunService(task_manager=task_manager)  # type: ignore[arg-type]
    flow = build_flow()
    flow.input_variables = [
        {"name": "username", "type": "String", "scope": "全局", "value": "zhang.san"},
        {"name": "retry_count", "type": "Integer", "scope": "全局", "value": "1"},
    ]

    await service.run_flow(
        flow,
        mode="run",
        run_request=FlowRunRequest(
            mode="debug",
            scope="from-selection",
            startNodeId="n1",
            failureStrategy="retry",
            screenshot=False,
            concurrency=3,
            timeoutMs=12_000,
            variables={"retry_count": 5, "run_scope": "partial"},
        ),
    )

    request = task_manager.requests[0]
    assert request.mode == "debug"
    assert request.scope == "from-selection"
    assert request.start_node_id == "n1"
    assert request.failure_strategy == "retry"
    assert request.screenshot is False
    assert request.concurrency == 3
    assert request.timeout_ms == 1_000
    assert request.variables == {
        "username": "zhang.san",
        "retry_count": 5,
        "run_scope": "partial",
    }


async def test_flow_definition_selector_returns_ordered_fetch_nodes_and_skips_detached() -> None:
    definition = {
        "nodes": [
            {
                "id": "detached",
                "type": "browser.fetch",
                "targetUrl": "https://example.com/detached",
                "selector": ".detached::text",
            },
            {"id": "start", "type": "start"},
            {
                "id": "first",
                "type": "browser.fetch",
                "targetUrl": "https://example.com/first",
                "selector": ".first::text",
            },
            {"id": "disabled", "type": "control.step", "disabled": True},
            {
                "id": "second",
                "type": "browser.fetch",
                "targetUrl": "https://example.com/second",
                "selector": ".second::text",
            },
        ],
        "edges": [
            {"source": "start", "target": "first"},
            {"source": "first", "target": "disabled"},
            {"source": "disabled", "target": "second"},
        ],
    }

    nodes = FlowDefinitionSelector.select_executable_fetch_nodes(definition)

    assert [node["id"] for node in nodes] == ["first", "second"]


async def test_flow_definition_selector_includes_condition_nodes_in_execution_plan() -> None:
    definition = {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "guard", "type": "control.step", "description": "row_count > 0"},
            {
                "id": "fetch",
                "type": "browser.fetch",
                "targetUrl": "https://example.com/fetch",
                "selector": ".fetch::text",
            },
        ],
        "edges": [
            {"source": "start", "target": "guard"},
            {"source": "guard", "target": "fetch", "label": "是"},
        ],
    }

    nodes = FlowDefinitionSelector.select_executable_nodes(definition)

    assert [node["id"] for node in nodes] == ["guard", "fetch"]


async def test_flow_condition_evaluator_supports_safe_operands() -> None:
    variables = RuntimeVariableStore.from_initial({"row_count": "2", "status": "ok", "ready": True})

    assert evaluate_condition({"type": "control.condition", "condition": "row_count > 0"}, variables) is True
    assert evaluate_condition({"type": "control.condition", "condition": "row_count >= 2"}, variables) is True
    assert evaluate_condition({"type": "control.condition", "condition": "status == 'ok'"}, variables) is True
    assert evaluate_condition({"type": "control.condition", "condition": "ready"}, variables) is True


async def test_flow_condition_rejects_template_variable_syntax() -> None:
    variables = RuntimeVariableStore.from_initial({"row_count": "2"})

    with pytest.raises(ValueError, match="不支持的条件表达式值"):
        evaluate_condition({"type": "control.condition", "condition": "${var.row_count} > 0"}, variables)
