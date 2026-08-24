from __future__ import annotations

from pathlib import Path

from app.models.schemas import FlowAcceptanceContract
from app.services.acceptance_audit import audit_acceptance_contract
from app.services.ai_tools.diagnostics import _build_quality_repair_plan

# 8e38be17 会话的原始需求：34 次工具调用里有 25 次在给一个「表格」审计造数据。
# 交付形态当时靠扫需求文本猜（出现「markdown」当文档、出现「表格」当表格），
# 猜错就把一篇总结按表格审，助手只能反复造 rows 去迎合。
# 现在形态由契约的 kind 声明，这一整类失败不再可能发生——本文件验的就是「由声明决定」。
_MARKDOWN_SUMMARY = "# 帖子总结\n\n楼主问考编与就业的取舍，回帖普遍认为稳定性值得一部分收入。\n" * 20


def _document_contract() -> FlowAcceptanceContract:
    return FlowAcceptanceContract.model_validate({
        "deliverables": [{
            "id": "summary",
            "variable": "summary_md",
            "kind": "document",
            "minChars": 200,
            "sourceVariables": ["topic_text"],
        }],
    })


def test_document_deliverable_is_not_audited_by_table_clauses(tmp_path: Path) -> None:
    result = audit_acceptance_contract(
        _document_contract(),
        {"summary_md": _MARKDOWN_SUMMARY, "topic_text": "楼主问考编与就业的取舍"},
        [],
        workspace_root=tmp_path,
    )

    assert result["passed"] is True
    assert result["deliverables"] == [{"id": "summary", "variable": "summary_md", "kind": "document"}]


def test_the_declared_kind_decides_the_audit_path_not_the_text(tmp_path: Path) -> None:
    """同一个值，声明成 table 就按表格审——判据是契约，不是文本里出现了哪个词。"""
    contract = FlowAcceptanceContract.model_validate({
        "deliverables": [{"id": "summary", "variable": "summary_md", "kind": "table", "minRows": 1}],
    })

    result = audit_acceptance_contract(
        contract,
        {"summary_md": _MARKDOWN_SUMMARY},
        [],
        workspace_root=tmp_path,
    )

    assert result["passed"] is False
    assert [issue["issue"] for issue in result["issues"]] == ["deliverable_not_table"]


def test_document_missing_run_data_is_reported_against_the_declared_source(tmp_path: Path) -> None:
    """文档正文与本次抓取无交集：判据是契约点名的来源变量，不是需求关键词。"""
    result = audit_acceptance_contract(
        _document_contract(),
        {"summary_md": _MARKDOWN_SUMMARY, "topic_text": "另一个站点抓来的完全无关的一段正文内容"},
        [],
        workspace_root=tmp_path,
    )

    assert result["passed"] is False
    assert [issue["issue"] for issue in result["issues"]] == ["document_missing_source_data"]


def test_unstructured_delivery_carries_an_executable_plan() -> None:
    """「交付物不是按行数据」这条 issue 必须带出可执行步骤，否则助手拿回的是一句无处下手的结论。"""
    plan = _build_quality_repair_plan([{"issue": "deliverable_not_table"}])

    assert plan
    assert plan[0]["action"] == "produce_structured_rows"
    # 形态本身可能声明错了，出路是问用户，不是造一个 rows 变量迎合契约
    assert any("向用户确认交付形态" in step for step in plan[0]["steps"])
