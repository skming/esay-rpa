"""每条编排护栏的触发路径。

护栏是「模型明知规则仍会犯」时唯一还起作用的东西，但它平时不响：正常会话里
一条都不触发，重构把某条改瞎了不会有任何症状，直到线上出现一次本该被拦下的
昂贵空转。所以这里按 guard id 逐条断言触发，末尾再反向检查有没有新增了却
没人测的 guard——护栏清单能被枚举，本来就是为了这件事。

模型行为侧的验证（模型撞上拦截后是否真的改道）在 evals/run_evals.py。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.ai_guard_state import GuardState
from app.services.ai_guards import (
    GUARDS,
    apply_pre_tool_guards,
    call_fingerprint,
    describe_guards,
    guard_contract_lines,
)


def _blocked_by(tool: str, args: dict[str, Any], state: GuardState) -> dict[str, Any]:
    result = apply_pre_tool_guards(tool, args, state)
    assert result is not None, f"{tool} 本应被拦截"
    assert result["status"] == "blocked_by_orchestrator_guard"
    assert result["blocked_tool"] == tool
    return result


def test_read_only_mode_blocks_every_write_but_no_diagnosis() -> None:
    state = GuardState(read_only_tools=True)
    for tool in ("create_flow", "update_flow", "apply_node_fix", "run_flow", "publish_flow"):
        assert _blocked_by(tool, {}, state)["guard_id"] == "read_only_mode"
    # 诊断手段一个都不能收——只读模式的产物就是一份根因分析
    for tool in ("get_run_error", "inspect_page", "get_run_logs", "get_run_output"):
        assert apply_pre_tool_guards(tool, {}, state) is None


def test_screenshot_is_blocked_only_for_models_without_vision() -> None:
    blocked = _blocked_by("inspect_screenshot", {}, GuardState(model_no_vision=True))
    assert blocked["guard_id"] == "model_no_vision"
    assert blocked["required_tool"] == "inspect_page"
    assert apply_pre_tool_guards("inspect_screenshot", {}, GuardState(model_no_vision=False)) is None


def test_static_page_evidence_only_allows_fetch_flow() -> None:
    state = GuardState(page_evidence_source="scrapling_static")
    blocked = _blocked_by("create_flow", {
        "nodes": [
            {"id": "open", "type": "browser.open"},
            {"id": "extract", "type": "browser.extract"},
        ],
    }, state)
    assert blocked["guard_id"] == "static_page_evidence_requires_fetch_flow"
    assert blocked["required_action"] == "build_static_fetch_flow"

    # create_flow 必须自带 browser.fetch 主链路
    blocked = _blocked_by("create_flow", {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "write", "type": "file.write", "path": "output.json"},
            {"id": "end", "type": "end"},
        ],
    }, state)
    assert blocked["guard_id"] == "static_page_evidence_requires_fetch_flow"

    # update_flow 只改非浏览器节点不拦
    assert apply_pre_tool_guards("update_flow", {
        "update_nodes": [{"id": "write", "patch": {"path": "result.json"}}],
    }, state) is None

    assert apply_pre_tool_guards("create_flow", {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "fetch", "type": "browser.fetch"},
            {"id": "end", "type": "end"},
        ],
    }, state) is None
    assert apply_pre_tool_guards("update_flow", {
        "add_nodes": [{"id": "fetch", "type": "browser.fetch"}],
    }, state) is None
    assert apply_pre_tool_guards("create_flow", {
        "nodes": [{"id": "fetch", "type": "browser.fetch"}],
    }, GuardState(page_evidence_source="browser_dom")) is None


def test_static_page_evidence_enforces_static_fetcher() -> None:
    state = GuardState(page_evidence_source="scrapling_static")
    blocked = _blocked_by("create_flow", {
        "nodes": [
            {"id": "fetch", "type": "browser.fetch", "fetcher": "dynamic"},
        ],
    }, state)
    assert blocked["guard_id"] == "static_page_evidence_requires_fetch_flow"
    assert blocked["required_action"] == "set_fetcher_to_static"
    assert blocked["found_fetcher"] == "dynamic"

    blocked = _blocked_by("update_flow", {
        "update_nodes": [{"id": "fetch", "patch": {"fetcher": "stealthy"}}],
    }, state)
    assert blocked["required_action"] == "set_fetcher_to_static"
    assert blocked["found_fetcher"] == "stealthy"

    # fetcher="static" 或缺省都通过
    assert apply_pre_tool_guards("create_flow", {
        "nodes": [{"id": "fetch", "type": "browser.fetch", "fetcher": "static"}],
    }, state) is None
    assert apply_pre_tool_guards("create_flow", {
        "nodes": [{"id": "fetch", "type": "browser.fetch"}],
    }, state) is None


def test_challenge_page_lock_stops_edits_and_reruns_against_the_same_wall() -> None:
    state = GuardState(challenge_page_lock={"url": "https://site.example/post-1", "label": "人机验证拦截页"})
    for tool in ("create_flow", "update_flow", "apply_node_fix", "run_flow"):
        assert _blocked_by(tool, {}, state)["guard_id"] == "challenge_page_lock"
    # 重探同一个 URL 就是撞同一堵墙，第一轮丢节点正是这么循环出来的
    blocked = _blocked_by("inspect_page", {"url": "https://site.example/post-1"}, state)
    assert blocked["required_action"] == "needs_human_verification"
    # 换个地址去看仍是正当动作，挡掉等于没收了模型唯一还能用的眼睛
    assert apply_pre_tool_guards("inspect_page", {"url": "https://other.example/"}, state) is None


def test_failure_budget_lock_keeps_every_read_only_tool_available() -> None:
    """挡掉纯读工具等于没收诊断手段，模型只能在剩下几个工具间空转。"""
    state = GuardState(failure_budget_lock={"flow_id": "f1"})
    for tool in ("list_node_types", "get_run_output", "get_run_logs",
                 "get_run_error", "inspect_page", "inspect_screenshot", "apply_node_fix"):
        assert apply_pre_tool_guards(tool, {}, state) is None
    for tool in ("update_flow", "run_flow", "create_flow"):
        assert _blocked_by(tool, {}, state)["guard_id"] == "failure_budget_lock"


def test_call_fingerprint_ignores_key_order_but_not_values() -> None:
    assert call_fingerprint("run_flow", {"a": 1, "b": 2}) == call_fingerprint("run_flow", {"b": 2, "a": 1})
    assert call_fingerprint("run_flow", {"a": 1}) != call_fingerprint("run_flow", {"a": 2})
    assert call_fingerprint("run_flow", {}) != call_fingerprint("inspect_page", {})
    # 不可序列化的参数不能让整条链路抛异常
    assert call_fingerprint("run_flow", {"o": object()})


def test_guards_never_rewrite_the_models_arguments() -> None:
    """护栏只放行或拦截，绝不改写参数。

    审计过去是模型可调的工具，判据参数由模型自己填（需求原文、内容已核对确认位），
    平台只能在调用前把这些参数改写掉。改写是最坏的一种补救：模型看到的是自己填的值，
    实际生效的是另一份，它照着返回的失败去修，永远修不到点上。
    现在判据全部由平台从冻结契约算出，模型手上根本没有这些参数——
    这条测试守的是「不要再把改写请回来」。
    """
    samples = {
        "run_flow": {"flow_id": "f1", "variables": {"page": 1}},
        "update_flow": {"flow_id": "f1", "add_nodes": [{"id": "n1"}]},
        "apply_node_fix": {"flow_id": "f1", "node_id": "n1", "config_patch": {"selector": "tbody tr"}},
        "get_run_output": {"task_id": "t1"},
        "set_acceptance_contract": {"flow_id": "f1", "requirement_change_quote": "改成 Excel"},
    }
    state = GuardState(latest_user_message="改成 Excel", user_requirement_text="抓 2024 年的订单")
    for tool, args in samples.items():
        before = json.loads(json.dumps(args))
        apply_pre_tool_guards(tool, args, state)
        assert args == before, tool


def test_acceptance_contract_change_requires_an_exact_user_quote() -> None:
    """普通修复不能靠模型自己复述一句需求来降低验收标准。"""
    state = GuardState(latest_user_message="请把交付格式改成 Excel，并保留原始日期字段")

    blocked = _blocked_by(
        "set_acceptance_contract",
        {"requirement_change_quote": "用户希望修改交付格式"},
        state,
    )
    assert blocked["guard_id"] == "acceptance_contract_change_requires_user_quote"
    assert blocked["required_action"] == "preserve_acceptance_contract"

    assert apply_pre_tool_guards(
        "set_acceptance_contract",
        {"requirement_change_quote": "把交付格式改成 Excel"},
        state,
    ) is None


def test_acceptance_contract_sources_must_match_user_requirement_text() -> None:
    state = GuardState(user_requirement_text="抓取全部订单并导出 Excel")
    invalid = {
        "acceptance_contract": {
            "requirements": [{
                "id": "export",
                "description": "导出 CSV",
                "source_kind": "user",
                "source_quote": "导出 CSV",
                "confidence": 1,
                "confirmed": True,
            }],
            "deliverables": [],
        },
    }
    blocked = _blocked_by("create_flow", invalid, state)
    assert blocked["guard_id"] == "acceptance_contract_sources_must_match_user"

    invalid["acceptance_contract"]["requirements"][0].update({
        "description": "导出 Excel",
        "source_quote": "导出 Excel",
    })
    assert apply_pre_tool_guards("create_flow", invalid, state) is None


def test_credential_values_must_stay_out_of_ai_tools() -> None:
    blocked = _blocked_by("create_flow", {
        "input_variables": [
            {"name": "username", "category": "credential", "value": "alice"},
            {"name": "password", "sensitive": True, "value": "secret"},
        ],
    }, GuardState())
    assert blocked["guard_id"] == "credential_values_must_stay_out_of_ai_tools"
    assert blocked["exposed_variables"] == ["username", "password"]

    assert apply_pre_tool_guards("create_flow", {
        "input_variables": [
            {"name": "username", "category": "credential", "value": ""},
            {"name": "date_start", "category": "flow", "value": "2026-01-01"},
        ],
    }, GuardState()) is None


def test_credential_check_covers_the_default_value_alias() -> None:
    """执行器把 defaultValue 当 value 的输入别名收下，只看 value 就留了一条同样能落盘的路。"""
    blocked = _blocked_by("create_flow", {
        "input_variables": [{"name": "api_token", "defaultValue": "t-123"}],
    }, GuardState())
    assert blocked["exposed_variables"] == ["api_token"]


def test_read_only_mode_wins_over_every_business_guard() -> None:
    """只读模式是调用方的授权边界，任何业务闸都不该排在它前面。

    「先做 X 再做 Y」那半边的优先级已经不在这张表里判了，见 test_ai_phases.py。
    """
    state = GuardState(
        read_only_tools=True,
        page_evidence_source="scrapling_static",
        failure_budget_lock={"flow_id": "f1"},
    )
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
