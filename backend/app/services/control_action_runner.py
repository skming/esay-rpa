from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.models.schemas import ScrapeResult
from app.services.runtime_variables import RuntimeVariableStore

type FlowNode = dict[str, object]

_CONTROL_ACTION_NODE_TYPES = {"control.delay", "control.break", "control.noop", "control.retry", "control.try"}
_SUBPROCESS_NODE_TYPES = {"control.subprocess"}
# 本 runner 不执行它（暂停等人的逻辑要拿 TaskRecord 上的 Event，只能留在 task_manager），
# 但类型判断放这里跟其他 control.* 一处，省得类型名散在多个文件里各写一份字面量。
HUMAN_TAKEOVER_NODE_TYPE = "control.human_takeover"
_MAX_DELAY_MS = 300_000  # 单次延时上限 5 分钟，防止误配置的巨大延时把任务挂死


class BreakLoopSignal(RuntimeError):
    """用于从循环体内安全跳出，避免把主动中断误判为运行失败。"""


@dataclass(frozen=True)
class ControlActionResult:
    action_type: str
    detail: str
    values: list[str]

    def to_scrape_result(self) -> ScrapeResult:
        return ScrapeResult(url="", selector=self.action_type, count=len(self.values), values=self.values)


class ControlActionRunner:
    async def run(self, node: FlowNode, variables: RuntimeVariableStore, *, timeout_ms: int) -> ControlActionResult:
        action_type = _read_action_type(node)
        if action_type == "control.delay":
            delay_ms = _read_delay_ms(node, variables)
            effective_timeout_ms = max(timeout_ms, delay_ms + 1000)
            await asyncio.wait_for(asyncio.sleep(delay_ms / 1000), timeout=max(1, effective_timeout_ms) / 1000)
            return ControlActionResult(action_type=action_type, detail=f"{delay_ms}ms", values=[str(delay_ms)])

        if action_type == "control.break":
            raise BreakLoopSignal("中断循环")

        if action_type == "control.noop":
            detail = _read_optional_string(node, "description") or "控制节点占位"
            return ControlActionResult(action_type=action_type, detail=detail, values=[detail])

        if action_type == "control.retry":
            # 注意：当前仅回显 retryCount/delayMs 配置，不在此处实际执行重试循环——
            # 重试行为需配合流程拓扑（如自身连回上游节点的边）实现，本节点本身不循环。
            retry_count_raw = node.get("retryCount", node.get("maxIterations", 3))
            retry_count = int(retry_count_raw) if isinstance(retry_count_raw, (int, float)) and not isinstance(retry_count_raw, bool) else 3
            delay_ms_raw = node.get("delayMs", 2000)
            delay_ms = int(delay_ms_raw) if isinstance(delay_ms_raw, (int, float)) and not isinstance(delay_ms_raw, bool) else 2000
            detail = f"最多 {retry_count} 次 · 间隔 {delay_ms}ms"
            return ControlActionResult(action_type=action_type, detail=detail, values=[str(retry_count)])

        if action_type == "control.try":
            error_variable = _read_optional_string(node, "errorVariable")
            if error_variable:
                variables.set(error_variable, "", scope="局部")
            return ControlActionResult(action_type=action_type, detail="try 块开始", values=[""])

        raise ValueError(f"不支持的控制节点类型: {action_type}")


def is_control_action_node(node: FlowNode) -> bool:
    return node.get("type") in _CONTROL_ACTION_NODE_TYPES


def is_subprocess_node(node: FlowNode) -> bool:
    return node.get("type") in _SUBPROCESS_NODE_TYPES


def is_human_takeover_node(node: FlowNode) -> bool:
    return node.get("type") == HUMAN_TAKEOVER_NODE_TYPE


def apply_control_result_variables(node: FlowNode, result: ControlActionResult, variables: RuntimeVariableStore) -> list[str]:
    saved_names: list[str] = []
    output_variable = _read_optional_string(node, "outputVariable") or _read_optional_string(node, "responseVariable") or _read_optional_string(node, "resultVariable")
    if output_variable is not None:
        variables.set(output_variable, result.values[0] if len(result.values) == 1 else result.values, scope="局部")
        saved_names.append(output_variable)
    return saved_names


def _read_action_type(node: FlowNode) -> str:
    value = node.get("type")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("控制节点缺少 type")
    return value.strip()


def _read_delay_ms(node: FlowNode, variables: RuntimeVariableStore) -> int:
    raw_value = node.get("delayMs", node.get("durationMs", node.get("timeoutMs", 1000)))
    if isinstance(raw_value, str):
        rendered = variables.resolve_text(raw_value).strip()
        try:
            value = int(rendered)
        except ValueError as exc:
            raise ValueError("等待延时必须是毫秒整数") from exc
    elif isinstance(raw_value, int) and not isinstance(raw_value, bool):
        value = raw_value
    else:
        value = 1000

    if value < 0:
        raise ValueError("等待延时不能小于 0")
    return min(value, _MAX_DELAY_MS)


def _read_optional_string(node: FlowNode, key: str) -> str | None:
    value = node.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
