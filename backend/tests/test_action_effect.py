"""动作目标状态的判据回归。

页面指纹只能回答「整页有没有变」：不动指纹的动作会被报成「没生效，换目标」，只让焦点变化的
死按钮又会被报成「有变化」。这里按动作逐个钉住结论——判错的方向是丢掉成功的操作，或宣布成功。
"""
from __future__ import annotations

from app.services.ai_tools.page_observation import describe_action_effect


def _fill(before: str, after: str, want: str, page_diff: dict | None = None) -> dict:
    return describe_action_effect(
        "fill", want,
        {"tag": "input", "focused": True, "value": before},
        {"tag": "input", "focused": True, "value": after},
        page_diff=page_diff,
    )


def test_focused_fill_that_lands_is_not_reported_as_ineffective() -> None:
    # 第二次填已聚焦的输入框：焦点没变、DOM 没变，指纹一动不动，但值确实写进去了
    out = _fill("", "2026-06-01", "2026-06-01")
    assert out["status"] == "target_reached"
    assert "断言" in out["note"]  # 写进去了不等于组件收下了，得靠抓回的数据断言


def test_filling_the_same_value_again_does_not_demand_a_new_target() -> None:
    out = _fill("2026-06-01", "2026-06-01", "2026-06-01")
    assert out["status"] == "already_in_target_state"


def test_a_value_the_component_rewrote_is_reported_as_not_reached() -> None:
    # 组件自己格式化或直接吃掉输入：以回读值为准，换格式重试，不是加 delayMs
    out = _fill("", "2026/06/01", "2026-06-01")
    assert out["status"] == "target_not_reached"
    assert "delayMs" in out["note"]


def test_native_select_switching_options_counts_as_reached() -> None:
    out = describe_action_effect(
        "select_option", "上海",
        {"tag": "select", "selected": ["bj"], "selected_text": ["北京"]},
        {"tag": "select", "selected": ["sh"], "selected_text": ["上海"]},
        page_diff={},
    )
    assert out["status"] == "target_reached"
    assert out["state_diff"]["selected"] == [["bj"], ["sh"]]


def test_scroll_that_moved_is_reached_even_with_no_page_change() -> None:
    out = describe_action_effect(
        "scroll", None,
        {"tag": "div", "scroll": {"top": 0, "left": 0, "max": 900}},
        {"tag": "div", "scroll": {"top": 400, "left": 0, "max": 900}},
        page_diff={},
    )
    assert out["status"] == "target_reached"


def test_scroll_at_the_bottom_is_idempotent_not_a_failure() -> None:
    out = describe_action_effect(
        "scroll", None,
        {"tag": "div", "scroll": {"top": 900, "left": 0, "max": 900}},
        {"tag": "div", "scroll": {"top": 900, "left": 0, "max": 900}},
        page_diff={},
    )
    assert out["status"] == "already_in_target_state"
    assert "换翻页方式" in out["note"]


def test_scroll_that_never_moves_points_at_the_wrong_container() -> None:
    out = describe_action_effect(
        "scroll", None,
        {"tag": "div", "scroll": {"top": 0, "left": 0, "max": 900}},
        {"tag": "div", "scroll": {"top": 0, "left": 0, "max": 900}},
        page_diff={},
    )
    assert out["status"] == "target_not_reached"
    assert "overflow" in out["note"]


def test_a_dead_button_that_only_took_focus_is_not_a_state_change() -> None:
    # 焦点变化会让整页指纹的 active 变。把它算成变化，死按钮就会被报成 state_changed。
    out = describe_action_effect(
        "click", None,
        {"tag": "button", "focused": False, "text": "查询"},
        {"tag": "button", "focused": True, "text": "查询"},
        page_diff={"active": [None, "button"]},
    )
    assert out["status"] == "focus_only"


def test_clicking_something_inert_reports_missing_evidence_not_failure() -> None:
    state = {"tag": "span", "focused": False, "text": "标签"}
    out = describe_action_effect("click", None, state, dict(state), page_diff={})
    assert out["status"] == "no_observable_change"
    assert "不等于失败" in out["note"]
    assert "delayMs" in out["note"]


def test_a_click_that_opened_a_panel_is_a_state_change() -> None:
    out = describe_action_effect(
        "click", None,
        {"tag": "input", "focused": True, "expanded": "false"},
        {"tag": "input", "focused": True, "expanded": "true"},
        page_diff={"layers": [0, 1]},
    )
    assert out["status"] == "state_changed"


def test_unreadable_target_state_is_unknown_not_failure() -> None:
    # 元素被替换或移出文档：读不到状态只能说读不到，宣布失败会让模型推翻一次成功的操作
    out = describe_action_effect("click", None, {"tag": "div"}, {}, page_diff={"layers": [0, 1]})
    assert out["status"] == "unknown"


def test_interaction_receipt_preserves_uncertainty_and_wait_timeout():
    from app.services.ai_tools.page_observation import build_interaction_result

    result = build_interaction_result(
        "click", "e1", None, {"status": "no_observable_change", "note": "缺少证据"},
        {"changed": False, "diff": {}}, {}, {"status": "timed_out", "selector": "#panel"},
    )
    assert result["warning"] == "缺少证据"
    assert "business_check" not in result
    assert result["wait_result"]["status"] == "timed_out"
