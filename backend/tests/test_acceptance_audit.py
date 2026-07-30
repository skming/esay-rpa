from __future__ import annotations

from pathlib import Path

from app.models.schemas import FlowAcceptanceContract, NodeExecutionEvidence
from app.services.acceptance_audit import audit_acceptance_contract


def test_table_contract_accepts_valid_rows_without_guessing_content_shape(tmp_path: Path) -> None:
    contract = FlowAcceptanceContract.model_validate({
        "deliverables": [{
            "id": "orders",
            "variable": "order_rows",
            "kind": "table",
            "minRows": 2,
            "requiredFields": ["id", "date", "status", "description"],
            "dateRanges": [{"field": "date", "start": "2026-01-01", "end": "2026-01-31"}],
            "allowedValues": [{"field": "status", "values": ["已完成", "待处理"]}],
            "uniqueBy": ["id"],
        }],
    })
    variables = {
        "order_rows": [
            {"id": 1001, "date": "2026-01-03", "status": "已完成", "description": "首笔订单"},
            {"id": 1002, "date": "2026-01-04", "status": "待处理", "description": "次笔订单"},
        ],
    }

    result = audit_acceptance_contract(contract, variables, [], workspace_root=tmp_path)

    assert result["passed"] is True
    assert result["issues"] == []


def test_explicit_forbidden_term_is_a_blocking_postcondition(tmp_path: Path) -> None:
    contract = FlowAcceptanceContract.model_validate({
        "deliverables": [{
            "id": "article",
            "variable": "cleaned_text",
            "kind": "document",
            "forbiddenTerms": ["登录", "Cloudflare"],
        }],
    })

    result = audit_acceptance_contract(
        contract,
        {"cleaned_text": "正文内容\n登录后查看更多"},
        [],
        workspace_root=tmp_path,
    )

    assert result["passed"] is False
    assert [issue["issue"] for issue in result["issues"]] == ["forbidden_terms_present"]


def test_unchanged_transform_evidence_is_warning_not_a_guessed_failure(tmp_path: Path) -> None:
    contract = FlowAcceptanceContract.model_validate({
        "deliverables": [{
            "id": "article",
            "variable": "cleaned_text",
            "kind": "document",
            "requiredTerms": ["正文"],
        }],
    })
    evidence = [NodeExecutionEvidence.model_validate({
        "nodeId": "clean",
        "nodeType": "script.python",
        "unchangedPairs": ["raw_text->cleaned_text"],
    })]

    result = audit_acceptance_contract(
        contract,
        {"cleaned_text": "正文内容本来已经符合要求"},
        evidence,
        workspace_root=tmp_path,
    )

    assert result["passed"] is True
    assert result["issues"] == []
    assert [warning["issue"] for warning in result["warnings"]] == ["transform_unchanged"]


def test_file_contract_rejects_paths_outside_the_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract = FlowAcceptanceContract.model_validate({
        "deliverables": [{"id": "export", "variable": "export_path", "kind": "file"}],
    })

    result = audit_acceptance_contract(
        contract,
        {"export_path": str(outside)},
        [],
        workspace_root=workspace,
    )

    assert result["passed"] is False
    assert result["issues"][0]["issue"] == "file_path_outside_workspace"


def test_table_contract_checks_numeric_format_sort_aggregate_and_coverage(tmp_path: Path) -> None:
    contract = FlowAcceptanceContract.model_validate({
        "deliverables": [{
            "id": "orders",
            "variable": "rows",
            "kind": "table",
            "numericRanges": [{"field": "amount", "minimum": 0}],
            "fieldFormats": [{"field": "email", "format": "email"}],
            "crossFieldAssertions": [{"leftField": "paid", "operator": "lte", "rightField": "amount"}],
            "sortAssertions": [{"field": "created", "direction": "desc"}],
            "aggregateAssertions": [{"field": "amount", "operation": "sum", "expected": 30}],
            "expectedCountVariable": "page_total",
            "minimumCoverageRatio": 1,
        }],
    })
    variables = {
        "page_total": 2,
        "rows": [
            {"amount": "20", "paid": "20", "email": "a@example.com", "created": "2026-02-02"},
            {"amount": "10", "paid": "5", "email": "b@example.com", "created": "2026-02-01"},
        ],
    }

    result = audit_acceptance_contract(contract, variables, [], workspace_root=tmp_path)

    assert result["passed"] is True


def test_table_contract_reports_incomplete_page_coverage(tmp_path: Path) -> None:
    contract = FlowAcceptanceContract.model_validate({
        "deliverables": [{
            "id": "orders",
            "variable": "rows",
            "kind": "table",
            "expectedCountVariable": "page_total",
            "minimumCoverageRatio": 0.9,
        }],
    })

    result = audit_acceptance_contract(
        contract,
        {"rows": [{"id": 1}], "page_total": 10},
        [],
        workspace_root=tmp_path,
    )

    assert result["passed"] is False
    assert result["issues"][0]["issue"] == "coverage_ratio_violation"
