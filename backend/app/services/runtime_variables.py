from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.models.schemas import RuntimeVariableSnapshot, ScrapeResult

_VARIABLE_PATTERN = re.compile(r"\$\{var\.([A-Za-z_][A-Za-z0-9_.-]{0,119})\}")
_MAX_TEMPLATE_RESOLUTION_DEPTH = 5  # 防止循环/过深嵌套的模板引用


@dataclass
class RuntimeVariableStore:
    """In-memory key-value store for runtime variables during a single task execution."""
    _values: dict[str, object] = field(default_factory=dict)
    _scopes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_initial(cls, variables: dict[str, object] | None) -> "RuntimeVariableStore":
        store = cls()
        for name, value in (variables or {}).items():
            store.set(name, value, scope="全局")
        return store

    def set(self, name: str, value: object, *, scope: str = "局部") -> None:
        normalized_name = normalize_variable_name(name)
        self._values[normalized_name] = value
        self._scopes[normalized_name] = normalize_scope(scope)

    def get(self, name: str) -> object:
        normalized_name = normalize_variable_name(name)
        if normalized_name in self._values:
            return self._values[normalized_name]

        nested_value = _read_nested_value(normalized_name, self._values)
        if nested_value is _MISSING:
            raise ValueError(f"变量未定义: {normalized_name}")
        return nested_value

    def resolve_text(self, value: str) -> str:
        resolved = value
        for _ in range(_MAX_TEMPLATE_RESOLUTION_DEPTH):
            next_value = _VARIABLE_PATTERN.sub(lambda match: stringify_variable_value(self.get(match.group(1))), resolved)
            if next_value == resolved:
                return next_value
            resolved = next_value
        if _VARIABLE_PATTERN.search(resolved):
            raise ValueError("变量模板嵌套过深")
        return resolved

    def snapshots(self) -> list[RuntimeVariableSnapshot]:
        return [
            RuntimeVariableSnapshot(
                name=name,
                scope=normalize_scope(self._scopes.get(name, "局部")),
                type=infer_variable_type(value),
                value=stringify_variable_value(value),
            )
            for name, value in sorted(self._values.items())
        ]

    def raw_values(self) -> dict[str, object]:
        return dict(self._values)


def resolve_template_value(value: object, variables: RuntimeVariableStore) -> object:
    if isinstance(value, str):
        return variables.resolve_text(value)
    return value


def apply_fetch_result_variables(node: dict[str, object], result: ScrapeResult, variables: RuntimeVariableStore) -> list[str]:
    saved_names: list[str] = []
    rows = list(result.structured) if result.structured is not None else list(result.values)
    # 四个 key 是不同节点 schema 版本遗留的别名，同一节点可能同时携带多个，全部要各自落盘。
    for key in ("outputVariable", "responseVariable", "saveAs", "resultVariable"):
        raw_name = node.get(key)
        if isinstance(raw_name, str) and raw_name.strip():
            variables.set(raw_name, rows, scope="局部")
            saved_names.append(normalize_variable_name(raw_name))

    count_name = node.get("countVariable")
    if isinstance(count_name, str) and count_name.strip():
        variables.set(count_name, result.count, scope="局部")
        saved_names.append(normalize_variable_name(count_name))

    first_value_name = node.get("firstValueVariable")
    if isinstance(first_value_name, str) and first_value_name.strip():
        variables.set(first_value_name, rows[0] if rows else "", scope="局部")
        saved_names.append(normalize_variable_name(first_value_name))

    _append_result_variable(node, variables, values=rows, count=result.count, saved_names=saved_names)
    return saved_names


def normalize_variable_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 120:
        raise ValueError("变量名不能为空且不能超过 120 个字符")
    if not (name[0].isalpha() or name[0] == "_"):
        raise ValueError(f"变量名必须以字母或下划线开头: {value}")
    if not all(char.isalnum() or char in {"_", ".", "-"} for char in name):
        raise ValueError(f"变量名包含非法字符: {value}")
    return name


def infer_variable_type(value: object) -> str:
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "Integer"
    if isinstance(value, list):
        return "List"
    if isinstance(value, dict):
        return "Dict"
    return "String"


def stringify_variable_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | dict):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    return str(value)


def read_variable_name(node: dict[str, object]) -> str:
    for key in ("variableName", "name", "outputVariable"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_variable_name(value)
    raise ValueError("变量节点缺少 variableName")


def read_variable_scope(node: dict[str, object], *, default: str) -> str:
    value = node.get("scope")
    return normalize_scope(value if isinstance(value, str) else default)


def normalize_scope(value: str) -> str:
    if value in {"全局", "循环", "局部"}:
        return value
    return "局部"


_MISSING = object()


def append_variable_values(variables: RuntimeVariableStore, name: str, values: list[object]) -> None:
    normalized_name = normalize_variable_name(name)
    try:
        current = variables.get(normalized_name)
    except ValueError:
        current = []

    if isinstance(current, list):
        next_value = [*current, *values]
    elif current in (None, ""):
        next_value = values
    else:
        next_value = [current, *values]
    variables.set(normalized_name, next_value, scope="局部")


def _append_result_variable(node: dict[str, object], variables: RuntimeVariableStore, *, values: list[object], count: int, saved_names: list[str]) -> None:
    append_variable = node.get("appendVariable", node.get("appendOutputVariable"))
    if not isinstance(append_variable, str) or not append_variable.strip():
        return

    mode = node.get("appendMode")
    if mode == "record":
        payload: list[object] = [
            {
                "count": count,
                "first": values[0] if values else "",
                "values": values,
            }
        ]
    else:
        payload = values
    append_variable_values(variables, append_variable, payload)
    saved_names.append(normalize_variable_name(append_variable))


def _read_nested_value(name: str, values: dict[str, object]) -> object:
    # 支持点路径访问："result.0.name" 解析进嵌套 dict/list。
    parts = name.split(".")
    for split_index in range(len(parts) - 1, 0, -1):
        root_name = ".".join(parts[:split_index])
        if root_name not in values:
            continue

        current: object = values[root_name]
        for part in parts[split_index:]:
            if isinstance(current, dict):
                if part not in current:
                    return _MISSING
                current = current[part]
            elif isinstance(current, list) and part.isdigit():
                index = int(part)
                if index >= len(current):
                    return _MISSING
                current = current[index]
            else:
                return _MISSING
        return current
    return _MISSING
