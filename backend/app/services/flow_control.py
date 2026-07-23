"""Condition evaluation and branch-routing logic for control-flow nodes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.services.runtime_variables import RuntimeVariableStore

type FlowEdge = dict[str, object]
type FlowNode = dict[str, object]

_BARE_VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,119}$")
_NUMERIC_PATTERN = re.compile(r"^-?(?:\d+\.?\d*|\.\d+)$")
_COMPARISON_OPERATORS = ("==", "!=", ">=", "<=", ">", "<")
_TRUE_BRANCH_LABELS = {"1", "true", "yes", "y", "是", "真", "成功", "then", "if-true", "true-branch"}
_FALSE_BRANCH_LABELS = {"0", "false", "no", "n", "否", "假", "失败", "else", "if-false", "false-branch"}
_CONTROL_NODE_TYPES = {"control.step", "control.condition", "condition.step", "condition"}
_MAX_EXPRESSION_LENGTH = 500


def is_condition_node(node: FlowNode, outgoing_edges: list[FlowEdge] | None = None) -> bool:
    node_type = node.get("type")
    if node_type not in _CONTROL_NODE_TYPES:
        return False

    if read_condition_expression(node) is None:
        return False

    if node_type in {"control.condition", "condition.step", "condition"}:
        return True

    edges = outgoing_edges or []
    return len(edges) > 1 or any(_read_branch(edge) is not None for edge in edges)


def read_condition_expression(node: FlowNode) -> str | None:
    for key in ("condition", "expression", "inputValue", "description"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def evaluate_condition(node: FlowNode, variables: RuntimeVariableStore) -> bool:
    return evaluate_condition_detail(node, variables).result


@dataclass(frozen=True)
class ConditionEvaluation:
    result: bool
    expression: str
    detail: str


def evaluate_condition_detail(node: FlowNode, variables: RuntimeVariableStore) -> ConditionEvaluation:
    expression = read_condition_expression(node)
    if expression is None:
        raise ValueError("条件节点缺少 condition/expression/inputValue/description")
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ValueError("条件表达式不能超过 500 个字符")

    operator_match = _find_comparison_operator(expression)
    if operator_match is None:
        value = _parse_operand(expression, variables)
        result = _to_bool(value)
        return ConditionEvaluation(result=result, expression=expression, detail=f"{expression} => {_format_condition_value(value)}")

    left_expression, operator, right_expression = operator_match
    left_value = _parse_operand(left_expression, variables)
    right_value = _parse_operand(right_expression, variables)
    result = _compare_values(left_value, operator, right_value)
    return ConditionEvaluation(
        result=result,
        expression=expression,
        detail=(
            f"{left_expression}={_format_condition_value(left_value)} "
            f"{operator} {right_expression}={_format_condition_value(right_value)}"
        ),
    )


def select_branch_edges(outgoing_edges: list[FlowEdge], condition_result: bool) -> list[FlowEdge]:
    """无标签边时按位置顺序（第一条为 true）兜底。"""
    expected_branch: Literal["true", "false"] = "true" if condition_result else "false"
    matched = [edge for edge in outgoing_edges if _read_branch(edge) == expected_branch]
    if matched:
        return matched

    if len(outgoing_edges) == 2:
        return [outgoing_edges[0] if condition_result else outgoing_edges[1]]

    return []


def _find_comparison_operator(expression: str) -> tuple[str, str, str] | None:
    # 逐字符扫描并跟踪引号状态，避免把字符串字面量内部的 "==" ">" 等字符误判成比较运算符。
    quote: str | None = None
    escaped = False
    for index, char in enumerate(expression):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote is not None:
            escaped = True
            continue
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
            continue
        if quote is not None:
            continue
        for operator in _COMPARISON_OPERATORS:
            if expression.startswith(operator, index):
                left_expression = expression[:index].strip()
                right_expression = expression[index + len(operator) :].strip()
                if not left_expression or not right_expression:
                    raise ValueError(f"条件表达式缺少比较值: {expression}")
                return left_expression, operator, right_expression
    return None


def _parse_operand(expression: str, variables: RuntimeVariableStore) -> object:
    value = expression.strip()
    if not value:
        raise ValueError("条件表达式包含空值")

    return _parse_literal_or_bare(value, variables)


def _parse_literal_or_bare(value: str, variables: RuntimeVariableStore) -> object:
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1].replace(r"\"", '"').replace(r"\'", "'").replace(r"\\", "\\")

    lower_value = value.lower()
    if lower_value == "true":
        return True
    if lower_value == "false":
        return False
    if lower_value in {"null", "none"}:
        return None
    if _NUMERIC_PATTERN.match(value):
        return float(value) if "." in value else int(value)
    if _BARE_VARIABLE_PATTERN.match(value):
        return variables.get(value)

    raise ValueError(f"不支持的条件表达式值: {value}")


def _compare_values(left_value: object, operator: str, right_value: object) -> bool:
    left_number = _to_number(left_value)
    right_number = _to_number(right_value)
    if operator in {">", ">=", "<", "<="}:
        if left_number is None or right_number is None:
            raise ValueError("大小比较仅支持数字变量和值")
        if operator == ">":
            return left_number > right_number
        if operator == ">=":
            return left_number >= right_number
        if operator == "<":
            return left_number < right_number
        return left_number <= right_number

    if left_number is not None and right_number is not None:
        is_equal = left_number == right_number
    else:
        is_equal = _normalize_equality_value(left_value) == _normalize_equality_value(right_value)
    return is_equal if operator == "==" else not is_equal


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "no", "n", "否", "假"}:
            return False
        if normalized in {"1", "true", "yes", "y", "是", "真"}:
            return True
    return value is not None


def _to_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and _NUMERIC_PATTERN.match(value.strip()):
        return float(value.strip())
    return None


def _normalize_equality_value(value: object) -> object:
    if isinstance(value, str):
        lower_value = value.strip().lower()
        if lower_value == "true":
            return True
        if lower_value == "false":
            return False
        number = _to_number(value)
        return number if number is not None else value
    return value


def _format_condition_value(value: object) -> str:
    if isinstance(value, str):
        return repr(value)
    return str(value)


def _read_branch(edge: FlowEdge) -> Literal["true", "false"] | None:
    for key in ("label", "sourceHandle", "targetHandle"):
        value = edge.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if normalized in _TRUE_BRANCH_LABELS:
            return "true"
        if normalized in _FALSE_BRANCH_LABELS:
            return "false"
    return None
