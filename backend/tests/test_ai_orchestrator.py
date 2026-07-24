from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app.services.ai_orchestrator import (
    AiOrchestrator,
    _after_tool_guidance,
    _compact_tool_messages,
    _build_few_shot_block,
    _build_system_message,
    _context_char_budget,
    _model_caps,
    _detect_turn_intents,
    _expand_history_tool_calls,
    _FlowContext,
    _MAX_REPAIR_CYCLES,
    _OLD_SCREENSHOT_PLACEHOLDER,
    _orchestrator_guard_after_tool,
    _orchestrator_guard_before_tool,
    _split_partial_tag_suffix,
    _ThinkTagFilter,
)


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


def test_terminal_guard_block_forces_a_closing_statement() -> None:
    """要用户拿主意的拦截必须逼出收尾正文，否则用户只看到一个空气泡。"""
    for action in ("report_to_user_and_stop", "needs_user_navigation_target"):
        guidance, stop = _after_tool_guidance("run_flow", {
            "status": "blocked_by_orchestrator_guard",
            "required_action": action,
        })
        assert guidance and "不要再调用任何工具" in guidance
        assert stop

    # 只是改道的拦截仍按原样放行，强行收尾会打断本该继续的诊断
    assert _after_tool_guidance("update_flow", {
        "status": "blocked_by_orchestrator_guard",
        "required_action": "call_inspect_page_first",
    }) == (None, False)


def test_waiting_for_the_user_does_not_burn_the_repair_budget() -> None:
    """停下来等人不是一次失败的修复，记进熔断计数等于罚用户操作慢。"""
    state: dict[str, Any] = {}
    for _ in range(_MAX_REPAIR_CYCLES + 2):
        _orchestrator_guard_after_tool("run_flow", {"status": "paused_for_human"}, state)
        _orchestrator_guard_after_tool("run_flow", {"status": "waiting_for_user_input"}, state)
    assert not state.get("repair_cycle_lock")
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is None

    # 真失败照常计数
    for _ in range(_MAX_REPAIR_CYCLES):
        _orchestrator_guard_after_tool("run_flow", {"status": "error", "error": "boom"}, state)
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is not None


def test_failure_budget_lock_still_allows_read_only_diagnosis() -> None:
    """挡掉纯读工具等于没收诊断手段，模型只能在剩下几个工具间空转。"""
    state: dict[str, Any] = {"failure_budget_lock": {"flow_id": "f1"}}
    for tool in ("list_node_types", "get_run_output", "validate_flow", "get_flow"):
        assert _orchestrator_guard_before_tool(tool, {}, state) is None
    # 写入与运行仍然挡住，这才是这道闸的本职
    assert _orchestrator_guard_before_tool("update_flow", {}, state) is not None
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is not None


def test_repeated_failed_runs_lock_further_repair_attempts() -> None:
    state: dict[str, Any] = {}
    failure = {"status": "error", "error": "Page.goto: Timeout 30000ms exceeded"}
    for _ in range(_MAX_REPAIR_CYCLES - 1):
        _orchestrator_guard_after_tool("run_flow", failure, state)
    assert _orchestrator_guard_before_tool("update_flow", {}, state) is None

    _orchestrator_guard_after_tool("run_flow", failure, state)
    blocked = _orchestrator_guard_before_tool("update_flow", {}, state)
    assert blocked and blocked["required_action"] == "report_to_user_and_stop"
    # 写入和再次运行都得拦住，只放诊断类工具
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is not None
    assert _orchestrator_guard_before_tool("get_run_error", {}, state) is None


def test_quality_failures_count_as_repair_cycles() -> None:
    """跑得起来但审计不合格是这类循环的主要形态，只数 run_flow 失败会完全数不到。"""
    state: dict[str, Any] = {}
    audit_failed = {"passed": False, "issues": [{"issue": "mixed_ui_rows", "message": "混入 UI 行"}]}
    for _ in range(_MAX_REPAIR_CYCLES):
        _orchestrator_guard_after_tool("run_flow", {"status": "success"}, state)
        _orchestrator_guard_after_tool("assert_run_output", audit_failed, state)
    assert _orchestrator_guard_before_tool("update_flow", {}, state) is not None


def test_passing_audit_clears_the_repair_cycle_counter() -> None:
    """审计通过才是一轮闭环，之后提新需求不该背着旧的失败计数。"""
    state: dict[str, Any] = {}
    for _ in range(_MAX_REPAIR_CYCLES - 1):
        _orchestrator_guard_after_tool("run_flow", {"status": "error"}, state)
    _orchestrator_guard_after_tool("assert_run_output", {"passed": True}, state)
    _orchestrator_guard_after_tool("run_flow", {"status": "error"}, state)
    assert _orchestrator_guard_before_tool("update_flow", {}, state) is None


def test_create_intent_needs_url_and_is_suppressed_by_repair_intent() -> None:
    blank = _FlowContext(is_blank=True)
    msgs = [{"role": "user", "content": "帮我抓取 https://example.com 的表格"}]
    assert _detect_turn_intents(msgs, "flow-1", blank).create_url == "https://example.com"
    # 没有 URL 就无从 inspect，不激活 gate
    assert _detect_turn_intents([{"role": "user", "content": "帮我建个流程"}], None, blank).create_url is None
    # "抓不全" 是修复而非新建
    repair = _detect_turn_intents(
        [{"role": "user", "content": "https://example.com 抓不全，帮我修一下"}], "flow-1", blank
    )
    assert repair.repair and repair.create_url is None


def test_existing_browser_chain_is_protected_unless_switch_is_explicit() -> None:
    ctx = _FlowContext(browser_chain_node_ids={"n1"})
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
        self, tool_name: str, args: dict[str, Any], progress_sink: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((tool_name, args))
        if tool_name == "create_flow":
            return {"flow_id": "flow-1", "status": "created"}
        return {"status": "ok"}


async def test_parallel_tool_calls_after_create_flow_get_placeholder_responses(monkeypatch) -> None:
    """P0 回归：create_flow 成功后 break 跳过的并行调用必须补 tool 应答，
    且流式返回的空 tool_call id 要合成兜底值——否则严格 OpenAI 兼容端点
    会在下一轮以 400 拒绝整个对话。"""
    import litellm

    captured_messages: list[list[dict[str, Any]]] = []
    rounds = iter([
        # 第 1 轮：并行发出 create_flow + lint_flow，且两个调用都不带 id
        _FakeStream([
            _chunk(tool_calls=[_tool_call_chunk(0, name="create_flow", arguments='{"name": "测试"}')]),
            _chunk(tool_calls=[_tool_call_chunk(1, name="lint_flow", arguments='{"flow_id": "flow-1"}')], finish="tool_calls"),
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

    # lint_flow 没有真正执行（create_flow 后 break）……
    assert [c[0] for c in executor.calls] == ["create_flow"]
    # ……但拿到了 skipped 占位结果事件
    skipped = [e for e in events if e["type"] == "tool_result" and e["tool"] == "lint_flow"]
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
        self, tool_name: str, args: dict[str, Any], progress_sink: dict[str, Any] | None = None
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


async def test_blank_open_flow_still_arms_pre_create_inspect_gate(monkeypatch) -> None:
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
    assert len(blocked) == 1 and blocked[0]["result"]["required_tool"] == "inspect_page"

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
