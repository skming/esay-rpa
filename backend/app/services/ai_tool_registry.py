"""工具的公开边界、执行入口与副作用分类；不依赖执行器或编排状态。"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    handler: str
    effect: Literal["read", "write", "flow_write"]
    after_handler: str | None
    context: Literal["none", "change_context", "progress_sink", "flow_id", "expected_flow_id"] = "none"
    public: bool = True
    parallel_safe: bool = False


_TOOLS = (
    ToolSpec("list_node_types", "_list_node_types", "read", None, parallel_safe=True),
    ToolSpec("create_flow", "_create_flow", "flow_write", "_after_create_flow", context="change_context"),
    ToolSpec("update_flow", "_update_flow", "flow_write", "_after_update_flow", context="change_context"),
    ToolSpec("run_flow", "_run_flow", "write", "_after_run_flow", context="progress_sink"),
    ToolSpec("check_extension_connection", "_check_extension_connection", "read", None),
    ToolSpec("stop_run", "_stop_run", "write", None),
    ToolSpec("list_schedules", "_list_schedules", "read", None, context="flow_id", parallel_safe=True),
    ToolSpec("create_schedule", "_create_schedule", "write", None),
    ToolSpec("toggle_schedule", "_toggle_schedule", "write", None, context="expected_flow_id"),
    ToolSpec("get_run_error", "_get_run_error", "read", "_after_get_run_error"),
    ToolSpec("apply_node_fix", "_apply_node_fix", "flow_write", "_after_apply_node_fix", context="change_context"),
    ToolSpec("set_acceptance_contract", "_set_acceptance_contract", "flow_write", "_after_set_acceptance_contract", context="change_context"),
    ToolSpec("get_run_output", "_get_run_output", "read", "_after_get_run_output", parallel_safe=True),
    ToolSpec("inspect_page", "_inspect_page", "read", "_after_inspect_page"),
    # 页面操作改变真实浏览器状态，不能作为只读工具并发预取。
    ToolSpec("interact_page", "_interact_page", "write", "_after_interact_page"),
    ToolSpec("inspect_screenshot", "_inspect_screenshot", "read", "_after_inspect_screenshot"),
    ToolSpec("get_run_logs", "_get_run_logs", "read", "_after_get_run_logs", parallel_safe=True),
    # 平台自行构造状态块和验收；这些能力不开放给模型调用。
    ToolSpec("get_flow", "_get_flow", "read", None, public=False),
    ToolSpec("lint_flow", "_lint_flow_tool", "read", None, public=False),
    ToolSpec("validate_flow", "_validate_flow", "read", None, public=False),
    ToolSpec("get_run_status", "_get_run_status", "read", None, public=False),
    ToolSpec("audit_run", "_audit_run", "read", None, public=False),
)

TOOL_REGISTRY = MappingProxyType({tool.name: tool for tool in _TOOLS})
if len(TOOL_REGISTRY) != len(_TOOLS):
    raise ValueError("Duplicate tool registration")

PUBLIC_TOOL_NAMES = frozenset(tool.name for tool in _TOOLS if tool.public)
WRITE_TOOLS = frozenset(tool.name for tool in _TOOLS if tool.effect != "read")
FLOW_WRITE_TOOLS = frozenset(tool.name for tool in _TOOLS if tool.effect == "flow_write")
PARALLEL_SAFE_TOOLS = frozenset(tool.name for tool in _TOOLS if tool.parallel_safe)


def bind_tool_schemas(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保留模型所见顺序与内容，拒绝公开工具漏接或内部工具误公开。"""
    names = [schema["function"]["name"] for schema in schemas]
    if len(names) != len(set(names)) or set(names) != PUBLIC_TOOL_NAMES:
        raise ValueError("Tool schemas must match public registrations exactly")
    return schemas
