from __future__ import annotations

from app.models.schemas import FlowAcceptanceContract
from app.services.acceptance_contract import contract_validation_errors, unmatched_user_quotes


def test_contract_requires_traceable_requirements_and_bindings() -> None:
    contract = FlowAcceptanceContract.model_validate({
        "requirements": [{
            "id": "orders",
            "description": "抓取全部订单",
            "sourceKind": "user",
            "sourceQuote": "抓取全部订单",
            "confidence": 1,
            "confirmed": True,
        }],
        "deliverables": [{
            "id": "rows",
            "variable": "order_rows",
            "kind": "table",
            "requirementIds": ["orders"],
        }],
    })

    assert contract_validation_errors(contract, defined_variables={"order_rows"}) == []
    assert unmatched_user_quotes(contract, "请抓取全部订单并导出") == []
    assert unmatched_user_quotes(contract, "只抓取退款订单") == ["orders"]


def test_low_confidence_requirement_cannot_be_frozen() -> None:
    contract = FlowAcceptanceContract.model_validate({
        "requirements": [{
            "id": "recent",
            "description": "最近订单按七天理解",
            "sourceKind": "product_default",
            "confidence": 0.5,
            "confirmed": True,
        }],
        "deliverables": [{
            "id": "rows",
            "variable": "order_rows",
            "kind": "table",
            "requirementIds": ["recent"],
        }],
    })

    assert any("置信度过低" in issue for issue in contract_validation_errors(contract, defined_variables={"order_rows"}))


def _daterange_contract(**deliverable) -> FlowAcceptanceContract:
    return FlowAcceptanceContract.model_validate({
        "requirements": [{
            "id": "r2",
            "description": "按起止日期筛选",
            "sourceKind": "user",
            "sourceQuote": "按起止日期筛选",
            "confidence": 1,
            "confirmed": True,
        }],
        "deliverables": [{
            "id": "d1",
            "variable": "start_readback",
            "kind": "scalar",
            "required": True,
            "requirementIds": ["r2"],
            **deliverable,
        }],
    })


def test_required_terms_cannot_freeze_one_runs_input_value() -> None:
    # holdout_custom_daterange 的真实失败形态：模型把第一次跑的 start_date 写进 requiredTerms，
    # 契约冻在流程上、换日期重放，回读明明是对的也必然报 required_terms_missing。
    errors = contract_validation_errors(
        _daterange_contract(requiredTerms=["2026-06-01"]),
        defined_variables={"start_readback", "start_date"},
        input_values={"start_date": "2026-06-01"},
    )
    assert any("requiredTerms[0]" in issue and "start_date" in issue for issue in errors)


def test_pinned_input_value_error_never_quotes_the_value() -> None:
    # 错误文本会被 orchestrator 原样回灌给模型，输入变量还可能带 sensitive 标记：
    # 带上值或词本身等于把用户数据漏回去。
    errors = contract_validation_errors(
        _daterange_contract(requiredTerms=["订单号 A-20260601-8899"], forbiddenTerms=["A-20260601-8899"]),
        defined_variables={"start_readback", "order_no"},
        input_values={"order_no": "A-20260601-8899"},
    )
    assert len(errors) == 2
    assert all("A-20260601-8899" not in issue for issue in errors)
    assert any("forbiddenTerms[0]" in issue for issue in errors)


def test_short_input_value_is_not_treated_as_contained() -> None:
    # "3" 几乎必然出现在别的词里，按包含算会把正常契约判成错；等值才算。
    assert contract_validation_errors(
        _daterange_contract(requiredTerms=["共 3 页"]),
        defined_variables={"start_readback", "page_count"},
        input_values={"page_count": "3"},
    ) == []


def test_empty_input_value_does_not_flag_every_term() -> None:
    # 输入变量默认值常是空串，任何串都「包含」空串。
    assert contract_validation_errors(
        _daterange_contract(requiredTerms=["已完成"]),
        defined_variables={"start_readback", "start_date"},
        input_values={"start_date": ""},
    ) == []


def test_contract_without_input_values_keeps_its_old_verdict() -> None:
    # 状态块拿不到运行期变量，不传 input_values 时这条判据必须完全沉默。
    assert contract_validation_errors(
        _daterange_contract(requiredTerms=["2026-06-01"]),
        defined_variables={"start_readback"},
    ) == []


def _document_contract(**deliverable) -> FlowAcceptanceContract:
    return FlowAcceptanceContract.model_validate({
        "requirements": [{
            "id": "summary",
            "description": "输出帖子总结",
            "sourceKind": "user",
            "sourceQuote": "输出帖子的总结",
            "confidence": 1,
            "confirmed": True,
        }],
        "deliverables": [{
            "id": "doc",
            "variable": "summary_md",
            "kind": "document",
            "requirementIds": ["summary"],
            **deliverable,
        }],
    })


def test_document_deliverable_must_declare_where_its_body_comes_from() -> None:
    """没有 sourceVariables，「正文里确实有本次抓取的数据」这条判据会被静默跳过。"""
    errors = contract_validation_errors(_document_contract(), defined_variables={"summary_md", "topic_text"})

    assert any("sourceVariables" in error for error in errors)


def test_document_source_variables_must_be_produced_by_the_flow() -> None:
    errors = contract_validation_errors(
        _document_contract(sourceVariables=["topic_text"]),
        defined_variables={"summary_md"},
    )

    assert any("未由流程节点或输入变量产出" in error for error in errors)


def test_document_cannot_cite_itself_as_its_own_source() -> None:
    errors = contract_validation_errors(
        _document_contract(sourceVariables=["summary_md"]),
        defined_variables={"summary_md"},
    )

    assert any("不能包含它自己" in error for error in errors)


def test_document_with_a_real_source_variable_is_accepted() -> None:
    assert contract_validation_errors(
        _document_contract(sourceVariables=["topic_text"]),
        defined_variables={"summary_md", "topic_text"},
    ) == []


def test_file_cannot_silently_accept_document_source_constraints() -> None:
    contract = _document_contract(
        kind="file", variable="post_content", sourceVariables=["post_content"],
    )
    errors = contract_validation_errors(contract, defined_variables={"post_content"})
    assert any("sourceVariables" in error and "document" in error for error in errors)


def test_file_cannot_silently_accept_min_chars() -> None:
    errors = contract_validation_errors(
        _document_contract(kind="file", minChars=1), defined_variables={"summary_md"},
    )
    assert any("minChars" in error and "document" in error for error in errors)


def test_file_without_document_constraints_remains_valid() -> None:
    assert contract_validation_errors(
        _document_contract(kind="file", minBytes=0), defined_variables={"summary_md"},
    ) == []
