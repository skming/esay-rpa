from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.schemas import FlowAcceptanceContract, NodeExecutionEvidence


def audit_acceptance_contract(
    contract: FlowAcceptanceContract,
    variables: dict[str, Any],
    evidence: list[NodeExecutionEvidence],
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    inspected: list[dict[str, Any]] = []
    for deliverable in contract.deliverables:
        if deliverable.variable not in variables:
            if deliverable.required:
                issues.append({
                    "issue": "deliverable_missing",
                    "deliverable_id": deliverable.id,
                    "message": f"验收契约要求变量 `{deliverable.variable}`，本次运行没有产出。",
                })
            continue
        value = variables[deliverable.variable]
        inspected.append({
            "id": deliverable.id,
            "variable": deliverable.variable,
            "kind": deliverable.kind,
        })
        if deliverable.kind == "table":
            issues.extend(_audit_table(deliverable, value, variables))
        elif deliverable.kind == "document":
            issues.extend(_audit_document(deliverable, value, variables, workspace_root))
        elif deliverable.kind == "file":
            issues.extend(_audit_file(deliverable, value, workspace_root))
        else:
            issues.extend(_audit_text_constraints(deliverable, _render(value)))

    for node in evidence:
        if node.unchanged_pairs:
            warnings.append({
                "issue": "transform_unchanged",
                "node_id": node.node_id,
                "message": (
                    f"节点 `{node.node_id}` 的输入输出内容指纹相同：{node.unchanged_pairs}。"
                    "这只说明本次运行没有产生变化；是否不合格由验收契约的后置条件决定。"
                ),
            })
    return {
        "passed": not issues,
        "issues": issues,
        "warnings": warnings,
        "deliverables": inspected,
    }


def _audit_table(deliverable, value: Any, variables: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return [_issue(deliverable, "deliverable_not_table", "交付变量不是数组，无法按表格验收。")]
    rows = value
    issues: list[dict[str, Any]] = []
    if deliverable.min_rows is not None and len(rows) < deliverable.min_rows:
        issues.append(_issue(deliverable, "too_few_rows", f"实际 {len(rows)} 行，小于要求的 {deliverable.min_rows} 行。"))
    if deliverable.max_rows is not None and len(rows) > deliverable.max_rows:
        issues.append(_issue(deliverable, "too_many_rows", f"实际 {len(rows)} 行，大于要求的 {deliverable.max_rows} 行。"))
    dict_rows = [row for row in rows if isinstance(row, dict)]
    if deliverable.required_fields:
        if len(dict_rows) != len(rows):
            issues.append(_issue(deliverable, "rows_not_objects", "契约要求具名字段，但部分数据行不是对象。"))
        missing = sorted({field for field in deliverable.required_fields if any(field not in row for row in dict_rows)})
        if missing:
            issues.append(_issue(deliverable, "required_fields_missing", f"缺少必需字段：{missing}。"))
    for constraint in deliverable.date_ranges:
        invalid = [
            row.get(constraint.field)
            for row in dict_rows
            if not _date_in_range(row.get(constraint.field), constraint.start, constraint.end)
        ]
        if invalid:
            issues.append(_issue(deliverable, "date_range_violation", f"字段 `{constraint.field}` 有 {len(invalid)} 行超出日期范围。"))
    for constraint in deliverable.allowed_values:
        allowed = set(constraint.values)
        invalid = sorted({str(row.get(constraint.field, "")) for row in dict_rows if str(row.get(constraint.field, "")) not in allowed})
        if invalid:
            issues.append(_issue(deliverable, "allowed_values_violation", f"字段 `{constraint.field}` 出现非法值：{invalid[:10]}。"))
    for constraint in deliverable.numeric_ranges:
        invalid = [
            row.get(constraint.field)
            for row in dict_rows
            if not _number_in_range(row.get(constraint.field), constraint.minimum, constraint.maximum)
        ]
        if invalid:
            issues.append(_issue(deliverable, "numeric_range_violation", f"字段 `{constraint.field}` 有 {len(invalid)} 行不是合法数值或超出范围。"))
    for constraint in deliverable.field_formats:
        invalid = [row.get(constraint.field) for row in dict_rows if not _matches_format(row.get(constraint.field), constraint.format)]
        if invalid:
            issues.append(_issue(deliverable, "field_format_violation", f"字段 `{constraint.field}` 有 {len(invalid)} 行不符合 {constraint.format} 格式。"))
    for constraint in deliverable.cross_field_assertions:
        invalid = [
            row for row in dict_rows
            if not _compare(row.get(constraint.left_field), constraint.operator, row.get(constraint.right_field))
        ]
        if invalid:
            issues.append(_issue(
                deliverable,
                "cross_field_violation",
                f"{len(invalid)} 行不满足 `{constraint.left_field} {constraint.operator} {constraint.right_field}`。",
            ))
    for constraint in deliverable.sort_assertions:
        values = [row.get(constraint.field) for row in dict_rows]
        if not _is_sorted(values, descending=constraint.direction == "desc"):
            issues.append(_issue(deliverable, "sort_order_violation", f"字段 `{constraint.field}` 未按 {constraint.direction} 排序。"))
    for constraint in deliverable.aggregate_assertions:
        actual = _aggregate(dict_rows, constraint.operation, constraint.field)
        if actual is None or not _compare_numbers(actual, constraint.operator, constraint.expected, constraint.tolerance):
            issues.append(_issue(
                deliverable,
                "aggregate_assertion_violation",
                f"聚合 `{constraint.operation}({constraint.field or '*'})` 实际为 {actual}，不满足 {constraint.operator} {constraint.expected}。",
            ))
    if deliverable.unique_by and dict_rows:
        seen: set[tuple[str, ...]] = set()
        duplicates = 0
        for row in dict_rows:
            key = tuple(_render(row.get(field)) for field in deliverable.unique_by)
            if key in seen:
                duplicates += 1
            seen.add(key)
        if duplicates:
            issues.append(_issue(deliverable, "unique_constraint_violation", f"唯一键 {deliverable.unique_by} 存在 {duplicates} 条重复记录。"))
    if deliverable.expected_count_variable:
        expected = _as_decimal(variables.get(deliverable.expected_count_variable))
        if expected is None or expected < 0:
            issues.append(_issue(deliverable, "coverage_total_invalid", f"覆盖率总数变量 `{deliverable.expected_count_variable}` 不是非负数值。"))
        elif expected > 0 and Decimal(len(rows)) / expected < Decimal(str(deliverable.minimum_coverage_ratio)):
            issues.append(_issue(
                deliverable,
                "coverage_ratio_violation",
                f"实际 {len(rows)} 行，相对页面声明总数 {expected} 的覆盖率低于 {deliverable.minimum_coverage_ratio:.0%}。",
            ))
    issues.extend(_audit_text_constraints(deliverable, _render(value)))
    return issues


def _audit_document(deliverable, value: Any, variables: dict[str, Any], workspace_root: Path) -> list[dict[str, Any]]:
    text = _read_text_value(value, workspace_root)
    if text is None:
        return [_issue(deliverable, "document_unreadable", "文档变量既不是正文，也不指向可读取文件。")]
    issues = _audit_text_constraints(deliverable, text)
    if deliverable.min_chars is not None and len(text.strip()) < deliverable.min_chars:
        issues.append(_issue(deliverable, "document_too_short", f"文档只有 {len(text.strip())} 字符，小于要求的 {deliverable.min_chars}。"))
    for source_name in deliverable.source_variables:
        if source_name not in variables:
            issues.append(_issue(deliverable, "source_variable_missing", f"来源变量 `{source_name}` 不存在。"))
            continue
        source = _render(variables[source_name]).strip()
        probes = [source[:16], source[len(source) // 2: len(source) // 2 + 16]] if len(source) >= 32 else [source]
        if source and not any(probe and probe in text for probe in probes):
            issues.append(_issue(deliverable, "document_missing_source_data", f"文档未包含来源变量 `{source_name}` 的运行数据。"))
    return issues


def _audit_file(deliverable, value: Any, workspace_root: Path) -> list[dict[str, Any]]:
    if not isinstance(value, str):
        return [_issue(deliverable, "file_path_invalid", "文件交付变量不是路径字符串。")]
    path = _resolve_workspace_path(Path(value), workspace_root)
    if path is None:
        return [_issue(deliverable, "file_path_outside_workspace", "交付文件路径超出 RPA 工作区。")]
    try:
        is_file = path.is_file()
    except OSError:
        is_file = False
    if not is_file:
        return [_issue(deliverable, "file_missing", f"交付文件不存在：{value}。")]
    issues: list[dict[str, Any]] = []
    extensions = {item.lower() if item.startswith(".") else f".{item.lower()}" for item in deliverable.extensions}
    if extensions and path.suffix.lower() not in extensions:
        issues.append(_issue(deliverable, "file_extension_mismatch", f"文件扩展名 `{path.suffix}` 不在允许范围 {sorted(extensions)}。"))
    try:
        size = path.stat().st_size
    except OSError:
        return [_issue(deliverable, "file_unreadable", "交付文件存在，但无法读取文件元数据。")]
    if deliverable.min_bytes is not None and size < deliverable.min_bytes:
        issues.append(_issue(deliverable, "file_too_small", f"文件只有 {size} 字节，小于要求的 {deliverable.min_bytes}。"))
    return issues


def _audit_text_constraints(deliverable, text: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    missing = [term for term in deliverable.required_terms if term not in text]
    if missing:
        issues.append(_issue(deliverable, "required_terms_missing", f"缺少必需内容：{missing[:10]}。"))
    forbidden = [term for term in deliverable.forbidden_terms if term in text]
    if forbidden:
        issues.append(_issue(deliverable, "forbidden_terms_present", f"仍包含禁止内容：{forbidden[:10]}。"))
    return issues


def _read_text_value(value: Any, workspace_root: Path) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = Path(value)
    path = _resolve_workspace_path(candidate, workspace_root)
    if candidate.is_absolute() and path is None:
        return None
    try:
        is_file = "\n" not in value and path is not None and path.is_file()
    except OSError:
        is_file = False
    if is_file:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    return value


def _resolve_workspace_path(candidate: Path, workspace_root: Path) -> Path | None:
    """只允许验收运行工作区内的文件，避免变量被用作任意本地文件探针。"""
    try:
        root = workspace_root.resolve()
        path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return path if path.is_relative_to(root) else None


def _date_in_range(value: Any, start: str | None, end: str | None) -> bool:
    text = str(value or "").strip()[:10]
    try:
        parsed = datetime.fromisoformat(text).date()
        start_date = datetime.fromisoformat(start[:10]).date() if start else None
        end_date = datetime.fromisoformat(end[:10]).date() if end else None
    except ValueError:
        return False
    return (start_date is None or parsed >= start_date) and (end_date is None or parsed <= end_date)


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _number_in_range(value: Any, minimum: float | None, maximum: float | None) -> bool:
    parsed = _as_decimal(value)
    if parsed is None:
        return False
    return (
        (minimum is None or parsed >= Decimal(str(minimum)))
        and (maximum is None or parsed <= Decimal(str(maximum)))
    )


def _matches_format(value: Any, format_name: str) -> bool:
    text = str(value or "").strip()
    if format_name == "non_empty":
        return bool(text)
    if format_name == "integer":
        parsed = _as_decimal(value)
        return parsed is not None and parsed == parsed.to_integral_value()
    if format_name == "decimal":
        return _as_decimal(value) is not None
    if format_name == "email":
        return bool(text) and text.count("@") == 1 and "." in text.rsplit("@", 1)[1]
    if format_name == "url":
        return text.startswith(("http://", "https://")) and len(text.split("://", 1)[1]) > 0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return bool(parsed) if format_name == "datetime" else len(text) >= 10


def _comparable(value: Any) -> Decimal | datetime | str | None:
    number = _as_decimal(value)
    if number is not None:
        return number
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text


def _compare(left: Any, operator: str, right: Any) -> bool:
    left_value = _comparable(left)
    right_value = _comparable(right)
    if left_value is None or right_value is None or type(left_value) is not type(right_value):
        return False
    return _apply_operator(left_value, operator, right_value)


def _apply_operator(left: Any, operator: str, right: Any) -> bool:
    return {
        "eq": left == right,
        "ne": left != right,
        "gt": left > right,
        "gte": left >= right,
        "lt": left < right,
        "lte": left <= right,
    }[operator]


def _is_sorted(values: list[Any], *, descending: bool) -> bool:
    comparable = [_comparable(value) for value in values]
    if any(value is None for value in comparable):
        return False
    try:
        return all(
            (left >= right if descending else left <= right)
            for left, right in zip(comparable, comparable[1:], strict=False)
        )
    except TypeError:
        return False


def _aggregate(rows: list[dict[str, Any]], operation: str, field: str | None) -> Decimal | None:
    if operation == "count":
        return Decimal(len(rows))
    if not field:
        return None
    values = [_as_decimal(row.get(field)) for row in rows]
    if not values or any(value is None for value in values):
        return None
    numbers = [value for value in values if value is not None]
    if operation == "sum":
        return sum(numbers, Decimal(0))
    if operation == "avg":
        return sum(numbers, Decimal(0)) / Decimal(len(numbers))
    if operation == "min":
        return min(numbers)
    if operation == "max":
        return max(numbers)
    return None


def _compare_numbers(actual: Decimal, operator: str, expected: float, tolerance: float) -> bool:
    expected_value = Decimal(str(expected))
    tolerance_value = Decimal(str(tolerance))
    if operator == "eq":
        return abs(actual - expected_value) <= tolerance_value
    if operator == "ne":
        return abs(actual - expected_value) > tolerance_value
    return _apply_operator(actual, operator, expected_value)


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _issue(deliverable, issue: str, message: str) -> dict[str, Any]:
    return {"issue": issue, "deliverable_id": deliverable.id, "message": message}
