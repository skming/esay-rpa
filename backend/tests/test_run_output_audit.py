from __future__ import annotations

from app.services.ai_tools.diagnostics import _detect_mixed_ui_rows


def test_long_body_text_containing_control_words_is_not_a_ui_row() -> None:
    """回归：一条 500 字的论坛评论因为含「不确定」被判成分页控件行。

    当时是把整行拼接后做子串扫描，「不确定」命中关键词「确定」，
    审计连判三次不合格，助手反复收窄一个本来就正确的 selector 直到撞上 failure budget。
    """
    rows = [
        {"序号": i, "类型": "回复", "内容": "他走的是考编这条路，追求稳定；你未来收入有很大的不确定，" * 8}
        for i in range(1, 21)
    ]

    assert _detect_mixed_ui_rows(rows) is None


def test_real_pagination_row_is_still_caught() -> None:
    rows = [{"a": "上一页", "b": "1", "c": "下一页"}, *({"a": f"数据{i}", "b": "x", "c": "y"} for i in range(20))]

    issue = _detect_mixed_ui_rows(rows)

    assert issue is not None
    assert issue["ui_like_row_indexes"] == [0]
    # 只给行号，模型无从判断是真噪声还是误报
    assert issue["ui_like_row_samples"][0]["text"].startswith("上一页")


def test_a_couple_of_noise_rows_only_warn_instead_of_failing_the_run() -> None:
    """1/100 行噪声推翻整次抓取，只会让助手重改一个能交付的流程。"""
    rows: list[dict[str, str]] = [{"a": f"数据{i}", "b": "x"} for i in range(79)]
    rows.insert(40, {"a": "下一页", "b": ""})

    issue = _detect_mixed_ui_rows(rows)

    assert issue is not None
    assert issue["severity"] == "warning"


def test_calendar_panel_rows_stay_blocking() -> None:
    rows = [{str(i): day for i, day in enumerate("日一二三四五六")} for _ in range(6)]

    issue = _detect_mixed_ui_rows(rows)

    assert issue is not None
    assert issue["severity"] == "blocking"
