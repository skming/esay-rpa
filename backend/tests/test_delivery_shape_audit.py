from __future__ import annotations

from app.services.ai_tools.diagnostics import (
    _build_quality_repair_plan,
    _describe_output_variables,
    _find_document_output,
    _requirement_wants_document,
)

# 8e38be17 会话的原始需求：34 次工具调用里有 25 次在给一个「表格」审计造数据
_MARKDOWN_REQUIREMENT = "https://www.v2ex.com/t/1225889 输出帖子的总结，markdown形式，无需登录，帖子存在分页"


def test_markdown_summary_requirement_is_not_audited_as_a_table() -> None:
    assert _requirement_wants_document(_MARKDOWN_REQUIREMENT)


def test_explicit_table_words_win_over_document_words() -> None:
    """既说了报告又说了表格时按表格审：形态判错的代价是单向的。"""
    assert not _requirement_wants_document("生成销售报告，导出成 excel 表格")
    assert not _requirement_wants_document("抓取订单清单并总结每一行的状态")


def test_pure_scrape_requirement_stays_on_the_table_path() -> None:
    assert not _requirement_wants_document("抓取该页面所有商品的名称和价格")


def test_document_output_prefers_the_written_file_over_intermediate_text() -> None:
    doc = _find_document_output({
        "topic_text": "正文" * 500,
        "markdown_file": "runs/8e38be17/t_71d2fde2/20260724_164902.md",
        "topic_texts_count": 104,
    })

    assert doc is not None
    assert doc["name"] == "markdown_file"
    assert doc["kind"] == "file"


def test_short_strings_are_not_mistaken_for_documents() -> None:
    assert _find_document_output({"status": "ok", "count": 3}) is None


def test_observed_variables_say_why_each_one_is_not_a_table() -> None:
    """只说「没有表格型变量」不说有什么，模型只能换个写法重试——空转就是这么烧的。"""
    described = _describe_output_variables({
        "paged_topic_texts": ["第一页正文", "第二页正文"],
        "topic_text": "正文",
    })

    by_name = {item["name"]: item for item in described}
    assert "纯文本" in by_name["paged_topic_texts"]["why_not_table"]
    assert by_name["topic_text"]["why_not_table"]


def test_no_table_like_output_now_carries_an_executable_plan() -> None:
    """原来这条 issue 没有任何 repair_plan 模板，助手拿回的是一句无处下手的结论。"""
    plan = _build_quality_repair_plan([{"issue": "no_table_like_output"}])

    assert plan
    assert plan[0]["action"] == "produce_structured_rows"
    # 出路必须是问用户，不能是「换个 requirement_text 再调一次」——
    # requirement_text 由 _enforce_requirement_provenance 强制覆盖，重调必然同样结果
    assert any("向用户确认交付形态" in step for step in plan[0]["steps"])
    assert not any("requirement_text" in step for step in plan[0]["steps"])
