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
