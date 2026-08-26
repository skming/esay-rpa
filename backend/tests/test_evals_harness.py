"""评测脚手架自身的测试。

评测集平时只在有 API Key 时跑，判分器写错了不会有人发现——错的判分比没有判分更糟，
因为它会给出「行为没退化」的假绿灯。这里用假数据把判分器和护栏观察器跑通。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evals.run_evals import (  # noqa: E402
    SCENARIOS,
    MockToolExecutor,
    Scenario,
    _judge_scenario,
    _observe_guards,
)


def _executor(*calls: str) -> MockToolExecutor:
    executor = MockToolExecutor()
    executor.calls = [(name, {}) for name in calls]
    return executor


def test_guard_assertions_separate_obedience_from_interception() -> None:
    """模型违规但被护栏拦住，不能算通过——它说明提示词没起作用。"""
    scenario = Scenario(
        name="t", description="", user_message="",
        expect_guards_not_triggered=["page_evidence_required"],
    )
    assert _judge_scenario(scenario, "", _executor("create_flow"), []) == []
    failures = _judge_scenario(scenario, "", _executor("create_flow"), ["page_evidence_required"])
    assert failures and "page_evidence_required" in failures[0]


def test_expect_guard_triggered_catches_an_unreachable_guard() -> None:
    scenario = Scenario(
        name="t", description="", user_message="",
        expect_guards_triggered=["attempt_budget_exhausted"],
    )
    assert _judge_scenario(scenario, "", _executor(), ["attempt_budget_exhausted"]) == []
    assert _judge_scenario(scenario, "", _executor(), [])


def test_observe_guards_records_guard_id_and_restores_the_original() -> None:
    from app.services import ai_orchestrator
    from app.services.ai_guard_state import GuardState

    original = ai_orchestrator._orchestrator_guard_before_tool
    state = GuardState(read_only_tools=True)
    with _observe_guards() as hits:
        blocked = ai_orchestrator._orchestrator_guard_before_tool("run_flow", {}, state)
        assert blocked is not None
        assert hits == ["read_only_mode"]
    assert ai_orchestrator._orchestrator_guard_before_tool is original


def test_observe_guards_also_records_phase_refusals() -> None:
    """拦截分两层，只钩住 GUARDS 那层会让阶段类断言永远静默通过——一盏假绿灯。"""
    from app.services import ai_orchestrator
    from app.services.ai_guard_state import GuardState

    state = GuardState(
        flow_has_nodes=False,
        page_evidence_required=None,
        page_evidence_done=True,
        run_authorized=True,
    )
    with _observe_guards() as hits:
        assert ai_orchestrator._orchestrator_guard_before_tool("run_flow", {}, state) is not None
    assert hits == ["flow_must_exist_before_run"]


def test_fabricated_write_claim_fails_even_with_no_scenario_assertion() -> None:
    """零调用却宣称落盘：现有判据一条都拦不住，所以这条挂在全套上。"""
    from app.services.ai_orchestrator import _FLOW_SAVED_CLAIM_PHRASES

    scenario = Scenario(name="t", description="", user_message="", expect_tool_order=[("a", "b")])
    claim = f"好的，{_FLOW_SAVED_CLAIM_PHRASES[0]}，你可以去运行了。"
    assert _judge_scenario(scenario, claim, _executor(), [])
    # 真的写了就不算编造，哪怕后面那句话一模一样
    assert _judge_scenario(scenario, claim, _executor("create_flow"), []) == []


def test_guard_scenarios_reference_real_guard_ids() -> None:
    """场景里写错 guard_id 会永远静默通过。"""
    from app.services.ai_guards import GUARDS
    from app.services.ai_phases import PHASE_GUARD_IDS

    known = {guard.id for guard in GUARDS} | set(PHASE_GUARD_IDS)
    for scenario in SCENARIOS:
        for guard_id in [*scenario.expect_guards_triggered, *scenario.expect_guards_not_triggered]:
            assert guard_id in known, f"{scenario.name} 引用了不存在的 guard {guard_id}"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_every_scenario_asserts_something(scenario: Scenario) -> None:
    checks = (
        scenario.expect_no_tools
        or scenario.expect_first_tool
        or scenario.expect_tools_called
        or scenario.expect_tools_not_called
        or scenario.expect_tool_order
        or scenario.expect_tool_max_calls
        or scenario.expect_reply_contains_any
        or scenario.expect_guards_triggered
        or scenario.expect_guards_not_triggered
        or scenario.expect_flow_created
        or scenario.expect_flow_lint_error_free
        or scenario.expect_flow_node_types_include
        or scenario.expect_flow_node_types_exclude
    )
    assert checks, f"{scenario.name} 没有任何断言，跑它只是在烧 token"


def _scenarios_expecting_run() -> list[Scenario]:
    return [
        s for s in SCENARIOS
        if s.flow_id and "run_flow" in (*s.expect_tools_called, *[t for pair in s.expect_tool_order for t in pair])
    ]


@pytest.mark.parametrize("scenario", _scenarios_expecting_run(), ids=lambda s: s.name)
async def test_scenarios_expecting_run_flow_can_reach_verify(scenario: Scenario) -> None:
    """断言 run_flow 的场景，用它自己的 fixture 必须真能推到 VERIFY。

    不到 VERIFY 时 run_flow 在结构上就拿不到，断言它会不会被调用等于断言一件不可能的事——
    而失败信息指向模型，人会去改提示词。实际踩过：fixture 流程没带 acceptance_contract，
    状态块判出 error 级 acceptance_contract_incomplete，四个场景整局钉在 FIX 阶段。

    「先修再跑」的场景开局就在 FIX 是对的，所以补一次落盘后重判：fixture 要么现在放行，
    要么修完放行；两次都到不了才是死局。这一条同时守住了 fixture 的动态性——
    写死返回值的 fixture 在第二次判定里仍然停在 FIX。

    这里只算 fixture 决定的那部分事实（有没有节点、有没有阻断诊断）。
    意图决定的 run_authorized / page_evidence_required 由场景自己的
    expect_guards_not_triggered 在评测时判——它们随用户措辞变，抄一份到这里会长出第二份真相。
    """
    from app.services.ai_phases import Phase

    as_is = await _fixture_phase(scenario, ())
    after_fix = await _fixture_phase(scenario, ("apply_node_fix",))
    assert Phase.VERIFY in (as_is[0], after_fix[0]), (
        f"{scenario.name} 断言了 run_flow，但它的 fixture 无论修没修都推不到 VERIFY："
        f"开局 {as_is[0].value}（阻断 {as_is[1]}）、落盘一次修复后 {after_fix[0].value}"
        f"（阻断 {after_fix[1]}）"
    )


async def _fixture_phase(
    scenario: Scenario, prior_calls: tuple[str, ...]
) -> tuple[object, list[str | None]]:
    from app.services.ai_flow_state import build_flow_state
    from app.services.ai_orchestrator import _blocking_diagnostics
    from app.services.ai_guard_state import GuardState
    from app.services.ai_phases import resolve_phase

    executor = MockToolExecutor(scenario.tool_overrides)
    executor.calls = [(name, {}) for name in prior_calls]
    flow_state = await build_flow_state(executor, scenario.flow_id)
    state = GuardState(
        flow_has_nodes=not flow_state.is_blank,
        page_evidence_required=None,
        page_evidence_done=True,
        run_authorized=True,
    )
    state.blocking_diagnostics = _blocking_diagnostics(flow_state, state)
    issues = [f.get("issue") for f in state.blocking_diagnostics or []]
    return resolve_phase(state), issues
