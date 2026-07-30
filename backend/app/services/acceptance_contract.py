from __future__ import annotations

from app.models.schemas import FlowAcceptanceContract
from app.services.execution_evidence import collect_node_output_names


def contract_validation_errors(
    contract: FlowAcceptanceContract,
    *,
    defined_variables: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not contract.requirements:
        errors.append("验收契约缺少可追溯的 requirements")
    if not contract.deliverables:
        errors.append("验收契约至少需要一个 deliverable")
    elif not any(deliverable.required for deliverable in contract.deliverables):
        errors.append("验收契约至少需要一个 required=true 的 deliverable")

    for requirement in contract.requirements:
        if requirement.confidence < 0.75:
            errors.append(f"需求条款 {requirement.id} 置信度过低，必须先向用户确认")

    for deliverable in contract.deliverables:
        if not deliverable.requirement_ids:
            errors.append(f"交付物 {deliverable.id} 没有关联 requirementIds")
        if defined_variables is not None and deliverable.variable not in defined_variables:
            errors.append(f"交付变量 {deliverable.variable} 未由流程节点或输入变量产出")
        if (
            defined_variables is not None
            and deliverable.expected_count_variable
            and deliverable.expected_count_variable not in defined_variables
        ):
            errors.append(f"覆盖率总数字段 {deliverable.expected_count_variable} 未由流程产出")
        table_only = any((
            deliverable.min_rows is not None,
            deliverable.max_rows is not None,
            deliverable.required_fields,
            deliverable.date_ranges,
            deliverable.allowed_values,
            deliverable.unique_by,
            deliverable.numeric_ranges,
            deliverable.field_formats,
            deliverable.cross_field_assertions,
            deliverable.sort_assertions,
            deliverable.aggregate_assertions,
            deliverable.expected_count_variable,
        ))
        if deliverable.kind != "table" and table_only:
            errors.append(f"交付物 {deliverable.id} 使用了仅适用于 table 的断言")
    return errors


def unmatched_user_quotes(contract: FlowAcceptanceContract, user_text: str) -> list[str]:
    normalized = " ".join(user_text.split())
    return [
        requirement.id
        for requirement in contract.requirements
        if requirement.source_kind == "user"
        and " ".join((requirement.source_quote or "").split()) not in normalized
    ]


def definition_variable_names(
    definition: dict[str, object],
    input_variable_names: list[str],
) -> set[str]:
    names = set(input_variable_names)
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return names
    for node in nodes:
        if isinstance(node, dict):
            names.update(collect_node_output_names(node))
    return names
