from __future__ import annotations

from dataclasses import dataclass

from app.models.schemas import ScrapeResult, TaskLogLevel
from app.services.runtime_variables import RuntimeVariableStore, normalize_variable_name, read_variable_name, read_variable_scope, resolve_template_value, stringify_variable_value

type FlowNode = dict[str, object]

_VARIABLE_ACTION_TYPES = {
    "variable.step",
    "variable.set",
    "variable.assign",
    "variable.get",
    "variable.input",
    "variable.log",
    "variable.notify",
    "variable.clipboard",
}
_LOG_LEVELS: set[TaskLogLevel] = {"info", "success", "running", "warn", "error"}


@dataclass(frozen=True)
class VariableActionResult:
    action_type: str
    detail: str
    values: list[object]
    log_level: TaskLogLevel = "success"

    def to_scrape_result(self) -> ScrapeResult:
        return ScrapeResult(
            url="",
            selector=self.action_type,
            count=len(self.values),
            values=[stringify_variable_value(value) for value in self.values],
        )


class VariableActionRunner:
    async def run(self, node: FlowNode, variables: RuntimeVariableStore, *, timeout_ms: int) -> VariableActionResult:
        action_type = _read_action_type(node)

        if action_type in {"variable.step", "variable.set", "variable.assign"}:
            name = read_variable_name(node)
            value = _read_template_value(node, variables, keys=("value", "inputValue", "defaultValue", "content"))
            variables.set(name, value, scope=read_variable_scope(node, default="全局"))
            return VariableActionResult(action_type=action_type, detail=f"{name}={stringify_variable_value(value)}", values=[value])

        if action_type == "variable.get":
            name = _read_variable_lookup_name(node)
            value = variables.get(name)
            return VariableActionResult(action_type=action_type, detail=name, values=[value])

        if action_type == "variable.input":
            message = _read_optional_template_text(node, variables, "message") or _read_optional_template_text(node, variables, "description") or "输入弹窗"
            value = _read_template_value(node, variables, keys=("defaultValue", "value", "inputValue"), default="")
            return VariableActionResult(action_type=action_type, detail=message, values=[value])

        if action_type == "variable.log":
            message = _read_message(node, variables)
            level = _read_log_level(node)
            return VariableActionResult(action_type=action_type, detail=message, values=[message], log_level=level)

        if action_type == "variable.notify":
            channel = _read_optional_template_text(node, variables, "channel") or "系统通知"
            message = _read_message(node, variables)
            return VariableActionResult(action_type=action_type, detail=f"{channel}: {message}", values=[message], log_level="info")

        if action_type == "variable.clipboard":
            value = _read_template_text(node, variables, keys=("content", "inputValue", "value", "message"))
            return VariableActionResult(action_type=action_type, detail="剪贴板内容已写入运行变量", values=[value])

        raise ValueError(f"不支持的变量 / 消息节点类型: {action_type}")


def is_variable_action_node(node: FlowNode) -> bool:
    return node.get("type") in _VARIABLE_ACTION_TYPES


def apply_variable_result_variables(node: FlowNode, result: VariableActionResult, variables: RuntimeVariableStore) -> list[str]:
    saved_names: list[str] = []

    if result.action_type in {"variable.step", "variable.set", "variable.assign"}:
        _append_saved_name(saved_names, read_variable_name(node))

    if result.action_type == "variable.input":
        variable_name = _read_optional_string(node, "variableName") or _read_optional_string(node, "name")
        if variable_name is not None and result.values:
            variables.set(variable_name, result.values[0], scope=read_variable_scope(node, default="全局"))
            _append_saved_name(saved_names, variable_name)

    output_variable = _read_optional_string(node, "outputVariable") or _read_optional_string(node, "resultVariable") or _read_optional_string(node, "responseVariable")
    if output_variable is not None:
        normalized_output_variable = normalize_variable_name(output_variable)
        if normalized_output_variable not in saved_names:
            variables.set(output_variable, result.values[0] if len(result.values) == 1 else result.values, scope="局部")
            saved_names.append(normalized_output_variable)
    elif result.action_type == "variable.clipboard" and result.values:
        # 未指定 outputVariable 时约定写入固定名 clipboard_text，供下游节点无需配置即可引用
        variables.set("clipboard_text", result.values[0], scope="局部")
        _append_saved_name(saved_names, "clipboard_text")

    return saved_names


def _read_action_type(node: FlowNode) -> str:
    value = node.get("type")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("变量 / 消息节点缺少 type")
    return value.strip()


def _read_variable_lookup_name(node: FlowNode) -> str:
    for key in ("variableName", "name", "inputVariable"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_variable_name(value)
    raise ValueError("获取变量节点缺少 variableName")


def _read_message(node: FlowNode, variables: RuntimeVariableStore) -> str:
    return _read_template_text(node, variables, keys=("message", "content", "inputValue", "value", "description"))


def _read_template_text(node: FlowNode, variables: RuntimeVariableStore, *, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return variables.resolve_text(value.strip())
    raise ValueError("变量 / 消息节点缺少消息内容")


def _read_template_value(node: FlowNode, variables: RuntimeVariableStore, *, keys: tuple[str, ...], default: object = "") -> object:
    for key in keys:
        if key in node:
            return resolve_template_value(node[key], variables)
    return default


def _read_optional_template_text(node: FlowNode, variables: RuntimeVariableStore, key: str) -> str | None:
    value = node.get(key)
    if isinstance(value, str) and value.strip():
        return variables.resolve_text(value.strip())
    return None


def _read_log_level(node: FlowNode) -> TaskLogLevel:
    value = _read_optional_string(node, "logLevel") or _read_optional_string(node, "level") or "info"
    return value if value in _LOG_LEVELS else "info"


def _read_optional_string(node: FlowNode, key: str) -> str | None:
    value = node.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _append_saved_name(saved_names: list[str], name: str) -> None:
    normalized_name = normalize_variable_name(name)
    if normalized_name not in saved_names:
        saved_names.append(normalized_name)
