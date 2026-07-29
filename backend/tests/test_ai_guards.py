"""每条编排护栏的触发路径。

护栏是「模型明知规则仍会犯」时唯一还起作用的东西，但它平时不响：正常会话里
一条都不触发，重构把某条改瞎了不会有任何症状，直到线上出现一次本该被拦下的
昂贵空转。所以这里按 guard id 逐条断言触发，末尾再反向检查有没有新增了却
没人测的 guard——护栏清单能被枚举，本来就是为了这件事。

模型行为侧的验证（模型撞上拦截后是否真的改道）在 evals/run_evals.py。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.ai_guards import (
    GUARDS,
    MAX_CONSECUTIVE_INSPECT_PAGE,
    NODE_SELECTOR_FIX_BUDGET,
    apply_pre_tool_guards,
    describe_guards,
    guard_contract_lines,
)


def _blocked_by(tool: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    result = apply_pre_tool_guards(tool, args, state)
    assert result is not None, f"{tool} 本应被拦截"
    assert result["status"] == "blocked_by_orchestrator_guard"
    assert result["blocked_tool"] == tool
    return result


def test_read_only_mode_blocks_every_write_but_no_diagnosis() -> None:
    state = {"read_only_tools": True}
    for tool in ("create_flow", "update_flow", "apply_node_fix", "run_flow", "publish_flow"):
        assert _blocked_by(tool, {}, state)["guard_id"] == "read_only_mode"
    # 诊断手段一个都不能收——只读模式的产物就是一份根因分析
    for tool in ("get_run_error", "inspect_page", "lint_flow", "get_flow"):
        assert apply_pre_tool_guards(tool, {}, state) is None


def test_execution_channel_preservation_catches_delete_rewrite_and_edge_cut() -> None:
    state = {
        "repair_intent": "preserve_execution_channel",
        "browser_chain_node_ids": {"n_open", "n_extract"},
        "browser_chain_edges_by_id": {"e1": ("n_open", "n_extract")},
    }

    # ① 直接删主链路节点
    direct = _blocked_by("update_flow", {"remove_node_ids": ["n_open"]}, state)
    assert direct["guard_id"] == "execution_channel_preservation"
    assert direct["violations"][0]["issue"] == "repair_removed_existing_nodes"

    # ② 把主链路节点改写成脚本抓取
    rewrite = _blocked_by(
        "apply_node_fix",
        {"node_id": "n_extract", "config_patch": {"type": "script.python", "code": "requests.get(url)"}},
        state,
    )
    assert {v["issue"] for v in rewrite["violations"]} == {
        "repair_replaced_node_with_script", "repair_uses_script_http_fetch",
    }

    # ③ 节点没删，但连线全被剪断且没接回——功能上等同于删除
    edge_cut = _blocked_by("update_flow", {"remove_edge_ids": ["e1"]}, state)
    assert any(
        v["issue"] == "repair_orphaned_browser_chain_node_via_edges" for v in edge_cut["violations"]
    )

    # 追加新节点是被允许的修复方式，不能一起挡掉
    assert apply_pre_tool_guards(
        "update_flow", {"add_nodes": [{"id": "n_wait", "type": "browser.wait"}]}, state
    ) is None


def test_screenshot_is_blocked_only_for_models_without_vision() -> None:
    assert _blocked_by("inspect_screenshot", {}, {"model_no_vision": True})["required_tool"] == "inspect_page"
    assert apply_pre_tool_guards("inspect_screenshot", {}, {"model_no_vision": False}) is None


def test_pre_create_gate_cannot_be_bypassed_by_switching_write_tool() -> None:
    """按入口猜的 build_tool 只挡一个工具的话，换个写入工具就绕过去了。"""
    state = {"pre_create_inspect_gate": {"inspect_done": False, "suggested_url": "https://example.com"}}
    for tool in ("create_flow", "update_flow", "apply_node_fix"):
        blocked = _blocked_by(tool, {}, state)
        assert blocked["guard_id"] == "pre_create_inspect_gate"
        assert blocked["suggested_args"]["url"] == "https://example.com"

    state["pre_create_inspect_gate"]["inspect_done"] = True
    assert apply_pre_tool_guards("create_flow", {}, state) is None


def test_consecutive_inspect_limit_forces_a_change_of_strategy() -> None:
    state = {"consecutive_inspect_page_count": MAX_CONSECUTIVE_INSPECT_PAGE - 1}
    assert apply_pre_tool_guards("inspect_page", {}, state) is None

    state["consecutive_inspect_page_count"] = MAX_CONSECUTIVE_INSPECT_PAGE
    blocked = _blocked_by("inspect_page", {}, state)
    assert blocked["guard_id"] == "consecutive_inspect_limit"
    # 截图与 DOM 探测共用计数，否则两个工具轮流调就绕过去了
    assert _blocked_by("inspect_screenshot", {}, state)["guard_id"] == "consecutive_inspect_limit"
    assert "create_flow" in blocked["allowed_next_tools"]


def test_repair_cycle_lock_stops_writes_and_runs_but_keeps_diagnosis() -> None:
    state = {"repair_cycle_lock": {"cycles": 3, "last_error": "timeout"}}
    for tool in ("update_flow", "apply_node_fix", "create_flow", "run_flow"):
        blocked = _blocked_by(tool, {}, state)
        assert blocked["guard_id"] == "repair_cycle_lock"
        assert blocked["required_action"] == "report_to_user_and_stop"
        # 拦截要能直接变成一句对用户说的话，否则用户只看到一个空气泡
        assert blocked["user_message"]
    assert apply_pre_tool_guards("get_run_error", {}, state) is None


def test_quality_budget_lock_blocks_retry_of_the_same_failed_direction() -> None:
    state = {"quality_budget_lock": {"issue": "mixed_ui_rows", "count": 2}}
    for tool in ("update_flow", "run_flow"):
        assert _blocked_by(tool, {}, state)["guard_id"] == "quality_budget_lock"
    # 单节点精准修复是这道闸给出的出路，不能连它一起挡
    assert apply_pre_tool_guards("apply_node_fix", {}, state) is None


def test_challenge_page_lock_stops_edits_and_reruns_against_the_same_wall() -> None:
    state = {"challenge_page_lock": {"url": "https://site.example/post-1", "label": "人机验证拦截页"}}
    for tool in ("create_flow", "update_flow", "apply_node_fix", "run_flow"):
        assert _blocked_by(tool, {}, state)["guard_id"] == "challenge_page_lock"
    # 重探同一个 URL 就是撞同一堵墙，第一轮丢节点正是这么循环出来的
    blocked = _blocked_by("inspect_page", {"url": "https://site.example/post-1"}, state)
    assert blocked["required_action"] == "needs_human_verification"
    # 换个地址去看仍是正当动作，挡掉等于没收了模型唯一还能用的眼睛
    assert apply_pre_tool_guards("inspect_page", {"url": "https://other.example/"}, state) is None


def test_navigation_budget_lock_asks_the_user_for_a_target_url() -> None:
    state = {"navigation_budget_lock": {"node_id": "n_menu", "count": 2}}
    blocked = _blocked_by("run_flow", {}, state)
    assert blocked["guard_id"] == "navigation_budget_lock"
    assert blocked["needed_from_user"]
    assert "inspect_page" in blocked["allowed_next_tools"]


def test_failure_budget_lock_keeps_every_read_only_tool_available() -> None:
    """挡掉纯读工具等于没收诊断手段，模型只能在剩下几个工具间空转。"""
    state = {"failure_budget_lock": {"flow_id": "f1"}}
    for tool in ("list_node_types", "get_run_output", "validate_flow", "get_flow",
                 "get_run_error", "inspect_page", "inspect_screenshot", "apply_node_fix"):
        assert apply_pre_tool_guards(tool, {}, state) is None
    for tool in ("update_flow", "run_flow", "create_flow"):
        assert _blocked_by(tool, {}, state)["guard_id"] == "failure_budget_lock"


def test_inspect_hint_forces_dom_evidence_before_any_further_move() -> None:
    state = {"requires_inspect_page": {"url": "https://example.com/list"}}
    assert _blocked_by("run_flow", {}, state)["guard_id"] == "requires_inspect_page"
    assert _blocked_by("apply_node_fix", {}, state)["guard_id"] == "requires_inspect_page"
    for tool in ("inspect_page", "inspect_screenshot", "get_run_error", "get_run_logs", "get_flow", "lint_flow"):
        assert apply_pre_tool_guards(tool, {}, state) is None


def test_failed_audit_blocks_rerun_and_hands_back_the_repair_plan() -> None:
    state = {"requires_quality_fix": {"issues": [{"issue": "x"}], "repair_plan": ["改 selector"]}}
    blocked = _blocked_by("run_flow", {}, state)
    assert blocked["guard_id"] == "requires_quality_fix"
    assert blocked["repair_plan"] == ["改 selector"]
    # 修复本身必须放行，否则模型无路可走
    assert apply_pre_tool_guards("update_flow", {}, state) is None


def test_blocking_lint_findings_block_the_run() -> None:
    state = {"requires_lint_fix": [{"severity": "error", "issue": "single_navigation_node"}]}
    blocked = _blocked_by("run_flow", {}, state)
    assert blocked["guard_id"] == "requires_lint_fix"
    assert blocked["lint_findings"] == state["requires_lint_fix"]


def test_field_oscillation_blocks_reverting_to_a_known_failed_value() -> None:
    state = {"node_field_history": {"n1.selector": [".a", ".b"]}}
    # .b 是当前值，重复写入属幂等，不该拦
    assert apply_pre_tool_guards("apply_node_fix", {"node_id": "n1", "config_patch": {"selector": ".b"}}, state) is None

    blocked = _blocked_by("apply_node_fix", {"node_id": "n1", "config_patch": {"selector": ".a"}}, state)
    assert blocked["guard_id"] == "field_oscillation"
    # 同一字段只提醒一次，否则模型改口后的每次写入都会再撞一遍
    assert apply_pre_tool_guards("apply_node_fix", {"node_id": "n1", "config_patch": {"selector": ".a"}}, state) is None


def test_selector_fix_budget_requires_new_page_evidence() -> None:
    state = {"node_selector_fix_counts": {"n1": NODE_SELECTOR_FIX_BUDGET}}
    blocked = _blocked_by("apply_node_fix", {"node_id": "n1", "config_patch": {"selector": ".new"}}, state)
    assert blocked["guard_id"] == "node_selector_fix_budget"
    assert blocked["blocked_node_ids"] == ["n1"]

    # 改的是别的字段就不该挡：这道闸针对的是盲改 selector
    assert apply_pre_tool_guards("apply_node_fix", {"node_id": "n1", "config_patch": {"timeoutMs": 5000}}, state) is None
    # 拿到新页面证据后放行
    state["fresh_page_evidence"] = True
    assert apply_pre_tool_guards("apply_node_fix", {"node_id": "n1", "config_patch": {"selector": ".new"}}, state) is None


def test_repair_intent_requires_diagnosis_before_touching_nodes() -> None:
    state = {"pending_repair_gate": {"lint_done": False, "inspect_done": False}}
    blocked = _blocked_by("apply_node_fix", {}, state)
    assert blocked["guard_id"] == "pending_repair_gate"
    assert blocked["required_tools"] == ["lint_flow", "inspect_page"]

    state["pending_repair_gate"]["lint_done"] = True
    assert _blocked_by("update_flow", {}, state)["required_tools"] == ["inspect_page"]

    state["pending_repair_gate"]["inspect_done"] = True
    assert apply_pre_tool_guards("update_flow", {}, state) is None


def test_repair_intent_does_not_get_to_run_the_flow_on_its_own() -> None:
    """提示词里也写着同一条，但模型该跑还是跑——这道闸是唯一拦得住的。"""
    state = {"repair_autorun_lock": True}
    blocked = _blocked_by("run_flow", {}, state)
    assert blocked["guard_id"] == "repair_autorun_lock"
    assert blocked["required_action"] == "ask_user"

    # 只挡运行：诊断和修复本身是用户要的，挡掉就什么也交付不了
    assert apply_pre_tool_guards("lint_flow", {}, state) is None
    assert apply_pre_tool_guards("apply_node_fix", {}, state) is None

    # 锁只在本轮挂着，用户下一句说「跑一下」时不该还被拦
    assert apply_pre_tool_guards("run_flow", {}, {"repair_autorun_lock": None}) is None


def test_requirement_text_and_confirmation_bit_are_taken_over_by_the_system() -> None:
    """被审计方自己填需求、自己勾确认位，对齐检查就永远命中不了真实需求。"""
    state = {"user_requirement_text": "抓 2024 年的订单"}
    args = {"requirement_text": "修复 selector", "content_match_confirmed": True}
    assert apply_pre_tool_guards("assert_run_output", args, state) is None
    assert args["requirement_text"] == "抓 2024 年的订单"
    assert args["content_match_confirmed"] is False
    assert state["requirement_text_overridden"] and state["content_match_confirm_stripped"]

    # 工具确实报过内容不匹配之后，确认位才作数
    state["content_mismatch_reported"] = True
    args2 = {"content_match_confirmed": True}
    apply_pre_tool_guards("assert_run_output", args2, state)
    assert args2["content_match_confirmed"] is True


def test_coarse_circuit_breakers_win_over_fine_grained_gates() -> None:
    """顺序即优先级：模型每轮换个节点改，按节点计数的闸一条都触发不了。"""
    state = {
        "repair_cycle_lock": {"cycles": 3},
        "pending_repair_gate": {"lint_done": False, "inspect_done": False},
        "requires_lint_fix": [{"severity": "error"}],
    }
    assert _blocked_by("update_flow", {}, state)["guard_id"] == "repair_cycle_lock"

    # 只读模式是调用方的授权边界，任何业务闸都不该排在它前面
    state["read_only_tools"] = True
    assert _blocked_by("update_flow", {}, state)["guard_id"] == "read_only_mode"


def test_guard_ids_are_unique_and_ordered_by_precedence() -> None:
    ids = [guard.id for guard in GUARDS]
    assert len(ids) == len(set(ids))
    assert [row["precedence"] for row in describe_guards()] == list(range(len(GUARDS)))
    # 契约行是注入 system prompt 的，缺了 summary/contract 的条目会变成空 bullet
    assert all(line.strip() != "-" for line in guard_contract_lines())
    assert all(guard.summary for guard in GUARDS)


def test_every_guard_has_a_test_in_this_file() -> None:
    """新增 guard 却没人测，是这套护栏最容易出现的退化方式。"""
    source = Path(__file__).read_text(encoding="utf-8")
    untested = [guard.id for guard in GUARDS if f'"{guard.id}"' not in source]
    assert not untested, f"以下 guard 没有触发用例：{untested}"
