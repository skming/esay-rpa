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


def _pdf_contract() -> FlowAcceptanceContract:
    return FlowAcceptanceContract.model_validate({
        "deliverables": [{
            "id": "report",
            "variable": "report_path",
            "kind": "document",
            "requiredTerms": ["帖子总结"],
            "sourceVariables": ["topic_text"],
        }],
    })


def test_correct_pdf_passes_and_only_notes_that_its_body_is_unverifiable(tmp_path: Path) -> None:
    """回归：一份内容完全正确的 PDF 曾被判成缺少来源数据。

    按 UTF-8 读 PDF 拿到的是容器字节，requiredTerms / sourceVariables 的逐字比对必然落空。
    同一条失败两次触发质量熔断，流程锁死在一个它无论如何都满足不了的判据上。
    """
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.7\n" + b"x" * 512)

    result = audit_acceptance_contract(
        _pdf_contract(),
        {"report_path": "report.pdf", "topic_text": "楼主问考编与就业的取舍"},
        [],
        workspace_root=tmp_path,
    )

    assert result["passed"] is True
    assert result["issues"] == []
    assert [warning["issue"] for warning in result["warnings"]] == ["document_content_not_text_verifiable"]


def test_empty_shell_pdf_still_fails(tmp_path: Path) -> None:
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.7\n")

    result = audit_acceptance_contract(
        _pdf_contract(),
        {"report_path": "report.pdf", "topic_text": "楼主问考编与就业的取舍"},
        [],
        workspace_root=tmp_path,
    )

    assert result["passed"] is False
    assert [issue["issue"] for issue in result["issues"]] == ["document_binary_too_small"]


def test_pdf_extension_with_a_non_pdf_body_fails(tmp_path: Path) -> None:
    report = tmp_path / "report.pdf"
    report.write_bytes(b"# markdown wearing a pdf extension\n" + b"x" * 512)

    result = audit_acceptance_contract(
        _pdf_contract(),
        {"report_path": "report.pdf", "topic_text": "楼主问考编与就业的取舍"},
        [],
        workspace_root=tmp_path,
    )

    assert result["passed"] is False
    assert [issue["issue"] for issue in result["issues"]] == ["document_binary_header_mismatch"]


def _markdown_contract() -> FlowAcceptanceContract:
    return FlowAcceptanceContract.model_validate({
        "deliverables": [{
            "id": "summary",
            "variable": "md_path",
            "kind": "document",
            "sourceVariables": ["post_texts"],
        }],
    })


def test_document_keeps_passing_after_the_script_reflows_the_body(tmp_path: Path) -> None:
    """脚本写文档时会重排换行与缩进，来源变量又通常是一个 list——两者都不能让判据落空。

    片段若取自来源变量的 JSON 序列化，开头就是 `["`，正文里永远不会出现；比对时不压空白，
    一处折行就算「没搬数据」。两种误判都会让内容完全正确的文档反复被打回。
    """
    doc = tmp_path / "summary.md"
    doc.write_text("# 总结\n\n1. 全系支持 92 号、95 号、98\n   号汽油；\n", encoding="utf-8")

    result = audit_acceptance_contract(
        _markdown_contract(),
        {"md_path": "summary.md", "post_texts": ["全系支持 92 号、95 号、98 号汽油；\n"]},
        [],
        workspace_root=tmp_path,
    )

    assert result["passed"] is True, result["issues"]


def test_document_written_entirely_from_boilerplate_is_rejected(tmp_path: Path) -> None:
    """事故复盘：正文整篇由脚本写出，把需求原话写成标题就能骗过关键词判据。

    模型当时明说要「让文档正文显式包含需求关键词后重新验收」——比修抽取节点便宜得多。
    所以判据只能比抓取值，不能比它自己写的字。
    """
    doc = tmp_path / "summary.md"
    doc.write_text("# 帖子内容总结\n\n## 生成总结\n\n" + "本文档为交付说明。\n" * 20, encoding="utf-8")

    result = audit_acceptance_contract(
        _markdown_contract(),
        {"md_path": "summary.md", "post_texts": ["全系支持 92 号、95 号、98 号汽油；", "感觉都是文字游戏"]},
        [],
        workspace_root=tmp_path,
    )

    assert result["passed"] is False
    assert [issue["issue"] for issue in result["issues"]] == ["document_missing_source_data"]
