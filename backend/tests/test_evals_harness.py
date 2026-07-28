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
        expect_guards_not_triggered=["pre_create_inspect_gate"],
    )
    assert _judge_scenario(scenario, "", _executor("create_flow"), []) == []
    failures = _judge_scenario(scenario, "", _executor("create_flow"), ["pre_create_inspect_gate"])
    assert failures and "pre_create_inspect_gate" in failures[0]


def test_expect_guard_triggered_catches_an_unreachable_guard() -> None:
    scenario = Scenario(
        name="t", description="", user_message="",
        expect_guards_triggered=["repair_cycle_lock"],
    )
    assert _judge_scenario(scenario, "", _executor(), ["repair_cycle_lock"]) == []
    assert _judge_scenario(scenario, "", _executor(), [])


def test_observe_guards_records_guard_id_and_restores_the_original() -> None:
    from app.services import ai_orchestrator

    original = ai_orchestrator.apply_pre_tool_guards
    state = {"read_only_tools": True}
    with _observe_guards() as hits:
        blocked = ai_orchestrator.apply_pre_tool_guards("run_flow", {}, state)
        assert blocked is not None
        assert hits == ["read_only_mode"]
    assert ai_orchestrator.apply_pre_tool_guards is original


def test_guard_scenarios_reference_real_guard_ids() -> None:
    """场景里写错 guard_id 会永远静默通过。"""
    from app.services.ai_guards import GUARDS

    known = {guard.id for guard in GUARDS}
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
