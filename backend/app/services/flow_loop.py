from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from app.services.runtime_variables import RuntimeVariableStore, normalize_variable_name

type FlowEdge = dict[str, object]
type FlowNode = dict[str, object]

_LOOP_NODE_TYPES = {"control.foreach"}
_BODY_EDGE_LABELS = {"body", "loop", "loop-body", "foreach-body", "each", "iterate", "true", "yes", "是", "循环", "循环体", "每项", "迭代"}
_EXIT_EDGE_LABELS = {"exit", "done", "complete", "loop-exit", "foreach-exit", "false", "no", "否", "完成", "结束", "退出", "跳出"}
# 节点自身可配置 maxIterations，但无论如何都会被硬顶到 10000——防止 AI/用户配置失误
# 或数据源异常导致循环节点失控跑成千上万轮，拖垮任务。
_DEFAULT_MAX_ITERATIONS = 1000
_HARD_MAX_ITERATIONS = 10_000


_REPEAT_UNTIL_NODE_TYPES = {"control.repeat_until"}
# 循环次数由运行时决定的场景（翻到目标月份、点到没有「加载更多」为止）此前无法表达，
# 只能展开成写死次数的节点链——那个次数是生成当天算出来的，之后每次运行都是错的。
_DEFAULT_REPEAT_MAX_ITERATIONS = 50
# 与条件节点共用 inputValue，属性面板和 AI 才不用记两套字段名；
# 但不接受 description 兜底——把描述文字误当退出条件会让循环第一轮就退出或永不退出，两种都不报错。
_REPEAT_UNTIL_CONDITION_KEYS = ("condition", "untilCondition", "expression", "inputValue")


@dataclass(frozen=True)
class RepeatUntilConfig:
    index_variable: str
    max_iterations: int
    fail_on_max: bool


def is_repeat_until_node(node: FlowNode) -> bool:
    return node.get("type") in _REPEAT_UNTIL_NODE_TYPES


def read_repeat_until_config(node: FlowNode) -> RepeatUntilConfig:
    # 与条件节点不同，这里不接受从 description 兜底读表达式：把描述文字误当退出条件，
    # 会让循环在第一轮就退出或永不退出，而两种都不会报错。
    read_repeat_until_expression(node)  # 缺失即抛错
    return RepeatUntilConfig(
        index_variable=normalize_variable_name(_read_optional_string(node, "indexVariable") or "repeat_index"),
        max_iterations=_read_max_iterations(node, default=_DEFAULT_REPEAT_MAX_ITERATIONS),
        # 跑满上限说明退出条件始终没满足，即目标状态没达成。默认必须让运行失败：
        # 静默退出会让流程带着「翻月没翻到位」这种错误状态继续跑完并返回错数据。
        fail_on_max=node.get("continueOnMaxIterations") is not True,
    )


def read_repeat_until_expression(node: FlowNode) -> str:
    for key in _REPEAT_UNTIL_CONDITION_KEYS:
        value = _read_optional_string(node, key)
        if value:
            return value
    raise ValueError("repeat_until 节点缺少 condition（退出条件表达式）")


@dataclass(frozen=True)
class LoopConfig:
    items_variable: str
    item_variable: str
    index_variable: str
    items: list[object]
    max_iterations: int

    @property
    def planned_iterations(self) -> int:
        return min(len(self.items), self.max_iterations)

    @property
    def truncated(self) -> bool:
        return len(self.items) > self.max_iterations


def is_loop_node(node: FlowNode) -> bool:
    return node.get("type") in _LOOP_NODE_TYPES


def read_loop_config(node: FlowNode, variables: RuntimeVariableStore) -> LoopConfig:
    items_variable = _read_optional_string(node, "itemsVariable") or _read_optional_string(node, "listVariable") or _read_optional_string(node, "inputVariable")
    if items_variable is None:
        raise ValueError("循环节点缺少 itemsVariable")

    item_variable = normalize_variable_name(_read_optional_string(node, "itemVariable") or "current_item")
    index_variable = normalize_variable_name(_read_optional_string(node, "indexVariable") or "loop_index")
    items = _normalize_loop_items(variables.get(items_variable), items_variable=items_variable)
    max_iterations = _read_max_iterations(node)
    return LoopConfig(
        items_variable=normalize_variable_name(items_variable),
        item_variable=item_variable,
        index_variable=index_variable,
        items=items,
        max_iterations=max_iterations,
    )


def materialize_loop_item(item: object) -> object:
    """循环项若是字符串且形如 JSON 数组/对象（常见于历史步骤写回的 items 变量），尝试还原为对象供下游取字段；解析失败则原样返回字符串。"""
    if not isinstance(item, str):
        return item
    normalized = item.strip()
    if not normalized or normalized[0] not in "[{":
        return item
    try:
        return json.loads(normalized)
    except json.JSONDecodeError:
        return item


def split_loop_edges(
    outgoing_edges: list[FlowEdge],
    *,
    loop_node_id: str | None = None,
    adjacency: dict[str, list[FlowEdge]] | None = None,
) -> tuple[list[FlowEdge], list[FlowEdge]]:
    body_edges: list[FlowEdge] = []
    exit_edges: list[FlowEdge] = []
    unknown_edges: list[FlowEdge] = []

    for edge in outgoing_edges:
        role = _read_loop_edge_role(edge)
        if role == "body":
            body_edges.append(edge)
        elif role == "exit":
            exit_edges.append(edge)
        else:
            unknown_edges.append(edge)

    if not body_edges and unknown_edges:
        # 无显式标签时靠可达性判断：能绕回循环节点自身（成环）的边即为 body 边。
        if loop_node_id and adjacency and len(unknown_edges) >= 2:
            for edge in unknown_edges:
                target = read_edge_target(edge)
                if target and _can_reach_node(target, loop_node_id, adjacency, visited=set()):
                    body_edges.append(edge)
                else:
                    exit_edges.append(edge)
            if not body_edges:
                body_edges.append(unknown_edges[0])
                exit_edges.extend(unknown_edges[1:])
        else:
            body_edges.append(unknown_edges[0])
            exit_edges.extend(unknown_edges[1:])
    else:
        exit_edges.extend(unknown_edges)

    return body_edges, exit_edges


def _can_reach_node(start: str, target: str, adjacency: dict[str, list[FlowEdge]], visited: set[str]) -> bool:
    if start in visited:
        return False
    visited.add(start)
    for edge in adjacency.get(start, []):
        next_id = read_edge_target(edge)
        if next_id is None:
            continue
        if next_id == target:
            return True
        if _can_reach_node(next_id, target, adjacency, visited):
            return True
    return False


def read_edge_target(edge: FlowEdge) -> str | None:
    target = edge.get("target")
    return target if isinstance(target, str) else None


def _normalize_loop_items(value: object, *, items_variable: str) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [{"key": key, "value": item} for key, item in value.items()]
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return []
        try:
            decoded = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise ValueError(f"循环变量必须是列表或 JSON 数组: {items_variable}") from exc
        return _normalize_loop_items(decoded, items_variable=items_variable)
    raise ValueError(f"循环变量必须是列表: {items_variable}")


def _read_max_iterations(node: FlowNode, *, default: int = _DEFAULT_MAX_ITERATIONS) -> int:
    raw_value = node.get("maxIterations", node.get("limit", default))
    if isinstance(raw_value, bool):
        raise ValueError("循环 maxIterations 必须是正整数")
    if isinstance(raw_value, int):
        value = raw_value
    elif isinstance(raw_value, str) and raw_value.strip().isdigit():
        value = int(raw_value.strip())
    else:
        value = default

    if value < 1:
        raise ValueError("循环 maxIterations 必须大于 0")
    return min(value, _HARD_MAX_ITERATIONS)


def _read_loop_edge_role(edge: FlowEdge) -> Literal["body", "exit"] | None:
    for key in ("label", "sourceHandle", "targetHandle"):
        value = edge.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if normalized in _BODY_EDGE_LABELS:
            return "body"
        if normalized in _EXIT_EDGE_LABELS:
            return "exit"
    return None


def _read_optional_string(node: FlowNode, key: str) -> str | None:
    value = node.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
