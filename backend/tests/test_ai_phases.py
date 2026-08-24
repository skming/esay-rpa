"""阶段机与收敛判据的每条触发路径。

这十条判定原来是十个独立 guard，各自一个 state 键。现在合成两条判据，所以用例
也按判据写：构造事实，断言推出哪个阶段、哪些工具被拦。

重点守三件重构最容易改瞎、且平时没有任何症状的事：
- 阶段的推导顺序（熔断在前、前置门在后），排错了会把模型引向另一个同样被挡的动作；
- 「读工具永不受阶段约束」这条不变量；
- 预算的计价规则（重复算两份），它是三个旧阈值能被一个数复现的唯一原因。
"""
from __future__ import annotations

from typing import Any

from app.services.ai_phases import (
    EVIDENCE_TOOLS,
    PHASE_GATED_TOOLS,
    PHASE_GUARD_IDS,
    VERIFY_ATTEMPT_BUDGET,
    Phase,
    admitted_tool_names,
    apply_phase_gate,
    describe_phases,
    initial_facts,
    note_evidence,
    note_failed_attempt,
    note_guard_block,
    note_progress,
    note_verified,
    phase_contract_lines,
    resolve_phase,
)

_ALL_TOOLS = frozenset({
    "create_flow", "update_flow", "apply_node_fix", "set_acceptance_contract",
    "run_flow", "publish_flow", "stop_run", "create_schedule", "toggle_schedule",
    "inspect_page", "inspect_screenshot", "get_run_error", "get_run_logs",
    "get_run_output", "list_node_types", "list_schedules",
})


def _ready(**overrides: Any) -> dict[str, Any]:
    """一个可以直接跑流程的会话：流程已存在、证据到手、诊断干净、用户授权。"""
    state = initial_facts(
        flow_has_nodes=True,
        page_evidence_required=None,
        page_evidence_done=True,
        run_authorized=True,
    )
    state.update(overrides)
    return state


def _blocked_by(tool: str, state: dict[str, Any], args: dict[str, Any] | None = None) -> dict[str, Any]:
    result = apply_phase_gate(tool, args or {}, state)
    assert result is not None, f"{tool} 本应被拦截"
    assert result["status"] == "blocked_by_orchestrator_guard"
    assert result["blocked_tool"] == tool
    return result


# ── 阶段推导 ──────────────────────────────────────────────────────────────────


def test_a_ready_session_is_in_verify_and_may_run() -> None:
    state = _ready()
    assert resolve_phase(state) is Phase.VERIFY
    for tool in sorted(PHASE_GATED_TOOLS):
        assert apply_phase_gate(tool, {}, state) is None, tool


def test_missing_page_evidence_puts_the_session_in_discover() -> None:
    state = _ready(page_evidence_required={"url": "https://example.com/list"}, page_evidence_done=False)
    assert resolve_phase(state) is Phase.DISCOVER
    for tool in ("create_flow", "update_flow", "apply_node_fix", "run_flow"):
        blocked = _blocked_by(tool, state)
        assert blocked["guard_id"] == "page_evidence_required"
        assert blocked["suggested_args"]["url"] == "https://example.com/list"
        assert blocked["required_tools"] == ["inspect_page"]

    state["page_evidence_done"] = True
    assert apply_phase_gate("create_flow", {}, state) is None


def test_page_evidence_gate_cannot_be_bypassed_by_switching_write_tool() -> None:
    """旧实现按入口猜一个 build_tool 只挡一个工具，换个写入工具就绕过去了。

    阶段准入按类别定，四个写流程工具是一组，没有「换一个」这条缝。
    """
    state = _ready(page_evidence_required={"url": "https://example.com"}, page_evidence_done=False)
    assert not (PHASE_GATED_TOOLS & set(admitted_tool_names(_ALL_TOOLS, state)))


def test_a_blank_flow_is_in_build_and_cannot_be_run() -> None:
    state = _ready(flow_has_nodes=False)
    assert resolve_phase(state) is Phase.BUILD
    blocked = _blocked_by("run_flow", state)
    assert blocked["guard_id"] == "flow_must_exist_before_run"
    # 建流程本身必须放行，否则这一阶段无路可走
    for tool in ("create_flow", "update_flow", "apply_node_fix", "set_acceptance_contract"):
        assert apply_phase_gate(tool, {}, state) is None, tool


def test_blocking_diagnostics_put_the_session_in_fix_and_stop_the_run() -> None:
    findings = [{"severity": "error", "issue": "single_navigation_node"}]
    state = _ready(blocking_diagnostics=findings)
    assert resolve_phase(state) is Phase.FIX
    blocked = _blocked_by("run_flow", state)
    assert blocked["guard_id"] == "blocking_diagnostics_must_be_fixed"
    assert blocked["lint_findings"] == findings
    # 修复必须放行
    assert apply_phase_gate("update_flow", {}, state) is None


def test_failed_audit_blocks_the_rerun_and_hands_back_the_repair_plan() -> None:
    state = _ready(audit_findings={"issues": [{"issue": "mixed_ui_rows"}], "repair_plan": ["改 selector"]})
    assert resolve_phase(state) is Phase.FIX
    blocked = _blocked_by("run_flow", state)
    assert blocked["guard_id"] == "audit_findings_must_be_fixed"
    assert blocked["repair_plan"] == ["改 selector"]
    assert apply_phase_gate("update_flow", {}, state) is None


def test_lint_findings_are_reported_before_audit_findings() -> None:
    """两者同时存在时报诊断：那是模型手上真正能直接改的东西。"""
    state = _ready(
        blocking_diagnostics=[{"severity": "error"}],
        audit_findings={"issues": [{"issue": "x"}], "repair_plan": ["y"]},
    )
    assert _blocked_by("run_flow", state)["guard_id"] == "blocking_diagnostics_must_be_fixed"


def test_read_tools_are_never_gated_by_phase() -> None:
    """挡掉纯读工具等于没收诊断手段，模型只能在剩下几个工具间空转。

    包含 get_run_output——旧 requires_inspect_page 会挡它，为的是省一轮；
    这里刻意放行，换「诊断手段永不没收」这条不变量没有例外。
    """
    states = [
        _ready(page_evidence_required={"url": "u"}, page_evidence_done=False),
        _ready(flow_has_nodes=False),
        _ready(blocking_diagnostics=[{"severity": "error"}]),
        _ready(attempt_budget={"spent": VERIFY_ATTEMPT_BUDGET, "signatures": {}, "attempts": []}),
    ]
    for state in states:
        for tool in sorted(EVIDENCE_TOOLS | {"list_node_types", "list_schedules"}):
            assert apply_phase_gate(tool, {}, state) is None, (resolve_phase(state), tool)


def test_side_line_tools_are_outside_the_phase_machine() -> None:
    """publish / 定时任务 / stop_run 不在构建—验证主线上，用户随时可能单独要求。"""
    state = _ready(page_evidence_required={"url": "u"}, page_evidence_done=False)
    for tool in ("publish_flow", "create_schedule", "toggle_schedule", "stop_run"):
        assert apply_phase_gate(tool, {}, state) is None, tool


# ── 运行授权 ──────────────────────────────────────────────────────────────────


def test_repair_only_turns_do_not_get_to_run_the_flow() -> None:
    """提示词里也写着同一条，但模型该跑还是跑——这道闸是唯一拦得住的。"""
    state = _ready(run_authorized=False)
    blocked = _blocked_by("run_flow", state)
    assert blocked["guard_id"] == "run_not_authorized"
    # 一步都还没落盘就被拦：这轮还有活要干，不能收尾
    assert blocked["required_action"] == "explain_and_wait"

    # 修复已经写进流程，剩下的只是「要不要跑」——交给用户定，本轮收尾
    landed = _ready(run_authorized=False, current_flow_revision=5)
    assert _blocked_by("run_flow", landed)["required_action"] == "ask_user"

    # 只挡运行：诊断和修复本身是用户要的，挡掉就什么也交付不了
    for tool in ("inspect_page", "apply_node_fix", "update_flow"):
        assert apply_phase_gate(tool, {}, state) is None, tool


def test_a_dirty_flow_reports_the_diagnostics_not_the_authorization() -> None:
    """两条都成立时先说诊断：那是模型下一步能动手的事，授权只是最后一个决定。"""
    state = _ready(run_authorized=False, blocking_diagnostics=[{"severity": "error"}])
    assert _blocked_by("run_flow", state)["guard_id"] == "blocking_diagnostics_must_be_fixed"


# ── 收敛：失败预算 ────────────────────────────────────────────────────────────


def test_three_different_failures_exhaust_the_budget() -> None:
    """旧 MAX_REPAIR_CYCLES=3：不看改的是哪里，只认「又跑了一次又没成」。"""
    state = _ready()
    for index in range(VERIFY_ATTEMPT_BUDGET - 1):
        note_failed_attempt(state, kind="run_error", signature=f"sig-{index}", detail=f"err {index}")
        assert resolve_phase(state) is Phase.VERIFY
    note_failed_attempt(state, kind="run_error", signature="sig-last", detail="err last")
    assert resolve_phase(state) is Phase.REPORT


def test_repeating_the_same_failure_costs_double_and_exhausts_on_the_second() -> None:
    """旧 NAV_FAILURE_BUDGET / 质量预算都是 2。重复计两份让一个数复现两个阈值。"""
    state = _ready()
    note_failed_attempt(state, kind="navigation", signature="n_menu:browser.click:selector_error")
    assert resolve_phase(state) is Phase.VERIFY
    note_failed_attempt(state, kind="navigation", signature="n_menu:browser.click:selector_error")
    assert resolve_phase(state) is Phase.REPORT


def test_changing_the_node_no_longer_buys_a_fresh_budget() -> None:
    """七个计数器各管一个维度时，换个节点改就是一份新额度——这正是要修的洞。"""
    state = _ready()
    for node in ("n_a", "n_b", "n_c"):
        note_failed_attempt(state, kind="run_error", signature=f"{node}:selector_error")
    assert resolve_phase(state) is Phase.REPORT


def test_exhausted_budget_stops_writes_and_runs_but_keeps_diagnosis_and_node_fix() -> None:
    state = _ready()
    note_failed_attempt(state, kind="run_error", signature="s", detail="timeout")
    note_failed_attempt(state, kind="run_error", signature="s")
    assert resolve_phase(state) is Phase.REPORT

    for tool in ("create_flow", "update_flow", "set_acceptance_contract", "run_flow"):
        blocked = _blocked_by(tool, state)
        assert blocked["guard_id"] == "attempt_budget_exhausted"
        assert blocked["required_action"] == "report_to_user_and_stop"
        # 拦截要能直接变成一句对用户说的话，否则用户只看到一个空气泡
        assert blocked["user_message"]
        assert "timeout" in blocked["user_message"]

    # 单节点精准修复是留给这一阶段的出路：它做不成盲改循环的载体，
    # 而它滥用的那一面由 lint_diff 的 selector 预算和字段回摆各自挡着。
    assert apply_phase_gate("apply_node_fix", {}, state) is None
    assert apply_phase_gate("get_run_error", {}, state) is None


def test_navigation_exhaustion_asks_the_user_for_a_target_url() -> None:
    """旧 navigation_budget_lock 最有价值的部分：给三条可执行的补充路径，而不是「我卡住了」。"""
    state = _ready()
    for _ in range(2):
        note_failed_attempt(state, kind="navigation", signature="n_menu:browser.click:selector_error")
    blocked = _blocked_by("run_flow", state)
    assert blocked["required_action"] == "needs_user_navigation_target"
    assert any("URL" in item for item in blocked["needed_from_user"])


def test_audit_exhaustion_asks_for_the_expected_shape() -> None:
    state = _ready()
    for _ in range(2):
        note_failed_attempt(state, kind="audit", signature="audit:mixed_ui_rows")
    assert _blocked_by("run_flow", state)["needed_from_user"] == [
        "确认交付物应有的字段与行数口径（哪些行算数、哪些该排除）",
        "或一份期望结果的样例（几行即可）",
    ]


def test_a_verified_run_clears_the_budget_and_the_pending_obligations() -> None:
    """跑通了就该重新给额度，否则同一会话里的下一个需求会背着上一个的熔断。"""
    state = _ready(
        blocking_diagnostics=[{"severity": "error"}],
        audit_findings={"issues": [], "repair_plan": []},
    )
    note_failed_attempt(state, kind="run_error", signature="s")
    note_failed_attempt(state, kind="run_error", signature="s")
    assert resolve_phase(state) is Phase.REPORT

    note_verified(state)
    assert resolve_phase(state) is Phase.VERIFY
    assert apply_phase_gate("run_flow", {}, state) is None


# ── 收敛：重复取证 ────────────────────────────────────────────────────────────


def test_the_same_evidence_call_twice_is_refused() -> None:
    state = _ready()
    args = {"url": "https://example.com/list"}
    assert apply_phase_gate("inspect_page", args, state) is None
    note_evidence(state, "inspect_page", args)

    blocked = _blocked_by("inspect_page", state, args)
    assert blocked["guard_id"] == "evidence_already_collected"
    assert blocked["required_action"] == "use_evidence_already_in_context"


def test_inspecting_a_different_target_is_always_allowed() -> None:
    """旧的「连续 3 次」会把三个不同 URL 的正当探测误挡。"""
    state = _ready()
    for index in range(5):
        args = {"url": f"https://example.com/page-{index}"}
        assert apply_phase_gate("inspect_page", args, state) is None
        note_evidence(state, "inspect_page", args)


def test_alternating_two_evidence_tools_does_not_dodge_the_check() -> None:
    """旧实现让截图和 DOM 探测共用一个计数，正是因为两个工具轮流调能绕过去。"""
    state = _ready()
    for tool in ("inspect_page", "inspect_screenshot"):
        note_evidence(state, tool, {})
    for tool in ("inspect_page", "inspect_screenshot"):
        assert _blocked_by(tool, state)["guard_id"] == "evidence_already_collected"


def test_a_landed_change_makes_the_same_evidence_call_legitimate_again() -> None:
    """改完再看同一个页面是正当动作；连着看两次同一个页面不是。"""
    state = _ready()
    args = {"url": "https://example.com/list"}
    note_evidence(state, "inspect_page", args)
    assert _blocked_by("inspect_page", state, args)

    note_progress(state)
    assert apply_phase_gate("inspect_page", args, state) is None


def test_run_flow_repeats_are_budget_not_repeated_evidence() -> None:
    """重复调 run_flow 是「又试了一次」，该花额度，不该被当成打转白拦。"""
    state = _ready()
    note_evidence(state, "run_flow", {"flow_id": "f1"})
    assert apply_phase_gate("run_flow", {"flow_id": "f1"}, state) is None


# ── 收敛：护栏拦截 ────────────────────────────────────────────────────────────


def _guard_block(guard_id: str) -> dict[str, Any]:
    return {
        "status": "blocked_by_orchestrator_guard",
        "guard_id": guard_id,
        "message": f"{guard_id} 的原始提示",
    }


def test_the_first_guard_block_is_free_and_passes_through_unchanged() -> None:
    """第一次拦截是在纠正一个具体错误，改对就能过；收它的费等于拒绝自我加固。"""
    state = _ready()
    blocked = _guard_block("acceptance_contract_sources_must_match_user")
    assert note_guard_block(state, "set_acceptance_contract", blocked) is blocked
    assert resolve_phase(state) is Phase.VERIFY


def test_the_third_identical_guard_block_hands_the_turn_back_to_the_user() -> None:
    """实测烧掉 294k token 的那条空转：同一条契约校验连拦 11 次，没有任何判据数得到。"""
    state = _ready()
    for _ in range(2):
        note_guard_block(state, "set_acceptance_contract",
                         _guard_block("acceptance_contract_sources_must_match_user"))
    final = note_guard_block(state, "set_acceptance_contract",
                             _guard_block("acceptance_contract_sources_must_match_user"))
    assert final["guard_id"] == "attempt_budget_exhausted"
    assert final["required_action"] == "report_to_user_and_stop"
    # 默认那句「我已经改了流程并重试了几次」在这里是假话：流程可能一次都没落盘
    assert "前置校验" in final["user_message"]
    assert any("口径" in item for item in final["needed_from_user"])
    assert resolve_phase(state) is Phase.REPORT


def test_switching_the_tool_does_not_buy_a_fresh_guard_budget() -> None:
    """签名只含 guard_id：同一条 check 换个工具调用还是同一面墙。"""
    state = _ready()
    for tool in ("create_flow", "update_flow", "set_acceptance_contract"):
        final = note_guard_block(state, tool, _guard_block("same_check"))
    assert final["guard_id"] == "attempt_budget_exhausted"


def test_different_guards_each_get_their_own_first_free_block() -> None:
    """三条不同的拦截各是一次可改正的具体错误，不该合起来判成打转。"""
    state = _ready()
    for guard_id in ("guard_a", "guard_b", "guard_c"):
        blocked = _guard_block(guard_id)
        assert note_guard_block(state, "update_flow", blocked) is blocked
    assert resolve_phase(state) is Phase.VERIFY


# ── 暴露的工具集 ──────────────────────────────────────────────────────────────


def test_schema_exposure_matches_admission_exactly() -> None:
    """暴露了却会被拦，等于故意让模型白花一轮。"""
    states = [
        _ready(),
        _ready(flow_has_nodes=False, page_evidence_required={"url": "u"}, page_evidence_done=False),
        _ready(flow_has_nodes=False),
        _ready(blocking_diagnostics=[{"severity": "error"}]),
        _ready(attempt_budget={"spent": VERIFY_ATTEMPT_BUDGET, "signatures": {}, "attempts": []}),
    ]
    for state in states:
        exposed = admitted_tool_names(_ALL_TOOLS, state)
        for tool in sorted(exposed):
            # run_flow 的授权是用户意图，不是阶段——它可以被暴露却仍需用户点头
            if tool == "run_flow" and not state.get("run_authorized"):
                continue
            assert apply_phase_gate(tool, {}, state) is None, (resolve_phase(state), tool)


def test_a_blank_flow_awaiting_evidence_only_sees_inspect_page() -> None:
    """流程还不存在时，除了去看页面没有任何别的动作能推进——连 run 都没有可读的。"""
    state = _ready(flow_has_nodes=False, page_evidence_required={"url": "u"}, page_evidence_done=False)
    assert admitted_tool_names(_ALL_TOOLS, state) == frozenset({"inspect_page"})


def test_discovery_on_an_existing_flow_keeps_every_read_tool() -> None:
    state = _ready(page_evidence_required={"url": "u"}, page_evidence_done=False)
    exposed = admitted_tool_names(_ALL_TOOLS, state)
    assert EVIDENCE_TOOLS <= exposed
    assert not (PHASE_GATED_TOOLS & exposed)


# ── 自省 ──────────────────────────────────────────────────────────────────────


def test_phase_contract_lines_are_renderable_bullets() -> None:
    """契约行直接注入 system prompt，空 bullet 会变成一条没有内容的规则。"""
    lines = phase_contract_lines()
    assert lines
    assert all(line.startswith("- ") and len(line) > 10 for line in lines)
    # 预算刻意不进提示词：提前告诉模型「三次会被锁」等于把上限当额度用
    joined = "\n".join(lines)
    assert str(VERIFY_ATTEMPT_BUDGET) not in joined


def test_report_is_the_coarsest_phase_and_wins_over_every_gate() -> None:
    """顺序即优先级：在已经打不动的局面下报一个前置门，只会把模型引向另一个被挡的动作。"""
    state = _ready(
        flow_has_nodes=False,
        page_evidence_required={"url": "u"},
        page_evidence_done=False,
        blocking_diagnostics=[{"severity": "error"}],
    )
    note_failed_attempt(state, kind="run_error", signature="s")
    note_failed_attempt(state, kind="run_error", signature="s")
    assert resolve_phase(state) is Phase.REPORT
    assert _blocked_by("update_flow", state)["guard_id"] == "attempt_budget_exhausted"


def test_every_phase_is_described_and_ordered_by_precedence() -> None:
    rows = describe_phases()
    assert [row["phase"] for row in rows] == [phase.value for phase in Phase]
    assert [row["precedence"] for row in rows] == list(range(len(rows)))
    assert rows[0]["phase"] == Phase.REPORT.value


def test_declared_guard_ids_match_the_refusal_sites() -> None:
    """PHASE_GUARD_IDS 是评测断言的白名单，漏一个就让那条断言永远静默通过。

    自己声明自己的清单会跟 `_blocked` 调用点各自演化，所以这里从源码里把字面量抠出来比。
    """
    import re
    from pathlib import Path

    from app.services import ai_phases

    source = Path(ai_phases.__file__).read_text(encoding="utf-8")
    # _blocked(tool_name, "<guard_id>", ...) —— 第二个实参恒为字面量
    used = set(re.findall(r'_blocked\(\s*\w+,\s*"([a-z_]+)"', source))
    assert used == set(PHASE_GUARD_IDS)
