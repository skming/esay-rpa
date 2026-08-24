from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap
from types import SimpleNamespace
from typing import Any

from app.services.ai_flow_state import (
    FlowState,
    build_flow_state,
    is_local_draft_flow_id,
    render_flow_state,
    sync_state_message,
)
from app.services.ai_phases import VERIFY_ATTEMPT_BUDGET, initial_facts
from app.services.ai_orchestrator import (
    AiOrchestrator,
    _AFTER_TOOL_HANDLERS,
    _AFTER_TOOL_NO_STATE_EFFECT,
    _after_tool_guidance,
    _compact_tool_messages,
    _build_few_shot_block,
    _build_system_message,
    _context_char_budget,
    _model_caps,
    _detect_turn_intents,
    _elide_repeated_result,
    _FLOW_WRITE_TOOLS,
    _ELIDE_MIN_CHARS,
    _expand_history_tool_calls,
    _mark_history_cache_anchor,
    _misapplied_refusal,
    _stable_prefix_end,
    _OLD_SCREENSHOT_PLACEHOLDER,
    _orchestrator_guard_after_tool,
    _orchestrator_guard_before_tool,
    _resolve_resumable_task_state,
    _RUN_NOT_STARTED_STATUSES,
    _RUN_REFUSED_MODEL_FIXABLE,
    _RUN_WAITING_STATUSES,
    _split_partial_tag_suffix,
    _system_prompt_for_round,
    _task_state_message,
    _terminal_tool_response,
    _ThinkTagFilter,
    _tool_schemas_for_round,
    _TurnIntents,
    _unmet_verification_request,
)
from app.services.ai_tools.executor import RpaToolExecutor
from app.services.ai_tools.schemas import TOOL_SCHEMAS


def _ready(**overrides: Any) -> dict[str, Any]:
    """一个可以直接跑流程的会话：流程已存在、证据到手、诊断干净、用户授权。

    阶段机的事实全部 fail-closed（缺键即最保守），空 dict 会被判成 BUILD 且未授权运行。
    编排层用例要验的是「钩子有没有把事实改对」，起点必须是一个明确的可运行局面，
    否则断言到的是缺省值而不是这次改动。
    """
    state = initial_facts(
        flow_has_nodes=True,
        page_evidence_required=None,
        page_evidence_done=True,
        run_authorized=True,
    )
    state.update(overrides)
    return state


def test_split_partial_tag_suffix_holds_back_split_tag() -> None:
    # 标签被 chunk 边界劈开：结尾的真前缀要扣下
    assert _split_partial_tag_suffix("你好<thi", "<think>") == ("你好", "<thi")
    assert _split_partial_tag_suffix("推理中</thin", "</think>") == ("推理中", "</thin")
    # 完整标签不算前缀（由 find 分支处理），普通文本原样放行
    assert _split_partial_tag_suffix("没有标签", "<think>") == ("没有标签", "")
    assert _split_partial_tag_suffix("<", "<think>") == ("", "<")


def _feed_all(chunks: list[str]) -> list[tuple[str, str]]:
    f = _ThinkTagFilter()
    events: list[tuple[str, str]] = []
    for c in chunks:
        events.extend(f.feed(c))
    events.extend(f.flush())
    return events


def test_think_filter_splits_thinking_from_visible_text() -> None:
    assert _feed_all(["<think>推理</think>结论"]) == [("thinking", "推理"), ("text", "结论")]
    # 标签被 chunk 边界劈开，思维链不得泄漏进可见文本
    assert _feed_all(["前言<thi", "nk>推理</thin", "k>结论"]) == [
        ("text", "前言"), ("thinking", "推理"), ("text", "结论"),
    ]
    # 没有标签时原样透传
    assert _feed_all(["纯", "文本"]) == [("text", "纯"), ("text", "文本")]


def test_think_filter_flushes_unclosed_tag_prefix_as_plain_text() -> None:
    # 流结束时扣下的 "<thi" 不是完整标签，按当前状态补发而不是吞掉
    assert _feed_all(["结论<thi"]) == [("text", "结论"), ("text", "<thi")]
    assert _feed_all(["<think>推理未闭合"]) == [("thinking", "推理未闭合")]


def test_blocked_write_gets_no_success_guidance_and_does_not_end_the_round() -> None:
    blocked = {"status": "blocked_by_orchestrator_guard", "required_tool": "inspect_page"}
    assert _after_tool_guidance("update_flow", blocked) == (None, False)
    assert _after_tool_guidance("create_flow", {**blocked, "flow_id": "f1"}) == (None, False)
    # 真正写入成功才结束本轮剩余并行调用
    guidance, stop = _after_tool_guidance("create_flow", {"status": "created", "flow_id": "f1"})
    assert guidance and stop
    # apply_node_fix 是增量修复，注入引导但不打断本轮
    guidance, stop = _after_tool_guidance("apply_node_fix", {"status": "ok"})
    assert guidance and not stop


def test_write_directive_points_at_the_state_block_instead_of_relisting_findings() -> None:
    """诊断只有一份，在状态块里。写入返回里再列一遍就是第二个副本。

    两份副本的时点不同（写入返回是写入那一刻的，状态块是下一轮读到的），一旦不一致，
    模型无从判断该信哪个。
    """
    clean, _ = _after_tool_guidance("update_flow", {"status": "updated", "revision": 4}, _ready())
    assert "revision 4" in clean
    assert "run_flow" in clean
    assert "状态块" in clean

    # 连通性是写入前后对比才看得出来的，状态块读单份定义看不到，这条必须留在写入返回里
    broken, _ = _after_tool_guidance("update_flow", {
        "status": "updated",
        "revision": 5,
        "connectivity_warning": "n7 成了孤儿节点",
    }, _ready())
    assert "n7 成了孤儿节点" in broken
    assert "run_flow" not in broken


def test_write_directive_respects_this_turn_run_authorization() -> None:
    """用户只说修、没说跑时，写完要交回用户，不能顺手把 run_flow 写进下一步。"""
    locked, _ = _after_tool_guidance(
        "update_flow", {"status": "updated", "revision": 7}, _ready(run_authorized=False)
    )
    assert "run_flow" in locked and "不要调用 run_flow" in locked
    assert "问是否要运行验证" in locked


def test_terminal_guard_block_forces_a_closing_statement() -> None:
    """要用户拿主意的拦截必须逼出收尾正文，否则用户只看到一个空气泡。"""
    for action in ("report_to_user_and_stop", "needs_user_navigation_target"):
        guidance, stop = _after_tool_guidance("run_flow", {
            "status": "blocked_by_orchestrator_guard",
            "required_action": action,
        })
        assert guidance and "不要再调用任何工具" in guidance
        assert stop

    # ask_user 也收尾，但措辞不能说"卡住了"——改动已经落盘，只是要不要跑归用户定
    guidance, stop = _after_tool_guidance("run_flow", {
        "status": "blocked_by_orchestrator_guard",
        "required_action": "ask_user",
    })
    assert guidance and stop
    assert "不要再调用任何工具" in guidance
    assert "卡在哪一步" not in guidance
    assert "要不要现在运行" in guidance

    # 只是改道的拦截仍按原样放行，强行收尾会打断本该继续的诊断
    assert _after_tool_guidance("update_flow", {
        "status": "blocked_by_orchestrator_guard",
        "required_action": "call_inspect_page_first",
    }) == (None, False)
    # 修复还没落盘时被 autorun lock 拦住同样不能收尾：那时候一步都还没做
    assert _after_tool_guidance("run_flow", {
        "status": "blocked_by_orchestrator_guard",
        "required_action": "explain_and_wait",
    }) == (None, False)

    guidance, stop = _after_tool_guidance("inspect_page", {
        "status": "blocked_page_access",
        "http_status": 403,
        "required_action": "report_to_user_and_stop",
    })
    assert guidance and stop


def test_ask_user_block_closes_the_round_without_a_canned_reply() -> None:
    """修复落盘后被 autorun lock 拦下：收走工具，但收尾话必须由模型自己写。

    换成 terminal_response_only 会走 _terminal_tool_response 的模板，
    「改了哪个节点的哪个字段」那段就被顶掉了——那恰好是用户唯一要看的东西。
    """
    state: dict[str, Any] = {}
    blocked = {
        "status": "blocked_by_orchestrator_guard",
        "guard_id": "run_not_authorized",
        "required_action": "ask_user",
    }
    _orchestrator_guard_after_tool("run_flow", blocked, state)
    assert state["closing_statement_only"] is True
    assert not state.get("terminal_response_only")
    assert _tool_schemas_for_round(state, _TurnIntents()) == []

    # 其余阻断结果照旧不影响 state
    other: dict[str, Any] = {}
    _orchestrator_guard_after_tool("update_flow", {
        "status": "blocked_by_orchestrator_guard",
        "required_action": "call_inspect_page_first",
    }, other)
    assert not other.get("closing_statement_only")


def test_tool_schemas_follow_the_current_phase() -> None:
    no_url = _TurnIntents(create_requested=True)
    assert _tool_schemas_for_round({}, no_url) == []

    inspecting = _TurnIntents(create_requested=True, create_url="https://example.com")
    discovering = _ready(
        flow_has_nodes=False,
        page_evidence_required={"reason": "build_from_url"},
        page_evidence_done=False,
    )
    schemas = _tool_schemas_for_round(discovering, inspecting)
    assert [schema["function"]["name"] for schema in schemas] == ["inspect_page"]

    assert _tool_schemas_for_round({"terminal_response_only": True}, inspecting) == []
    # 改动已落盘、只剩「要不要运行」这个决定时同样收工具，逼出收尾正文
    assert _tool_schemas_for_round({"closing_statement_only": True}, inspecting) == []
    all_schemas = _tool_schemas_for_round({**discovering, "page_evidence_done": True}, inspecting)
    assert len(all_schemas) > 1


def test_state_reading_tools_are_not_exposed_at_all() -> None:
    """读当前状态的工具一把都不给：状态块每轮开头已经把答案放在上下文里了。

    留着它们等于让模型花一整轮去问平台已经答完的问题——实测这类复检占了全部
    工具调用的 18%。删的是那一轮，不是那份能力：executor 里这些方法仍然在，
    唯一的调用方从模型换成了 ai_flow_state。
    """
    names = {s["function"]["name"] for s in _tool_schemas_for_round({}, _TurnIntents())}
    assert not names & {"get_flow", "lint_flow", "validate_flow", "get_run_status"}
    # 失败现场（截图 / 导航轨迹 / 节点配置）状态块给不出，这把必须留着
    assert "get_run_error" in names


def test_capability_gaps_are_hidden_not_just_blocked() -> None:
    """能力缺失不是模型该去发现的事实：暴露一个必被拦的工具就是白烧一轮。

    只读模式和无视觉模型整轮固定，与阶段无关——所以断言的是「schema 里不出现」，
    而不是「调用时被拦」。拦截仍在（授权边界不能只靠没暴露来守），由 test_ai_guards 证。
    """
    from app.services.ai_guards import WRITE_TOOLS

    read_only = {s["function"]["name"] for s in _tool_schemas_for_round(
        _ready(read_only_tools=True), _TurnIntents(),
    )}
    assert not read_only & WRITE_TOOLS
    # 诊断手段一个都不能收：只读模式的产物就是一份根因分析
    assert {"get_run_error", "inspect_page", "get_run_logs"} <= read_only

    no_vision = {s["function"]["name"] for s in _tool_schemas_for_round(
        _ready(model_no_vision=True), _TurnIntents(),
    )}
    assert "inspect_screenshot" not in no_vision
    assert "inspect_page" in no_vision

    both = {s["function"]["name"] for s in _tool_schemas_for_round(
        _ready(read_only_tools=True, model_no_vision=True), _TurnIntents(),
    )}
    assert both and "inspect_screenshot" not in both and not both & WRITE_TOOLS


def test_page_discovery_uses_a_compact_phase_prompt() -> None:
    from app.services.ai_prompts import PAGE_DISCOVERY_PROMPT, SYSTEM_PROMPT

    state = _ready(
        flow_has_nodes=False,
        page_evidence_required={"reason": "build_from_url"},
        page_evidence_done=False,
    )
    assert _system_prompt_for_round(state) == PAGE_DISCOVERY_PROMPT
    assert len(PAGE_DISCOVERY_PROMPT) < len(SYSTEM_PROMPT) // 5
    state["page_evidence_done"] = True
    assert _system_prompt_for_round(state) == SYSTEM_PROMPT
    # 修复路径上的取证阶段仍要完整规则：看完 DOM 紧接着就要改节点
    assert _system_prompt_for_round(_ready(
        page_evidence_required={"reason": "repair_touches_page_elements"},
        page_evidence_done=False,
    )) == SYSTEM_PROMPT


def test_terminal_tool_response_prefers_structured_user_guidance() -> None:
    text = _terminal_tool_response("inspect_page", {
        "status": "blocked_page_access",
        "error": "目标页面返回 HTTP 403。",
        "required_action": "report_to_user_and_stop",
        "user_message": "请完成登录后再继续。",
    })
    assert text == "**当前无法继续。** 目标页面返回 HTTP 403。\n\n请完成登录后再继续。"


async def test_stream_sends_only_phase_relevant_tool_schemas(monkeypatch) -> None:
    import litellm

    captured: list[dict[str, Any]] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return _FakeStream([_chunk(content="请提供目标网址。", finish="stop")])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    orchestrator = AiOrchestrator(tool_executor=_FakeExecutor())  # type: ignore[arg-type]

    async for _ in orchestrator.stream(
        messages=[{"role": "user", "content": "帮我创建一个网页抓取流程"}],
        model="test-model",
    ):
        pass
    assert "tools" not in captured[-1]
    assert "tool_choice" not in captured[-1]

    async for _ in orchestrator.stream(
        messages=[{"role": "user", "content": "创建流程，抓取 https://example.com 的正文"}],
        model="test-model",
    ):
        pass
    assert [item["function"]["name"] for item in captured[-1]["tools"]] == ["inspect_page"]

    continuation = [
        {"role": "user", "content": "https://example.com/post/1，帖子主题及回帖"},
        {
            "role": "assistant",
            "content": "页面返回 403。",
            "toolCalls": [{
                "tool": "inspect_page",
                "args": '{"url":"https://example.com/post/1"}',
                "result": {
                    "status": "blocked_page_access",
                    "requested_url": "https://example.com/post/1",
                },
            }],
        },
        {"role": "user", "content": "继续创建"},
    ]
    async for _ in orchestrator.stream(messages=continuation, model="test-model"):
        pass
    assert [item["function"]["name"] for item in captured[-1]["tools"]] == ["inspect_page"]
    assert any("当前可恢复任务状态" in str(message.get("content")) for message in captured[-1]["messages"])


async def test_terminal_page_block_returns_without_a_second_llm_round(monkeypatch) -> None:
    import litellm

    calls: list[dict[str, Any]] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return _FakeStream([
            _chunk(
                tool_calls=[_tool_call_chunk(
                    0,
                    call_id="inspect-1",
                    name="inspect_page",
                    arguments='{"url":"https://blocked.test"}',
                )],
                finish="tool_calls",
            ),
        ])

    class _BlockedExecutor(_FakeExecutor):
        async def execute(
            self, tool_name: str, args: dict[str, Any],
            progress_sink: dict[str, Any] | None = None,
            change_context: Any = None,
        ) -> dict[str, Any]:
            self.calls.append((tool_name, args))
            return {
                "status": "blocked_page_access",
                "http_status": 403,
                "error": "目标页面返回 HTTP 403。",
                "required_action": "report_to_user_and_stop",
                "user_message": "请完成登录后再继续。",
            }

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    executor = _BlockedExecutor()
    orchestrator = AiOrchestrator(tool_executor=executor)  # type: ignore[arg-type]
    events = [event async for event in orchestrator.stream(
        messages=[{"role": "user", "content": "抓取 https://blocked.test 的正文"}],
        model="test-model",
    )]

    assert len(calls) == 1
    assert len(str(calls[0]["messages"][0]["content"])) < 6_000
    assert executor.calls == [("inspect_page", {"url": "https://blocked.test"})]
    assert "HTTP 403" in "".join(event.get("delta", "") for event in events if event["type"] == "text")
    assert events[-1] == {"type": "done"}


def test_waiting_for_the_user_does_not_burn_the_repair_budget() -> None:
    """停下来等人不是一次失败的修复，记进预算等于罚用户操作慢。"""
    state = _ready()
    for _ in range(VERIFY_ATTEMPT_BUDGET + 2):
        _orchestrator_guard_after_tool("run_flow", {"status": "paused_for_human"}, state)
        _orchestrator_guard_after_tool("run_flow", {"status": "waiting_for_user_input"}, state)
    assert state["attempt_budget"]["spent"] == 0
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is None

    # 真失败照常计价：同一条错误连着两次 = 1 + 2，正好压满额度
    for _ in range(2):
        _orchestrator_guard_after_tool("run_flow", {"status": "error", "error": "boom"}, state)
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is not None


def test_run_refused_before_it_started_does_not_burn_the_repair_budget() -> None:
    """起跑前被拒的运行一行都没跑，按「又白跑了一轮」计价是错的定价。

    `blocked_by_failure_budget` 最能说明问题：这把锁自己的拒绝在花掉产生它的额度，
    没有新失败、用户也看不到任何症状，只是越锁越死。但签名照样要记——同一条拒绝
    再来一次就按一次重复失败计价，否则这类拒绝一条收口都没有。
    """
    for status in sorted(_RUN_NOT_STARTED_STATUSES):
        refusal = {"status": status, "message": f"拒绝：{status}"}
        state = _ready()
        _orchestrator_guard_after_tool("run_flow", refusal, state)
        assert state["attempt_budget"]["spent"] == 0, status
        # 这条拒绝自己可能另有锁（failure_budget_lock），但收敛额度不该被它花掉
        first = _orchestrator_guard_before_tool("run_flow", {}, state)
        assert first is None or first["guard_id"] != "attempt_budget_exhausted", status

        # 撞同一堵墙就是原地打转：第二次起按重复失败计价，第三次压满额度
        _orchestrator_guard_after_tool("run_flow", refusal, state)
        assert state["attempt_budget"]["spent"] == 2, status
        _orchestrator_guard_after_tool("run_flow", refusal, state)
        blocked = _orchestrator_guard_before_tool("update_flow", {}, state)
        assert blocked, status
        # blocked_by_failure_budget 自带一把优先级更高的锁，拦它的是那把锁而不是这份额度；
        # 其余拒绝没有别的收口，必须由额度接住，否则模型能无限次撞同一堵墙
        if not state.get("failure_budget_lock"):
            assert blocked["guard_id"] == "attempt_budget_exhausted", status
            # 收尾要向用户要的是「清掉那条拒绝」，不是站点登录前提
            assert any("输入变量" in item for item in blocked["needed_from_user"]), status


def test_only_the_model_fixable_refusals_leave_the_run_unattempted() -> None:
    """撤回重写那条催跑规则唯一的开关是 run_attempted，置错就等于把它关掉。

    模型把 run_flow 的调用参数写错一次、接着写个总结收尾，用户要的验收结论一个字
    都没有，而催跑被自己的错误调用静默关掉——这条判据挂的是「这条拒绝谁能清掉」。
    """
    verification_request = {"latest_user_message": "帮我跑一下看看能不能用"}

    for status in sorted(_RUN_REFUSED_MODEL_FIXABLE):
        state = _ready(**verification_request)
        _orchestrator_guard_after_tool("run_flow", {"status": status, "message": "拒"}, state)
        assert not state.get("run_attempted"), status
        assert _unmet_verification_request("我已经检查了流程结构。", state) is not None, status

    # 只能等用户的那几条：模型确实跑不了，再催是空转
    for status in sorted(_RUN_NOT_STARTED_STATUSES - _RUN_REFUSED_MODEL_FIXABLE):
        state = _ready(**verification_request)
        _orchestrator_guard_after_tool("run_flow", {"status": status, "message": "拒"}, state)
        assert state["run_attempted"] is True, status
        assert _unmet_verification_request("我已经检查了流程结构。", state) is None, status


def test_every_run_flow_status_is_classified_as_started_or_not() -> None:
    """新增一条起跑前拒绝却没归类，就会静默按「跑过一次」计价——本文件其余断言全绿。

    只扫 `_run_flow` 自己返回的 status 字面量：真正跑过之后的状态由 task.status 决定，
    是另一条路径（success/error/timeout/stopped/paused_*），在下面显式列出。
    """
    source = inspect.getsource(RpaToolExecutor._run_flow)
    literals = set(re.findall(r'"status":\s*"([a-z_]+)"', source))
    assert literals, "取不到 run_flow 的 status 字面量，这条元测试会静默通过"

    # 起跑前拒绝之外，_run_flow 里出现的 status 只允许是这几个「跑过了」的终态
    after_the_run = {"success", "error", "timeout", "stopped"} | _RUN_WAITING_STATUSES
    unclassified = literals - _RUN_NOT_STARTED_STATUSES - after_the_run
    assert not unclassified, unclassified


def test_every_schema_tool_decides_whether_it_writes_facts() -> None:
    """新增一个工具而没人决定「它的返回写不写事实」，会静默什么都不写。

    这类缺陷没有症状：工具照常返回、对话照常继续，只是某条义务或某个熔断从此立不起来。
    所以每个暴露给模型的工具都必须在分派表或「不写事实」名单里出现一次，两张表还不许重叠
    ——同时出现意味着有人两边都加了一遍，谁生效取决于读代码的人先看到哪张。
    """
    schema_tools = {
        str(tool["function"]["name"]) for tool in TOOL_SCHEMAS if isinstance(tool, dict)
    }
    assert len(schema_tools) > 10, "取不到工具名，这条元测试会静默通过"

    decided = set(_AFTER_TOOL_HANDLERS) | _AFTER_TOOL_NO_STATE_EFFECT
    assert not schema_tools - decided, f"以下工具没表态：{sorted(schema_tools - decided)}"
    assert not decided - schema_tools, f"以下表项已不是工具：{sorted(decided - schema_tools)}"
    overlap = set(_AFTER_TOOL_HANDLERS) & _AFTER_TOOL_NO_STATE_EFFECT
    assert not overlap, f"两张表重叠：{sorted(overlap)}"
    # 写工具必须四个都有处理函数：漏掉一个，那次写入之后旧的运行结果仍被当成有效证据
    assert not set(_FLOW_WRITE_TOOLS) - set(_AFTER_TOOL_HANDLERS)


def test_the_two_lock_statuses_come_from_exactly_one_tool_each() -> None:
    """按工具分派的等价前提：这两个状态各自只有一个产出方。

    它们原先写成与工具无关的判断，现在收进 inspect_page / run_flow 各自的处理函数里。
    执行器哪天让别的工具也返回同一个状态，锁就会静默地不再立起来——那时该在这里红，
    而不是等到线上出现一次「拦截页明明拦到了，下一轮却照旧改流程」。
    """
    owners = {
        "blocked_challenge_page": "_inspect_page",
        "blocked_by_failure_budget": "_run_flow",
    }
    source = inspect.getsource(RpaToolExecutor)
    tree = ast.parse(textwrap.dedent(source))
    methods = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    for status, entry_point in owners.items():
        # 直接产出该状态字面量的方法，可以是入口自己，也可以是它调用的私有辅助方法
        producers = {
            m.name for m in methods
            if any(
                isinstance(n, ast.Constant) and n.value == status
                for n in ast.walk(m)
            )
        }
        assert producers, f"{status} 在执行器里已经没有产出点，判据该跟着删"
        reachable = {entry_point} | {
            n.func.attr
            for m in methods if m.name == entry_point
            for n in ast.walk(m)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert not producers - reachable, (
            f"{status} 还从 {sorted(producers - reachable)} 产出，"
            f"不再只属于 {entry_point}，写入侧的分派要跟着改"
        )


def test_failure_budget_lock_still_allows_read_only_diagnosis() -> None:
    """挡掉纯读工具等于没收诊断手段，模型只能在剩下几个工具间空转。"""
    state = _ready(failure_budget_lock={"flow_id": "f1"})
    for tool in ("list_node_types", "get_run_output", "get_run_logs", "get_run_error"):
        assert _orchestrator_guard_before_tool(tool, {}, state) is None
    # 写入与运行仍然挡住，这才是这道闸的本职；断言 guard_id 是为了确认拦它的是这道闸
    for tool in ("update_flow", "run_flow"):
        blocked = _orchestrator_guard_before_tool(tool, {}, state)
        assert blocked and blocked["guard_id"] == "failure_budget_lock"


def test_repeated_failed_runs_exhaust_the_attempt_budget() -> None:
    """同一条错误再来一次按两份算：这是「换维度不再是换额度」的唯一实现。"""
    state = _ready()
    failure = {"status": "error", "error": "Page.goto: Timeout 30000ms exceeded"}
    _orchestrator_guard_after_tool("run_flow", failure, state)
    assert _orchestrator_guard_before_tool("update_flow", {}, state) is None

    # 毫秒数每次都不同，签名要把数字折掉才认得出是同一个失败
    _orchestrator_guard_after_tool(
        "run_flow", {"status": "error", "error": "Page.goto: Timeout 45000ms exceeded"}, state
    )
    blocked = _orchestrator_guard_before_tool("update_flow", {}, state)
    assert blocked and blocked["required_action"] == "report_to_user_and_stop"
    # 写入和再次运行都得拦住，只放诊断类工具
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is not None
    assert _orchestrator_guard_before_tool("get_run_error", {}, state) is None


def test_quality_failures_charge_the_same_budget_as_run_errors() -> None:
    """跑得起来但审计不合格是这类循环的主要形态，只数 run_flow 失败会完全数不到。"""
    state = _ready()
    audit_failed = {"passed": False, "issues": [{"issue": "mixed_ui_rows", "message": "混入 UI 行"}]}
    for _ in range(2):
        _orchestrator_guard_after_tool(
            "run_flow", {"status": "success", "acceptance_audit": audit_failed}, state
        )
    assert _orchestrator_guard_before_tool("update_flow", {}, state) is not None


def test_passing_audit_resets_the_attempt_budget() -> None:
    """审计通过才是一轮闭环，之后提新需求不该背着旧的失败计数。"""
    state = _ready()
    _orchestrator_guard_after_tool("run_flow", {"status": "error", "error": "boom"}, state)
    _orchestrator_guard_after_tool(
        "run_flow", {"status": "success", "acceptance_audit": {"passed": True}}, state
    )
    assert state["attempt_budget"]["spent"] == 0
    # 归零后同一条错误只是「第一次」，不该按重复计价
    _orchestrator_guard_after_tool("run_flow", {"status": "error", "error": "boom"}, state)
    assert _orchestrator_guard_before_tool("update_flow", {}, state) is None


def test_create_intent_needs_url_and_is_suppressed_by_repair_intent() -> None:
    blank = FlowState(is_blank=True)
    msgs = [{"role": "user", "content": "帮我抓取 https://example.com 的表格"}]
    detected = _detect_turn_intents(msgs, "flow-1", blank)
    assert detected.create_requested is True
    assert detected.create_url == "https://example.com"
    # 没有 URL 就无从 inspect，不激活 gate
    missing_url = _detect_turn_intents([{"role": "user", "content": "帮我建个流程"}], None, blank)
    assert missing_url.create_requested is True
    assert missing_url.create_url is None
    # "抓不全" 是修复而非新建
    repair = _detect_turn_intents(
        [{"role": "user", "content": "https://example.com 抓不全，帮我修一下"}], "flow-1", blank
    )
    assert repair.repair and repair.create_url is None


def test_run_authorization_is_detected_only_from_an_explicit_ask() -> None:
    """"审查/验收/跑一下" 是要运行证据；只说"修一下"就不许自作主张跑。

    这个判据决定 run_authorized 给不给——判宽了会背着用户跑生产流程，
    判窄了则让"帮我验收"这类请求永远交不出运行结果。
    """
    blank = FlowState(is_blank=True)
    for text in ("帮我跑一下看对不对", "运行验证一下", "重跑一次", "这流程能不能用"):
        assert _detect_turn_intents(
            [{"role": "user", "content": text}], "flow-1", blank
        ).run_authorized is True, text

    for text in ("帮我修一下抓不全的问题", "先别运行，只改配置", "优化一下选择器"):
        assert _detect_turn_intents(
            [{"role": "user", "content": text}], "flow-1", blank
        ).run_authorized is False, text


def test_misapplied_refusal_is_rewritten_only_when_the_turn_was_actionable() -> None:
    """拒答模板本身要留着（真无关话题还得用），错的是把它用在职责范围内的请求上。"""
    refusal = "我只能协助处理 RPA 流程的创建、修复与运行，其他话题请另找途径。"
    actionable: dict[str, Any] = {"turn_intent_actionable": True}
    assert _misapplied_refusal(refusal, actionable) is not None
    # 判定自己记账：同一轮只纠一次，否则重写文本若仍带模板就会无限撤回
    assert actionable["refusal_corrected"] is True
    assert _misapplied_refusal(refusal, actionable) is None

    # 本轮确实是闲聊，拒答是正确输出
    assert _misapplied_refusal(refusal, {"turn_intent_actionable": False}) is None
    # 正常答复不触发
    assert _misapplied_refusal(
        "已把节点 n2 的选择器改成 table.data", {"turn_intent_actionable": True}
    ) is None


async def test_local_draft_flow_uses_blank_creation_context() -> None:
    class _Executor:
        async def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("local draft 不应查询后端流程")

    state = await build_flow_state(_Executor(), "local-1785390406146")  # type: ignore[arg-type]
    assert state.is_blank is True
    assert render_flow_state(state) is None

    messages = [{"role": "user", "content": "抓取 https://example.com/posts 的帖子"}]
    intents = _detect_turn_intents(messages, "local-1785390406146", state)
    assert intents.create_requested is True
    assert intents.create_url == "https://example.com/posts"
    assert is_local_draft_flow_id("local-1785390406146") is True
    assert is_local_draft_flow_id("flow-1") is False


def test_state_block_is_replaced_at_the_tail_not_stacked() -> None:
    """两份状态块同时在场比没有状态更糟：模型无从判断该信哪一份。

    这里同时钉住位置——必须在消息尾部。状态块是「当前」事实，排在历史工具返回之后
    才压得住它们；混到中间就成了又一条会被后文覆盖的旧消息。
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "改一下这个流程"},
        {"role": "assistant", "content": "好"},
    ]
    sync_state_message(messages, '<flow-state revision="3">\nA\n</flow-state>')
    sync_state_message(messages, '<flow-state revision="4">\nB\n</flow-state>')

    blocks = [m for m in messages if str(m.get("content", "")).startswith("<flow-state")]
    assert len(blocks) == 1
    assert "revision=\"4\"" in blocks[0]["content"]
    assert messages[-1] is blocks[0]
    assert len(messages) == 3

    # 这一轮没有状态可讲（本地草稿 / 读取前）：旧的那份也得撤掉，不能留着当最新
    sync_state_message(messages, None)
    assert all(not str(m.get("content", "")).startswith("<flow-state") for m in messages)


def test_continuation_recovers_the_active_task_from_real_tool_history() -> None:
    blank = FlowState(is_blank=True)
    messages = [
        {"role": "user", "content": "https://example.com/post/1，帖子主题及回帖"},
        {
            "role": "assistant",
            "content": "页面返回 403。",
            "toolCalls": [{
                "tool": "inspect_page",
                "args": '{"url":"https://example.com/post/1"}',
                "result": {
                    "status": "blocked_page_access",
                    "requested_url": "https://example.com/post/1",
                    "http_status": 403,
                },
            }],
        },
        {"role": "user", "content": "继续创建"},
    ]

    state = _resolve_resumable_task_state(messages, "flow-1", blank)
    assert state.target_url == "https://example.com/post/1"
    assert state.target_source == "inspection_history"
    assert state.phase == "page_inspection_blocked"
    assert state.last_inspection_status == "blocked_page_access"
    assert "帖子主题及回帖" in state.requirement_text
    assert "继续创建" not in state.requirement_text

    detected = _detect_turn_intents(messages, "flow-1", blank, state)
    assert detected.create_requested is True
    assert detected.create_url == "https://example.com/post/1"

    context = _task_state_message(state)
    assert context is not None
    assert "retry_page_inspection" in str(context["content"])


def test_new_task_does_not_inherit_the_previous_target() -> None:
    blank = FlowState(is_blank=True)
    messages = [
        {"role": "user", "content": "抓取 https://old.example.com/list"},
        {"role": "assistant", "content": "需要用户处理。"},
        {"role": "user", "content": "继续创建另一个流程"},
    ]

    state = _resolve_resumable_task_state(messages, "flow-1", blank)
    assert state.target_url is None
    detected = _detect_turn_intents(messages, "flow-1", blank, state)
    assert detected.create_requested is True
    assert detected.create_url is None


def test_explicit_new_url_overrides_the_previous_task_target() -> None:
    blank = FlowState(is_blank=True)
    messages = [
        {"role": "user", "content": "抓取 https://old.example.com/list"},
        {"role": "assistant", "content": "页面不可访问。"},
        {"role": "user", "content": "换一个网站，继续抓取 https://new.example.com/list"},
    ]

    state = _resolve_resumable_task_state(messages, "flow-1", blank)
    assert state.target_url == "https://new.example.com/list"
    assert state.target_source == "current_message"
    detected = _detect_turn_intents(messages, "flow-1", blank, state)
    assert detected.create_url == "https://new.example.com/list"


def test_static_evidence_channel_outlives_the_flow_being_saved() -> None:
    """流程存下来之后 _resolve_resumable_task_state 提前返回，但证据通道不能跟着断。

    这条断的是最贵的一种静默失败：第一轮浏览器通道拿不到页面、降级成静态抓取，
    第二轮护栏一消失，模型改个 fetcher 就把执行通道换回刚刚失败的 Playwright，
    而流程照样存下来、照样交付给用户。
    """
    messages = [
        {"role": "user", "content": "抓取 https://example.com/list"},
        {
            "role": "assistant",
            "content": "",
            "toolCalls": [{
                "tool": "inspect_page",
                "args": '{"url":"https://example.com/list"}',
                "result": {
                    "requested_url": "https://example.com/list",
                    "status": "success",
                    "inspection_source": "scrapling_static",
                },
            }],
        },
        {"role": "user", "content": "字段少了一个"},
    ]

    built = _resolve_resumable_task_state(messages, "flow-1", FlowState(is_blank=False))
    assert built.last_inspection_source == "scrapling_static"
    assert built.target_url is None, "已有流程时不该再改写目标 URL，只取证据通道"

    # 浏览器通道恢复可用是唯一的解锁方式，靠的是新的探测结果而不是时间
    messages.append({
        "role": "assistant",
        "content": "",
        "toolCalls": [{
            "tool": "inspect_page",
            "args": '{"url":"https://example.com/list"}',
            "result": {"requested_url": "https://example.com/list", "status": "success"},
        }],
    })
    recovered = _resolve_resumable_task_state(messages, "flow-1", FlowState(is_blank=False))
    assert recovered.last_inspection_source is None


def test_successful_inspection_resumes_at_build_instead_of_reinspecting() -> None:
    blank = FlowState(is_blank=True)
    messages = [
        {"role": "user", "content": "抓取 https://example.com/list 的标题"},
        {
            "role": "assistant",
            "content": "",
            "toolCalls": [{
                "tool": "inspect_page",
                "args": '{"url":"https://example.com/list"}',
                "result": {
                    "requested_url": "https://example.com/list",
                    "title": "列表",
                    "page_layout": [{"tag": "main"}],
                },
            }],
        },
        {"role": "user", "content": "继续"},
    ]

    state = _resolve_resumable_task_state(messages, "flow-1", blank)
    assert state.phase == "page_inspected"
    context = _task_state_message(state)
    assert context is not None and "build_flow" in str(context["content"])

    intents = _detect_turn_intents(messages, "flow-1", blank, state)
    guard_state = _ready(
        flow_has_nodes=False,
        page_evidence_required={"url": "https://example.com/list", "reason": "build_from_page"},
        page_evidence_done=state.phase == "page_inspected",
    )
    schemas = _tool_schemas_for_round(guard_state, intents)
    assert len(schemas) > 1


def test_existing_browser_chain_is_protected_unless_switch_is_explicit() -> None:
    ctx = FlowState(browser_chain_node_ids={"n1"})
    msgs = [{"role": "user", "content": "数据少了一半"}]
    assert _detect_turn_intents(msgs, "flow-1", ctx).preserve_execution_channel
    # 无 flow_id 时没有存量链路可保护
    assert not _detect_turn_intents(msgs, None, ctx).preserve_execution_channel


def _image_message(label: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": label},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "x" * 1000}},
        ],
    }


def test_anthropic_system_prompt_carries_a_cache_breakpoint() -> None:
    """Anthropic 走原生端点时要打缓存断点：4.4 万字符的前缀每轮原样重发。"""
    from app.services.ai_orchestrator import SYSTEM_PROMPT

    msg = _build_system_message("claude-sonnet-5", relayed=False)
    assert msg["content"] == [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]


def test_cache_breakpoint_is_skipped_for_relays_and_other_providers() -> None:
    """中转是否透传 cache_control 不可知；OpenAI/DeepSeek 自动缓存，标记反而是多余字段。"""
    from app.services.ai_orchestrator import SYSTEM_PROMPT

    # 同一个 Anthropic 模型，一旦走中转就退回纯字符串
    assert _build_system_message("claude-sonnet-5", relayed=True)["content"] == SYSTEM_PROMPT
    assert _build_system_message("gpt-5.5", relayed=False)["content"] == SYSTEM_PROMPT
    assert _build_system_message("deepseek/deepseek-v4-pro", relayed=False)["content"] == SYSTEM_PROMPT
    # 目录外的未知模型按名字兜底识别
    assert isinstance(_build_system_message("claude-experimental", relayed=False)["content"], list)


def test_few_shot_gets_its_own_breakpoint_without_mutating_the_shared_list() -> None:
    """few-shot 落在 system 断点之后，不单独打断点就是每轮 1.5 万字符原价重发。"""
    from app.services.ai_orchestrator import _FEW_SHOT_MESSAGES

    original_tail = json.dumps(_FEW_SHOT_MESSAGES[-1], ensure_ascii=False)

    block = _build_few_shot_block("claude-sonnet-5", relayed=False)
    assert block[-1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert block[-1]["content"][0]["text"] == _FEW_SHOT_MESSAGES[-1]["content"]
    # 前面的示例逐条透传，只有末条被换掉
    assert block[:-1] == _FEW_SHOT_MESSAGES[:-1]

    # 模块级列表被共享，就地改会让下一个非 Anthropic 请求也带上标记
    assert json.dumps(_FEW_SHOT_MESSAGES[-1], ensure_ascii=False) == original_tail
    assert _build_few_shot_block("gpt-5.5", relayed=False) is _FEW_SHOT_MESSAGES
    assert _build_few_shot_block("claude-sonnet-5", relayed=True) is _FEW_SHOT_MESSAGES


def test_over_budget_history_drops_whole_turns_and_says_so() -> None:
    """压缩是单调的：全压过之后仍超预算就只能整轮丢，否则下一轮直接撞窗口。"""
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "第一轮" + "x" * 5_000},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "y" * 5_000},
        {"role": "user", "content": "第二轮"},
        {"role": "assistant", "content": "好的"},
    ]
    _compact_tool_messages(messages, budget=2_000, protect_prefix=1)

    # system 前缀保留；最后一轮永远保留
    assert messages[0]["content"] == "SYS"
    assert messages[-2]["content"] == "第二轮"
    assert messages[-1]["content"] == "好的"
    # 丢弃在 user 处切，不会留下没有 assistant.tool_calls 配对的孤儿 tool 消息
    assert not any(m.get("role") == "tool" for m in messages)
    assert str(messages[1]["content"]).startswith("[上下文超限，已丢弃最早的")


def test_repeated_over_budget_replaces_the_drop_notice_instead_of_stacking() -> None:
    """同一次会话可能轮轮超预算，提示叠加只会自己吃掉上下文。"""
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "system", "content": "[上下文超限，已丢弃最早的 4 条历史消息；…]"},
        {"role": "user", "content": "z" * 5_000},
        {"role": "user", "content": "最后一轮"},
    ]
    _compact_tool_messages(messages, budget=1_000, protect_prefix=1)

    notices = [m for m in messages if str(m.get("content") or "").startswith("[上下文超限")]
    assert len(notices) == 1
    assert messages[-1]["content"] == "最后一轮"


def test_dropping_history_keeps_the_users_hard_requirements() -> None:
    """约束通常只在最早那几轮说一次，正好是超预算时最先被丢掉的部分。

    丢完之后模型会安静地退回默认做法，用户只能再说一遍——而且往往看不出是上下文没了。
    """
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "抓一下订单页。日期必须用键盘输入，不要点日历控件。" + "x" * 3_000},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "最后一轮"},
    ]
    _compact_tool_messages(messages, budget=1_000, protect_prefix=1)

    kept = [m for m in messages if str(m.get("content") or "").startswith("【用户此前提出的硬性要求】")]
    assert len(kept) == 1
    body = str(kept[0]["content"])
    assert "日期必须用键盘输入" in body
    assert "不要点日历控件" in body
    # 摘的是原句，不是改写——改写过的约束就成了模型自己的话，没有任何东西能校验它
    assert "抓一下订单页" not in body


def test_kept_requirements_survive_a_second_round_of_dropping() -> None:
    """摘要本身也躺在会被删的区间里；第二次超预算时它不该跟着原文一起消失。"""
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "导出必须是 xlsx，不要 csv。"},
        {"role": "assistant", "content": "好"},
        {"role": "user", "content": "y" * 3_000},
        {"role": "assistant", "content": "好"},
        {"role": "user", "content": "最后一轮"},
    ]
    _compact_tool_messages(messages, budget=1_500, protect_prefix=1)
    _compact_tool_messages(messages, budget=200, protect_prefix=1)

    kept = [m for m in messages if str(m.get("content") or "").startswith("【用户此前提出的硬性要求】")]
    assert len(kept) == 1, "摘要要合并而不是层层叠加"
    assert "导出必须是 xlsx" in str(kept[0]["content"])


def _tool_msg(index: int, size: int = 100) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": f"c{index}", "content": f"r{index}" * size}


def test_cache_anchor_stays_behind_the_rewrite_frontier() -> None:
    """锚点必须落在还会被压缩改写的两条之前，否则一次改写让整段缓存作废。"""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "SYS"},
        _tool_msg(1),
        _tool_msg(2),
        _tool_msg(3),
        _tool_msg(4),
    ]
    _mark_history_cache_anchor(messages, "claude-sonnet-5", relayed=False)

    anchored = [i for i, m in enumerate(messages) if m.get("cache_control")]
    assert anchored == [2], "第 3、4 条是保留全文窗口，锚点只能打在第 2 条"


def test_cache_anchor_never_crosses_the_newest_screenshot() -> None:
    """最新截图会在下一张到来时被换成占位符，划进稳定区就等于锚在会变的内容上。"""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "SYS"},
        _tool_msg(1),
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:x"}}]},
        _tool_msg(2),
        _tool_msg(3),
        _tool_msg(4),
    ]
    _mark_history_cache_anchor(messages, "claude-sonnet-5", relayed=False)

    anchored = [i for i, m in enumerate(messages) if m.get("cache_control")]
    assert anchored == [1]


def test_cache_anchor_moves_instead_of_accumulating() -> None:
    """Anthropic 断点上限 4 个、system 与 few-shot 已占 2 个，旧锚点不撤就会顶掉新的。"""
    messages: list[dict[str, Any]] = [{"role": "system", "content": "SYS"}, _tool_msg(1), _tool_msg(2), _tool_msg(3)]
    _mark_history_cache_anchor(messages, "claude-sonnet-5", relayed=False)
    messages.extend([_tool_msg(4), _tool_msg(5)])
    _mark_history_cache_anchor(messages, "claude-sonnet-5", relayed=False)

    anchored = [i for i, m in enumerate(messages) if m.get("cache_control")]
    assert anchored == [3], "只留一个锚点，且跟着前沿前移"


def test_cache_anchor_is_skipped_when_the_provider_cannot_read_it() -> None:
    """非 Anthropic 与中转透传都不认 cache_control，多送一个字段可能直接被判非法参数。"""
    for model, relayed in (("zai/glm-4.6", False), ("claude-sonnet-5", True)):
        messages: list[dict[str, Any]] = [{"role": "system", "content": "SYS"}, _tool_msg(1), _tool_msg(2), _tool_msg(3)]
        _mark_history_cache_anchor(messages, model, relayed=relayed)
        assert not any(m.get("cache_control") for m in messages)


def test_stable_prefix_is_empty_before_enough_tool_results() -> None:
    """工具结果不够填满保留窗口时没有稳定区，此时打锚点等于锚在下一轮就要改的内容上。"""
    messages: list[dict[str, Any]] = [{"role": "system", "content": "SYS"}, _tool_msg(1), _tool_msg(2)]
    assert _stable_prefix_end(messages) == 0
    _mark_history_cache_anchor(messages, "claude-sonnet-5", relayed=False)
    assert not any(m.get("cache_control") for m in messages)


def test_identical_repeated_tool_result_is_elided_but_a_changed_one_is_not() -> None:
    """逐字相同才折叠：页面被改动过结果就不相等，不存在把旧状态当新状态交出去的路径。"""
    seen: dict[tuple[str, str], str] = {}
    args = '{"url":"https://x/list"}'
    big = {"html": "a" * _ELIDE_MIN_CHARS}

    assert "_unchanged" not in _elide_repeated_result("inspect_page", args, big, seen)
    assert '"_unchanged": true' in _elide_repeated_result("inspect_page", args, big, seen)
    changed = _elide_repeated_result(
        "inspect_page", args, {"html": "b" * _ELIDE_MIN_CHARS}, seen
    )
    assert "_unchanged" not in changed
    # 换了参数就是另一个页面，与上一次相同与否无关
    other = _elide_repeated_result("inspect_page", '{"url":"https://x/2"}', big, seen)
    assert "_unchanged" not in other


def test_small_repeated_results_are_resent_in_full() -> None:
    """指针本身也占字符，小结果重发比指回去更省。"""
    seen: dict[tuple[str, str], str] = {}
    small = {"ok": True}
    first = _elide_repeated_result("get_run_output", "{}", small, seen)
    assert _elide_repeated_result("get_run_output", "{}", small, seen) == first
    assert "_unchanged" not in first


def test_chit_chat_does_not_become_a_standing_requirement() -> None:
    """把闲聊当成硬性要求钉进上下文，比丢掉它更糟：模型会一直照着它做。"""
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "帮我看看这个页面能不能抓" + "x" * 3_000},
        {"role": "user", "content": "最后一轮"},
    ]
    _compact_tool_messages(messages, budget=1_000, protect_prefix=1)

    assert not any(str(m.get("content") or "").startswith("【用户此前提出的硬性要求】") for m in messages)


def test_navigation_failures_are_classified_on_every_selector_diagnostic() -> None:
    """诊断名写错不会报错，只会让这条判据永远不命中，而下面的关键词兜底会替它遮住。

    所以 selector 特意用不含任何导航关键词的写法把兜底那条路堵死：还能归类成导航失败，
    就只可能是诊断类型这条判据真的生效了。

    归类而不是再扣一次费：这次失败的费在 run_flow 那一刻就扣过了。归类决定预算见底时
    向用户要什么——导航类要的是目标 URL，别的类不是。
    """
    from app.services.ai_orchestrator import _orchestrator_guard_after_tool
    from app.services.ai_tools.diagnostics import SELECTOR_DIAGNOSTIC_KINDS

    for kind in SELECTOR_DIAGNOSTIC_KINDS:
        state = _ready()
        _orchestrator_guard_after_tool(
            "run_flow", {"status": "error", "error": "click timeout"}, state
        )
        spent_before = state["attempt_budget"]["spent"]
        _orchestrator_guard_after_tool("get_run_error", {
            "inspect_hint": True,
            "last_browser_url": "https://x.test/",
            "failed_node_id": "n_go",
            "failed_node_config": {"id": "n_go", "type": "browser.click", "selector": ".c1 .c2"},
            "selector_diagnostic": {"kind": kind},
        }, state)
        assert state.get("navigation_failure_hint"), kind
        assert state["attempt_budget"]["attempts"][-1]["kind"] == "navigation", kind
        assert state["attempt_budget"]["spent"] == spent_before, kind

    # 预算见底后，导航类归类给出的是「向用户要目标 URL」这条出路
    state = _ready()
    for error in ("click timeout", "click timeout"):
        _orchestrator_guard_after_tool("run_flow", {"status": "error", "error": error}, state)
        _orchestrator_guard_after_tool("get_run_error", {
            "inspect_hint": True,
            "last_browser_url": "https://x.test/",
            "failed_node_id": "n_go",
            "failed_node_config": {"id": "n_go", "type": "browser.click", "selector": ".c1 .c2"},
            "selector_diagnostic": {"kind": next(iter(SELECTOR_DIAGNOSTIC_KINDS))},
        }, state)
    blocked = _orchestrator_guard_before_tool("update_flow", {}, state)
    assert blocked and blocked["required_action"] == "needs_user_navigation_target"
    assert blocked["needed_from_user"]


def test_context_budget_follows_the_model_window() -> None:
    """40 万字符的固定阈值对 13 万窗口的小模型等于毫无保护。"""
    # 小窗口模型按窗口收紧
    assert _context_char_budget("openai/qwen3.6-flash") == int(131_072 * 0.7 * 1.5)
    assert _context_char_budget("zai/glm-4.6") == int(200_000 * 0.7 * 1.5)
    # 百万窗口不按窗口放开，压到实用上限
    assert _context_char_budget("claude-sonnet-5") == 400_000
    # 目录里查不到的模型退回默认窗口，而不是变成无上限
    assert _context_char_budget("某个没登记的模型") == int(200_000 * 0.7 * 1.5)


def test_history_tool_calls_expand_into_paired_tool_messages() -> None:
    """纯工具回合必须还原成 tool_calls + tool 消息，否则模型看不到上一轮做了什么。"""
    out = _expand_history_tool_calls([
        {"role": "user", "content": "抓一下这个页面"},
        {
            "role": "assistant",
            "content": "",
            "toolCalls": [
                {"tool": "inspect_page", "args": '{"url":"https://x.test"}', "result": {"title": "X"}},
                # 被中止的调用没有 result，仍要配一条 tool 消息，否则 tool_calls 悬空
                {"tool": "run_flow", "args": "", "result": None},
            ],
        },
        {"role": "user", "content": "继续"},
    ])
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "tool", "user"]

    assistant = out[1]
    # content 为空要落成 None：空字符串 assistant 消息被部分厂商判为非法输入
    assert assistant["content"] is None
    call_ids = [c["id"] for c in assistant["tool_calls"]]
    assert len(set(call_ids)) == 2
    assert [c["function"]["name"] for c in assistant["tool_calls"]] == ["inspect_page", "run_flow"]
    # 缺失的 args 补成合法 JSON，否则厂商侧解析直接报错
    assert assistant["tool_calls"][1]["function"]["arguments"] == "{}"

    assert [m["tool_call_id"] for m in out[2:4]] == call_ids
    assert json.loads(out[2]["content"]) == {"title": "X"}
    assert json.loads(out[3]["content"])["status"] == "interrupted"


def test_history_assistant_without_content_or_tools_is_dropped() -> None:
    out = _expand_history_tool_calls([
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": "在"},
    ])
    assert out == [{"role": "user", "content": "在吗"}, {"role": "assistant", "content": "在"}]


def test_compact_tool_messages_strips_all_but_latest_screenshot() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        _image_message("[截图1]"),
        {"role": "assistant", "content": "看过了"},
        _image_message("[截图2]"),
    ]
    _compact_tool_messages(messages)
    # 旧截图替换为文本占位（保留原文字说明），最新一张保持 vision 块
    assert isinstance(messages[1]["content"], str)
    assert "[截图1]" in messages[1]["content"]
    assert _OLD_SCREENSHOT_PLACEHOLDER in messages[1]["content"]
    assert isinstance(messages[3]["content"], list)


class _FakeStream:
    def __init__(self, chunks: list[Any]) -> None:
        self._it = iter(chunks)

    def __aiter__(self) -> "_FakeStream":
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


def _chunk(*, content: str | None = None, tool_calls: list[Any] | None = None, finish: str | None = None) -> Any:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


def _tool_call_chunk(index: int, *, call_id: str = "", name: str | None = None, arguments: str | None = None) -> Any:
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=call_id, function=fn)


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self, tool_name: str, args: dict[str, Any],
        progress_sink: dict[str, Any] | None = None,
        change_context: Any = None,
    ) -> dict[str, Any]:
        self.calls.append((tool_name, args))
        if tool_name == "create_flow":
            return {"flow_id": "flow-1", "status": "created"}
        return {"status": "ok"}


class _RevisionFlowExecutor(_FakeExecutor):
    async def execute(
        self, tool_name: str, args: dict[str, Any],
        progress_sink: dict[str, Any] | None = None,
        change_context: Any = None,
    ) -> dict[str, Any]:
        self.calls.append((tool_name, args))
        if tool_name == "get_flow":
            return {
                "flow_id": "flow-verified",
                "revision": 7,
                "definition": {
                    "nodes": [
                        {"id": "start", "type": "start", "title": "开始"},
                        {"id": "end", "type": "end", "title": "结束"},
                    ],
                    "edges": [{"id": "e1", "source": "start", "target": "end"}],
                },
            }
        return {"status": "ok"}


async def test_every_round_carries_exactly_one_fresh_state_block(monkeypatch) -> None:
    """状态块必须真的进到发给模型的那份 messages 里，且每轮只有一份、排在最后。

    这是整套设计唯一的承重点：状态块到不了模型手上，「不必再去查证」就成了空话，而删掉的
    读取工具让它连查证的路都没有。单测只能证明 sync_state_message 自己对——发给上游的
    列表是不是同一个列表，只有走完 stream 才知道。
    """
    import litellm

    captured: list[dict[str, Any]] = []
    rounds = iter([
        _FakeStream([_chunk(
            tool_calls=[_tool_call_chunk(0, call_id="logs-1", name="get_run_logs", arguments="{}")],
            finish="tool_calls",
        )]),
        _FakeStream([_chunk(content="流程只有 start/end 骨架。", finish="stop")]),
    ])

    async def fake_acompletion(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return next(rounds)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    orchestrator = AiOrchestrator(tool_executor=_RevisionFlowExecutor())  # type: ignore[arg-type]
    async for _ in orchestrator.stream(
        messages=[{"role": "user", "content": "这个流程现在什么状态？把运行日志也看一下"}],
        model="test-model",
        flow_id="flow-verified",
    ):
        pass

    assert len(captured) == 2
    for kwargs in captured:
        blocks = [
            m for m in kwargs["messages"]
            if isinstance(m.get("content"), str) and m["content"].startswith("<flow-state")
        ]
        assert len(blocks) == 1
        assert 'revision="7"' in blocks[0]["content"]
        # 排在最后才压得住历史工具返回里那些已经过期的版本
        assert kwargs["messages"][-1] is blocks[0]
        # 状态块答完的问题不该再有对应的工具可调，否则模型仍会花一轮去问
        offered = {item["function"]["name"] for item in kwargs.get("tools") or []}
        assert not offered & {"get_flow", "lint_flow", "validate_flow", "get_run_status"}


async def test_read_only_reply_does_not_claim_the_assistant_modified_the_flow(monkeypatch) -> None:
    """验证状态挂在消息气泡上，只读追问复用旧状态会被误读成“本轮做了修改”。"""
    import litellm

    async def fake_acompletion(**kwargs: Any) -> Any:
        return _FakeStream([_chunk(content="当前流程可以继续使用。", finish="stop")])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(
        "app.services.ai_orchestrator.load_verification_state",
        lambda *_args: {
            "current_flow_revision": 7,
            "run_verified_revision": 7,
            "accepted_revision": None,
        },
    )

    orchestrator = AiOrchestrator(tool_executor=_RevisionFlowExecutor())  # type: ignore[arg-type]
    events = [event async for event in orchestrator.stream(
        messages=[{"role": "user", "content": "这个流程现在是什么状态？"}],
        model="test-model",
        flow_id="flow-verified",
    )]

    verification = [event for event in events if event["type"] == "verification"]
    assert verification == []


async def test_parallel_tool_calls_after_create_flow_get_placeholder_responses(monkeypatch) -> None:
    """P0 回归：create_flow 成功后 break 跳过的并行调用必须补 tool 应答，
    且流式返回的空 tool_call id 要合成兜底值——否则严格 OpenAI 兼容端点
    会在下一轮以 400 拒绝整个对话。"""
    import litellm

    captured_messages: list[list[dict[str, Any]]] = []
    rounds = iter([
        # 第 1 轮：并行发出 create_flow + list_node_types，且两个调用都不带 id
        _FakeStream([
            _chunk(tool_calls=[_tool_call_chunk(0, name="create_flow", arguments='{"name": "测试"}')]),
            _chunk(tool_calls=[_tool_call_chunk(1, name="list_node_types", arguments='{"category": "browser"}')], finish="tool_calls"),
        ]),
        # 第 2 轮：纯文本收尾
        _FakeStream([_chunk(content="已创建流程。", finish="stop")]),
    ])

    async def fake_acompletion(**kwargs: Any) -> Any:
        captured_messages.append([dict(m) for m in kwargs["messages"]])
        return next(rounds)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    executor = _FakeExecutor()
    orchestrator = AiOrchestrator(tool_executor=executor)  # type: ignore[arg-type]
    events = [e async for e in orchestrator.stream(
        messages=[{"role": "user", "content": "帮我建一个流程"}], model="test-model",
    )]

    # list_node_types 没有真正执行（create_flow 后 break）……
    assert [c[0] for c in executor.calls] == ["create_flow"]
    # ……但拿到了 skipped 占位结果事件
    skipped = [e for e in events if e["type"] == "tool_result" and e["tool"] == "list_node_types"]
    assert len(skipped) == 1 and skipped[0]["result"]["status"] == "skipped"

    # 第 2 轮请求里：assistant 的每个 tool_call 都有非空 id 和对应的 tool 应答
    second_round = captured_messages[1]
    assistant = next(m for m in second_round if m.get("role") == "assistant" and m.get("tool_calls"))
    tool_response_ids = {m["tool_call_id"] for m in second_round if m.get("role") == "tool"}
    for tc in assistant["tool_calls"]:
        assert tc["id"], "空 tool_call id 必须被合成兜底值"
        assert tc["id"] in tool_response_ids, f"tool_call {tc['function']['name']} 缺少应答消息"

    assert events[-1] == {"type": "done"}


async def test_parallel_calls_of_same_tool_get_distinct_call_ids(monkeypatch) -> None:
    """同轮并行调用同一个工具时，每次调用的事件必须带各自的 call_id。

    前端只能按工具名匹配的话，两次结果会都盖到第一张卡片上，第二张永远转圈。
    """
    import litellm

    rounds = iter([
        _FakeStream([
            _chunk(tool_calls=[_tool_call_chunk(0, name="inspect_page", arguments='{"url": "https://a.test"}')]),
            _chunk(tool_calls=[_tool_call_chunk(1, name="inspect_page", arguments='{"url": "https://b.test"}')],
                   finish="tool_calls"),
        ]),
        _FakeStream([_chunk(content="两页都看过了。", finish="stop")]),
    ])

    async def fake_acompletion(**kwargs: Any) -> Any:
        return next(rounds)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    orchestrator = AiOrchestrator(tool_executor=_FakeExecutor())  # type: ignore[arg-type]
    events = [e async for e in orchestrator.stream(
        messages=[{"role": "user", "content": "看看这两个页面"}], model="test-model",
    )]

    starts = [e for e in events if e["type"] == "tool_start"]
    assert len({e["call_id"] for e in starts}) == 2, "两次调用必须拿到不同的 call_id"

    # 参数与结果都要回到各自的 call_id 上
    args_by_id = {e["call_id"]: e["args"] for e in events if e["type"] == "tool_args"}
    assert set(args_by_id) == {e["call_id"] for e in starts}
    assert sorted(args_by_id.values()) == ['{"url": "https://a.test"}', '{"url": "https://b.test"}']
    assert {e["call_id"] for e in events if e["type"] == "tool_result"} == set(args_by_id)


async def test_empty_response_mid_session_retries_once(monkeypatch) -> None:
    """P2 回归：非首轮出现一次空响应应注入提示重试，而不是立刻终止会话。"""
    import litellm

    rounds = iter([
        _FakeStream([]),                                        # 第 1 轮：空响应 → 重试
        _FakeStream([_chunk(content="正常回复", finish="stop")]),  # 第 2 轮：恢复
    ])

    async def fake_acompletion(**kwargs: Any) -> Any:
        return next(rounds)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    orchestrator = AiOrchestrator(tool_executor=_FakeExecutor())  # type: ignore[arg-type]
    events = [e async for e in orchestrator.stream(
        messages=[{"role": "user", "content": "你好"}], model="test-model",
    )]

    assert not [e for e in events if e["type"] == "error"]
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "正常回复"


class _BlankFlowExecutor(_FakeExecutor):
    """Studio 里刚新建、已存库但画布只有 start→end 的流程。"""

    async def execute(
        self, tool_name: str, args: dict[str, Any],
        progress_sink: dict[str, Any] | None = None,
        change_context: Any = None,
    ) -> dict[str, Any]:
        self.calls.append((tool_name, args))
        if tool_name == "get_flow":
            return {
                "flow_id": "flow-blank",
                "name": "新建 RPA 流程",
                "definition": {
                    "nodes": [
                        {"id": "start", "type": "start", "title": "开始"},
                        {"id": "end", "type": "end", "title": "结束"},
                    ],
                    "edges": [{"id": "e1", "source": "start", "target": "end"}],
                },
            }
        return {"status": "ok"}


async def test_blank_open_flow_still_requires_page_evidence(monkeypatch) -> None:
    """回归：在 Studio 空白流程（已有 flow_id）里提"抓取 <url>"，既不算修复也曾不算创建，
    一条引导都注入不到，模型会拿输出边界那句话当兜底回绝用户。"""
    import litellm

    captured_messages: list[list[dict[str, Any]]] = []
    rounds = iter([
        # 第 1 轮：跳过 inspect_page 直接落节点 → 必须被 guard 阻断
        _FakeStream([
            _chunk(tool_calls=[_tool_call_chunk(0, call_id="c1", name="update_flow", arguments='{"flow_id": "flow-blank"}')], finish="tool_calls"),
        ]),
        _FakeStream([_chunk(content="好的。", finish="stop")]),
    ])

    async def fake_acompletion(**kwargs: Any) -> Any:
        captured_messages.append([dict(m) for m in kwargs["messages"]])
        return next(rounds)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    executor = _BlankFlowExecutor()
    orchestrator = AiOrchestrator(tool_executor=executor)  # type: ignore[arg-type]
    events = [e async for e in orchestrator.stream(
        messages=[{"role": "user", "content": "抓取 https://example.com，工作台页面：核心业务指标模块数据，登录信息已设置变量"}],
        model="test-model",
        flow_id="flow-blank",
    )]

    guidance = [
        m for m in captured_messages[0]
        if m.get("role") == "system" and "强制执行顺序" in str(m.get("content") or "")
    ]
    assert guidance, "空白流程 + 含 URL 的构建需求必须注入 _GUIDANCE_BEFORE_CREATE"
    # 流程已存在，引导与阻断都该指向 update_flow 而不是再建一个流程
    assert "update_flow" in guidance[0]["content"]

    assert ("update_flow", {"flow_id": "flow-blank"}) not in executor.calls, "未 inspect_page 就落节点必须被阻断"
    blocked = [
        e for e in events
        if e["type"] == "tool_result" and e["result"].get("status") == "blocked_by_orchestrator_guard"
    ]
    assert len(blocked) == 1
    assert blocked[0]["result"]["guard_id"] == "page_evidence_required"
    assert blocked[0]["result"]["required_tools"] == ["inspect_page"]
    # 该看哪个页面得直接给出来，否则模型只知道「要看」不知道看哪
    assert blocked[0]["result"]["suggested_args"] == {"url": "https://example.com"}

    # 拦截结果里没有 error 字段，曾被当成写入成功
    assert not [
        m for m in captured_messages[1]
        if m.get("role") == "system" and "变更已写入" in str(m.get("content") or "")
    ]


def test_model_caps_is_the_single_lookup_for_model_differences() -> None:
    """分级/窗口/视觉/缓存原先各扫一遍目录、各写一套兜底，加模型漏一处就是静默降级。"""
    caps = _model_caps("zai/glm-4.6")
    assert caps.tier == "standard"
    assert caps.context_window == 200_000
    assert caps.supports_vision is False          # no_vision
    assert caps.supports_cache_control is False   # 非 Anthropic

    anthropic = _model_caps("claude-sonnet-5")
    assert anthropic.supports_cache_control is True
    assert anthropic.supports_vision is True

    # 目录外的模型（自定义/中转透传）走兜底，而不是抛错或返回空能力
    unknown = _model_caps("某个没登记的模型")
    assert unknown.tier == "standard"
    assert unknown.supports_vision is True
    assert unknown.supports_cache_control is False
    assert _model_caps("anthropic/未登记的新模型").supports_cache_control is True


def test_seed_catalog_holds_together() -> None:
    """目录是模型差异的唯一事实来源，字段缺失/自相矛盾都会静默走错分支。"""
    import json
    from pathlib import Path

    catalog = json.loads(
        (Path(__file__).parent.parent / "config" / "model_catalog.json").read_text(encoding="utf-8")
    )

    ids = [m["id"] for m in catalog]
    assert len(ids) == len(set(ids))
    for m in catalog:
        assert m["context_window"] > 0, m["id"]
        assert m.get("tier") in {"weak", "standard", "strong"}, m["id"]
        assert m.get("provider") and m.get("env_key") and m.get("provider_label"), m["id"]

    # 每个厂商恰好一个推荐项，否则选择器会同时给几个模型打「推荐」
    for provider in {m["provider"] for m in catalog}:
        picks = [m["id"] for m in catalog if m["provider"] == provider and m.get("recommended")]
        assert len(picks) == 1, f"{provider} 的推荐项为 {picks}"

    # 调试用的假模型不该随安装包发出去
    assert not [m for m in catalog if "debug" in m["id"] or "test-model" in m["id"]]


async def test_usage_events_report_rounds_tokens_and_blocked_calls(monkeypatch) -> None:
    """用量只进 logger 时，用户无从判断"再让它试一次"要付多少代价。

    这里同时钉住三件事：累计而非增量、护栏拦下的调用也计数、done 之前必有一条终值。
    """
    import litellm

    def _usage_chunk(prompt: int, completion: int, cached: int) -> Any:
        usage = SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        )
        return SimpleNamespace(choices=[], usage=usage)

    rounds = iter([
        _FakeStream([
            _chunk(tool_calls=[_tool_call_chunk(0, call_id="c1", name="list_node_types", arguments='{"category": "browser"}')]),
            # 没先 inspect_page 就建流程会被 DISCOVER 阶段拦掉，
            # 被拦的调用不该从计数里消失——它同样烧了一轮
            _chunk(tool_calls=[_tool_call_chunk(1, call_id="c2", name="create_flow", arguments='{"name": "x"}')],
                   finish="tool_calls"),
            _usage_chunk(1000, 200, 800),
        ]),
        _FakeStream([_chunk(content="检查完了。", finish="stop"), _usage_chunk(1500, 100, 1400)]),
    ])

    async def fake_acompletion(**kwargs: Any) -> Any:
        return next(rounds)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    orchestrator = AiOrchestrator(tool_executor=_FakeExecutor())  # type: ignore[arg-type]
    events = [e async for e in orchestrator.stream(
        messages=[{"role": "user", "content": "帮我建个流程抓 https://a.test 的表格"}], model="test-model",
    )]

    usages = [e["usage"] for e in events if e["type"] == "usage"]
    assert usages, "每轮都该推一次用量"
    assert usages[0]["rounds"] == 1 and usages[0]["prompt_tokens"] == 1000
    final = usages[-1]
    assert final["rounds"] == 2
    assert final["prompt_tokens"] == 2500 and final["completion_tokens"] == 300
    assert final["cached_tokens"] == 2200
    assert final["total_tokens"] == 2800
    assert final["tool_calls"] == 2
    assert final["blocked_calls"] == 1
    assert final["max_rounds"] > 0
    assert events[-1] == {"type": "done"}
    assert events[-2]["type"] == "usage", "终值必须在 done 之前发出，否则最后一次计数丢失"
    first_tool_result = next(i for i, event in enumerate(events) if event["type"] == "tool_result")
    assert events[first_tool_result + 1]["type"] == "usage"
    assert events[first_tool_result + 1]["usage"]["tool_calls"] == 1


def test_a_clean_write_unblocks_the_run_within_the_same_round() -> None:
    """写入返回里的 lint 针对写入之后那一版，比状态块新，闸门必须据它更新。

    否则模型改完想在同一轮接着跑，会被一份已经修好的问题清单拦住——下一轮的状态块要等
    它下一次说话才到。这跟「不许凭空宣布干净」不矛盾：判据是写入自己重跑出来的结论。
    """
    state = _ready(
        blocking_diagnostics=[{"severity": "error", "issue": "single_navigation_node"}],
        runtime_escape_findings=[],
    )
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is not None

    _orchestrator_guard_after_tool(
        "update_flow", {"status": "updated", "revision": 2, "lint_clean": True}, state
    )
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is None

    # 写入之后仍有阻断项：闸门换成新那一份，不是清空
    _orchestrator_guard_after_tool("update_flow", {
        "status": "updated", "revision": 3,
        "lint_findings": [{"severity": "error", "issue": "table_extract_selector_targets_container"}],
    }, state)
    blocked = _orchestrator_guard_before_tool("run_flow", {}, state)
    assert blocked is not None
    assert blocked["lint_findings"][0]["issue"] == "table_extract_selector_targets_container"

    # 压根不跑 lint 的写入工具不能顺手把闸门抹掉：它的返回里两个信号都没有
    _orchestrator_guard_after_tool(
        "set_acceptance_contract", {"status": "applied", "revision": 4}, state
    )
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is not None


def test_runtime_variable_escape_is_booked_separately_from_static_diagnostics() -> None:
    """静态诊断每轮由状态块重算，运行期逃逸重算不出来——它的前提正是静态扫描漏了它。

    写进 blocking_diagnostics 会被下一轮的重算直接冲掉，于是「运行期报出未定义变量」
    这件事只挡得住一轮，第二轮 run_flow 就放行了。
    """
    from app.services.ai_orchestrator import _orchestrator_guard_after_tool

    state = _ready()
    _orchestrator_guard_after_tool(
        "run_flow", {"status": "error", "error": "节点 n3 执行失败：变量未定义: order_no"}, state
    )
    escapes = state["runtime_escape_findings"]
    assert len(escapes) == 1
    assert escapes[0]["issue"] == "undefined_variable_ref_runtime_escape"
    assert escapes[0]["escaped_variable"] == "order_no"
    assert escapes[0]["severity"] == "error"

    # 只有真实结构修复才解除——再跑一次、再读一次状态都不算
    _orchestrator_guard_after_tool("run_flow", {"status": "error", "error": "boom"}, state)
    assert state["runtime_escape_findings"]
    _orchestrator_guard_after_tool("apply_node_fix", {"status": "ok"}, state)
    assert state["runtime_escape_findings"] == []


def test_a_selector_failure_at_runtime_forces_dom_evidence_even_if_lint_is_clean() -> None:
    """静态诊断里没有 selector 问题，不等于这次修复不需要看 DOM。

    旧设计用 lint_flow 的返回决定要不要看页面，而 lint 往往先于 get_run_error 到达：
    lint 干净就把证据位置真，之后运行错误报出 selector 失败也已经放行了。
    """
    from app.services.ai_orchestrator import _orchestrator_guard_after_tool

    state = _ready()
    _orchestrator_guard_after_tool(
        "get_run_error",
        {"inspect_hint": True, "failed_node_id": "n2", "last_browser_url": "https://x.test/"},
        state,
    )
    assert state["page_evidence_required"]["url"] == "https://x.test/"
    assert state["page_evidence_done"] is False
    # 证据没到手之前，改节点和重跑都得挡住
    assert _orchestrator_guard_before_tool("apply_node_fix", {}, state) is not None
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is not None

    _orchestrator_guard_after_tool(
        "inspect_page", {"requested_url": "https://x.test/", "page_layout": []}, state
    )
    assert state["page_evidence_done"] is True
    assert _orchestrator_guard_before_tool("apply_node_fix", {}, state) is None


def test_a_failed_inspection_does_not_count_as_page_evidence() -> None:
    """探测失败仍然置位就等于「看过了」，模型接着盲改 selector。"""
    from app.services.ai_orchestrator import _orchestrator_guard_after_tool

    state = _ready(
        page_evidence_required={"reason": "repair_touches_page_elements"},
        page_evidence_done=False,
    )
    _orchestrator_guard_after_tool("inspect_page", {"error": "页面打不开"}, state)
    assert state["page_evidence_done"] is False


def test_client_rejection_is_not_reported_as_a_bad_api_key() -> None:
    """裸子串匹配把两种毛病归成了一类：_AUTH_ERROR_HINTS 里的 "unauthorized" 吃掉了中转的
    unauthorized client detected，于是「中转拒了这个调用方」显示成「API Key 无效或已过期」。
    密钥是好的、模型也在中转上，用户照提示重填密钥永远修不好——同一把密钥换模型照样被拒。
    这条挂在判据顺序上：客户端被拒必须先于鉴权判，谁往关键词表里再加词都不能让它回到鉴权分支。
    """
    from app.services.ai_orchestrator import _clean_litellm_error

    raw = ("unauthorized client detected, contact support for assistance "
           "at https://discord.gg/aYq5B4RW3")
    cleaned = _clean_litellm_error(raw)

    assert "无效或已过期" not in cleaned, "客户端被拒被归进了鉴权类"
    # 上游原话带着申诉入口，换成自己的措辞等于把用户的出路删掉
    assert "discord.gg" in cleaned

    # 真正的鉴权失败仍然要翻译，别为了修上面那条把整条分支废掉
    assert "无效或已过期" in _clean_litellm_error("Invalid API key provided")
