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
