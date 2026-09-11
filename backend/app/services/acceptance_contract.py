from __future__ import annotations

from typing import Any

from app.models.schemas import FlowAcceptanceContract
from app.services.execution_evidence import collect_node_output_names

# 低于这个长度的输入值不做「被包含」判定：1-2 个字的值（"3"、"是"）几乎必然出现在
# 某个无关词里，按包含算会把正常契约判成错。等值判定不受此限制。
_MIN_CONTAINED_VALUE_CHARS = 4


def contract_validation_errors(
    contract: FlowAcceptanceContract,
    *,
    defined_variables: set[str] | None = None,
    input_values: dict[str, Any] | None = None,
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
        if deliverable.kind != "document":
            if deliverable.source_variables:
                errors.append(
                    f"交付物 {deliverable.id} 的 sourceVariables 仅适用于 document；"
                    "请将正文声明为 document 交付物，并将文件路径另列为 file 交付物"
                )
            if deliverable.min_chars is not None:
                errors.append(f"交付物 {deliverable.id} 的 minChars 仅适用于 document")
        if deliverable.kind == "document":
            errors.extend(_document_provenance_errors(deliverable, defined_variables))
        if input_values:
            errors.extend(_pinned_input_value_errors(deliverable, input_values))
    return errors


def _pinned_input_value_errors(deliverable, input_values: dict[str, Any]) -> list[str]:
    """契约冻结在流程上、换输入值重放（_replay_variants），而 required_terms 是纯字面子串
    判定（acceptance_audit._audit_text_constraints）：把本次输入的 "2026-06-01" 写进
    required_terms，下一次换日期重放时回读正确却仍报 required_terms_missing。

    错误文本只给字段名、下标、deliverable id、命中的变量名。orchestrator 把 ValidationError
    原样回灌给模型，输入变量还带 sensitive 标记，带上值等于把它漏回去。
    """
    errors: list[str] = []
    for field_name, terms in (("requiredTerms", deliverable.required_terms), ("forbiddenTerms", deliverable.forbidden_terms)):
        for index, term in enumerate(terms):
            hit = _matched_input_variable(term, input_values)
            if hit:
                errors.append(
                    f"交付物 {deliverable.id} 的 {field_name}[{index}] 等于或包含输入变量 {hit} 的本次取值；"
                    "契约会以别的输入重放，把某次输入值冻进判据会让其它输入必然判失败"
                )
    return errors


def _matched_input_variable(term: str, input_values: dict[str, Any]) -> str | None:
    if not isinstance(term, str) or not term:
        return None
    for name, value in input_values.items():
        text = value if isinstance(value, str) else ("" if value is None else str(value))
        if not text:  # 空值：任何串都「包含」空串，跳过
            continue
        if term == text:
            return name
        if len(text) >= _MIN_CONTAINED_VALUE_CHARS and text in term:
            return name
    return None


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
