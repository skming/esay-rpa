from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.schemas import FlowAcceptanceContract
from app.services.execution_evidence import collect_node_output_names

# 低于这个长度的输入值不做「被包含」判定：1-2 个字的值（"3"、"是"）几乎必然出现在
# 某个无关词里，按包含算会把正常契约判成错。等值判定不受此限制。
_MIN_CONTAINED_VALUE_CHARS = 4

# 行数界与个位数输入值相等几乎必然是巧合（重试次数、并发数都常是 2、3），按冻结判会把
# 正常契约判成错。两位数以上才归因到输入变量。
# simplified: 纯阈值判据；等契约里能声明「这个界来自哪条需求」再按来源判。
_MIN_PINNED_ROW_BOUND = 10

# 翻页/迭代上限字段：它们决定「最多翻几页」，决定不了「应该有多少行」。按字段名判而不按
# 节点类型判——换个节点类型只要用同名字段，同一条判据仍然成立。
_PAGE_CAP_FIELDS = ("maxIterations", "maxPages", "pageStep", "startPage")

_VARIABLE_REF = re.compile(r"\$\{var\.([A-Za-z_][A-Za-z0-9_.-]{0,119})\}")


@dataclass(frozen=True)
class PaginationCaps:
    """翻页上限由输入变量控制的节点：cap_variables 是上限变量名，output_variables 是这些节点的产出。"""

    cap_variables: frozenset[str]
    output_variables: frozenset[str]


def pagination_caps_from_nodes(nodes: Any) -> PaginationCaps:
    cap_variables: set[str] = set()
    output_variables: set[str] = set()
    for node in nodes if isinstance(nodes, list) else ():
        if not isinstance(node, dict):
            continue
        names = {
            match.group(1)
            for field_name in _PAGE_CAP_FIELDS
            if isinstance(node.get(field_name), str)
            for match in _VARIABLE_REF.finditer(node[field_name])
        }
        if names:
            cap_variables |= names
            output_variables.update(collect_node_output_names(node))
    return PaginationCaps(frozenset(cap_variables), frozenset(output_variables))


def contract_validation_errors(
    contract: FlowAcceptanceContract,
    *,
    defined_variables: set[str] | None = None,
    input_values: dict[str, Any] | None = None,
    pagination_caps: PaginationCaps | None = None,
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
            deliverable.min_rows_variable,
            deliverable.max_rows_variable,
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
        if defined_variables is not None:
            unknown_bindings = sorted({
                name for name in (
                    deliverable.min_rows_variable,
                    deliverable.max_rows_variable,
                    *(name for constraint in deliverable.allowed_values for name in constraint.value_variables),
                )
                if name and name not in defined_variables
            })
            if unknown_bindings:
                errors.append(f"交付物 {deliverable.id} 绑定的约束变量 {unknown_bindings} 未由流程节点或输入变量产出")
        if deliverable.kind == "document":
            errors.extend(_document_provenance_errors(deliverable, defined_variables))
        if input_values:
            errors.extend(_pinned_input_value_errors(deliverable, input_values))
        if pagination_caps is not None:
            errors.extend(_page_cap_row_bound_errors(deliverable, pagination_caps))
    return errors


def _page_cap_row_bound_errors(deliverable, caps: PaginationCaps) -> list[str]:
    """翻页上限是「最多翻几页」，不是「应该有多少行」：max_pages=2 抓回 2 行完全正确，
    写死的 min_rows=3 却把它判成缺数据。两者不能互相换算——一页几行只有跑完才知道。
    """
    errors: list[str] = []
    for field_name, variable_name in (
        ("minRowsVariable", deliverable.min_rows_variable),
        ("maxRowsVariable", deliverable.max_rows_variable),
    ):
        if variable_name and variable_name in caps.cap_variables:
            errors.append(
                f"交付物 {deliverable.id} 的 {field_name} 绑定了翻页上限变量 {variable_name}；"
                "页数不等于行数，一页几行只有跑完才知道"
            )
    if deliverable.variable not in caps.output_variables:
        return errors
    for field_name, literal in (("minRows", deliverable.min_rows), ("maxRows", deliverable.max_rows)):
        if literal is not None:
            errors.append(
                f"交付物 {deliverable.id} 的 {field_name} 写成了字面量，但 {deliverable.variable} 由翻页上限变量 "
                f"{sorted(caps.cap_variables)} 控制的节点产出，行数随输入变化；"
                f"确有固定业务界请用 {field_name}Variable 绑定行数变量，否则删掉这一条"
            )
    return errors


def _pinned_input_value_errors(deliverable, input_values: dict[str, Any]) -> list[str]:
    """契约冻结在流程上、换输入值重放（_replay_variants），而 required_terms 是纯字面子串
    判定（acceptance_audit._audit_text_constraints）：把本次输入的 "2026-06-01" 写进
    required_terms，下一次换日期重放时回读正确却仍报 required_terms_missing。
    allowedValues 的字面量与行数界同理，出路是改用 valueVariables / *RowsVariable 绑定。

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
    for index, constraint in enumerate(deliverable.allowed_values):
        for value_index, value in enumerate(constraint.values):
            hit = _matched_input_variable(value, input_values)
            if hit:
                errors.append(
                    f"交付物 {deliverable.id} 的 allowedValues[{index}].values[{value_index}] 等于或包含输入变量 "
                    f"{hit} 的本次取值；随输入变化的筛选值请改用 valueVariables 绑定该变量"
                )
    for field_name, literal in (("minRows", deliverable.min_rows), ("maxRows", deliverable.max_rows)):
        hit = _matched_row_bound_variable(literal, input_values)
        if hit:
            errors.append(
                f"交付物 {deliverable.id} 的 {field_name} 等于输入变量 {hit} 的本次取值；"
                f"换输入重放时它不再成立，请改用 {field_name}Variable 绑定该变量"
            )
    return errors


def _matched_row_bound_variable(literal: int | None, input_values: dict[str, Any]) -> str | None:
    if literal is None or literal < _MIN_PINNED_ROW_BOUND:
        return None
    for name, value in input_values.items():
        text = str(value).strip()
        if text.isdigit() and int(text) == literal:
            return name
    return None


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
