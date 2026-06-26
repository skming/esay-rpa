from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.models.schemas import FlowRunRequest, FlowSnapshot, RunMode, RunTaskRequest, TaskSnapshot
from app.services.flow_definition import FlowDefinitionSelector

if TYPE_CHECKING:
    from app.services.task_manager import TaskManager


class FlowRunService:
    """Translates a FlowSnapshot into a RunTaskRequest and delegates execution to TaskManager."""

    def __init__(self, task_manager: "TaskManager") -> None:
        self._task_manager = task_manager

    async def run_flow(self, flow: FlowSnapshot, *, mode: RunMode = "run", run_request: FlowRunRequest | None = None) -> TaskSnapshot:
        request = self._build_task_request(flow, mode=mode, run_request=run_request)
        return await self._task_manager.start_task(request)

    def _build_task_request(self, flow: FlowSnapshot, *, mode: RunMode, run_request: FlowRunRequest | None = None) -> RunTaskRequest:
        scope = run_request.scope if run_request is not None else "full"
        start_node_id = run_request.start_node_id if run_request is not None else None
        executable_nodes = FlowDefinitionSelector.select_executable_nodes(flow.definition, scope=scope, start_node_id=start_node_id)
        if not executable_nodes:
            raise ValueError("流程定义缺少可执行节点")

        overrides = run_request.variables if run_request is not None else {}
        base_request = RunTaskRequest(
            flowId=flow.flow_id,
            flowName=flow.name,
            flowDefinition=flow.definition,
            mode=run_request.mode if run_request is not None else mode,
            targetUrl="https://quotes.toscrape.com/",
            selector=".quote .text::text",
            variables={**_build_initial_variables(flow), **overrides},
            timeoutMs=run_request.timeout_ms if run_request is not None else 30_000,
            scope=scope,
            startNodeId=start_node_id,
            failureStrategy=run_request.failure_strategy if run_request is not None else "stop",
            screenshot=run_request.screenshot if run_request is not None else True,
            concurrency=run_request.concurrency if run_request is not None else 1,
        )
        fetch_node = next((node for node in executable_nodes if node.get("type") == "browser.fetch"), None)
        if fetch_node is None:
            return base_request
        return FlowDefinitionSelector.build_request_for_fetch_node(base_request, fetch_node)


def _build_initial_variables(flow: FlowSnapshot) -> dict[str, object]:
    """Converts the flow's declared input variables into typed Python values for the variable store."""
    variables: dict[str, object] = {}
    for variable in flow.input_variables:
        if isinstance(variable, dict):
            name = variable.get("name")
            variable_type = variable.get("type")
            value = variable.get("value")
        else:
            name = variable.name
            variable_type = variable.type
            value = variable.value

        if not isinstance(name, str) or not isinstance(variable_type, str) or not isinstance(value, str):
            continue
        variables[name] = _parse_variable_value(variable_type, value)
    return variables


def _parse_variable_value(variable_type: str, value: str) -> object:
    """Cast a string variable value to the appropriate Python type; falls back to the raw string on error."""
    if variable_type == "Integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    if variable_type == "Boolean":
        return value.strip().lower() == "true"
    if variable_type in {"List", "Dict"}:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
