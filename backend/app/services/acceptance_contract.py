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
        if deliverable.kind == "document":
            errors.extend(_document_provenance_errors(deliverable, defined_variables))
    return errors


def _document_provenance_errors(deliverable, defined_variables: set[str] | None) -> list[str]:
    """文档型交付必须声明正文取自哪些运行变量。

    没有这层声明，审计只能拿文档正文比对需求关键词——那等于让模型用自己写的标题自证。
    `source_variables` 是唯一能让「正文里确实有本次抓取的数据」成为可验证判据的入口，
    缺了它，document_missing_source_data 会被静默跳过，审计看起来通过了但什么也没验。
    """
    errors: list[str] = []
    if not deliverable.source_variables:
        errors.append(f"文档交付物 {deliverable.id} 必须用 sourceVariables 声明正文取自哪些运行变量")
    if deliverable.variable in deliverable.source_variables:
        errors.append(f"文档交付物 {deliverable.id} 的 sourceVariables 不能包含它自己，拿文档比对自己证明不了任何事")
    if defined_variables is not None:
        unknown = sorted({name for name in deliverable.source_variables if name not in defined_variables})
        if unknown:
            errors.append(f"文档交付物 {deliverable.id} 的来源变量 {unknown} 未由流程节点或输入变量产出")
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
