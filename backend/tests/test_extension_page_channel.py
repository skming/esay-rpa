"""扩展探索通道的协议性质：观察版本、ref 归属、定位裁决、动作链路与能力边界。

这些性质不能只在真实浏览器里验：真实页面上「点了但没生效」和「ref 指到了另一个元素」
都不报错，看起来一样成功。所以这里用一个按真实信封语义应答的假 bridge，把每条性质
钉成断言——包含它必须拒绝的那些输入。

为什么不能省掉版本与归属：漏掉版本，模型会拿上一次观察的编号去操作重渲染后的 DOM，
点到同一位置的另一行数据；漏掉归属，第二轮对话会在第一轮打开的页面上动作并顺手关掉它。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.ai_tools import extension_page_channel as channel_mod
from app.services.ai_tools import page_session
from app.services.ai_tools.extension_page_channel import (
    CHANNEL_OWNER,
    ExtensionCapabilityError,
    open_channel,
)
from app.services.ai_tools.page_observation import annotate_observation, describe_action_effect
from app.services.extension_executor import ExtensionBusyError, ExtensionExecutor


class FakeBridge:
    """按真实 ExtensionBridgeService.execute 的语义应答：{ok:false} 变成 RuntimeError。

    错误类型必须一致：通道靠 RuntimeError 判「这个 ref 不能用了」，而 ConnectionError /
    TimeoutError 是 OSError，不会被误当成 ref 失效。
    """

    def __init__(self, responses: dict[str, list[Any]] | None = None) -> None:
        self.is_connected = True
        self.calls: list[dict] = []
        self._responses = {key: list(value) for key, value in (responses or {}).items()}

    async def execute(self, action: dict, timeout: float = 30.0) -> Any:
        self.calls.append(action)
        queue = self._responses.get(str(action.get("type")))
        if not queue:
            return {"tab_id": 7} if action["type"] == "page.begin" else {}
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if action["type"] == "page.observe" and isinstance(reply, dict):
            return {"document_id": "doc-1", **reply}
        return reply

    def page_calls(self) -> list[dict]:
        return [c for c in self.calls if not str(c.get("type", "")).startswith("automation.group.") and c["type"] not in ("page.begin", "page.end")]


@pytest.fixture(autouse=True)
def _reset_channel():
    channel_mod._current = None
    yield
    if channel_mod._current is not None and channel_mod._current._idle_handle is not None:
        channel_mod._current._idle_handle.cancel()
    channel_mod._current = None


async def open_with(bridge: FakeBridge, *, token: str = "turn-1"):
    executor = ExtensionExecutor(bridge)  # type: ignore[arg-type]
    return executor, await open_channel(executor, token=token)


async def test_observe_bumps_version_and_stamps_it_on_the_result() -> None:
    """版本号必须随每次观察递增并回到结果里：它是内容脚本作废旧 ref 表的唯一依据。"""
    bridge = FakeBridge(responses={"page.observe": [{"url": "https://a/1"}, {"url": "https://a/2"}]})
    _, ch = await open_with(bridge)

    first = await ch.observe(None)
    second = await ch.observe(".panel")

    assert (first["observation_version"], second["observation_version"]) == (1, 2)
    sent = [c for c in bridge.page_calls() if c["type"] == "page.observe"]
    assert [c["observationVersion"] for c in sent] == [1, 2]
    assert [c["scope"] for c in sent] == [None, ".panel"]
    # url 变了要能被调用方看见：用户切了标签页，旧 ref 就指向另一个页面的元素
    assert ch.last_url == "https://a/2"


async def test_ref_from_an_earlier_observation_is_refused_without_touching_the_page() -> None:
    bridge = FakeBridge(responses={"page.observe": [{}, {}]})
    _, ch = await open_with(bridge)
    await ch.observe(None)
    await ch.observe(None)

    ref, error = await ch.resolve_target("e3", None, 1)

    assert ref is None
    assert error == {
        "status": "stale_element_ref",
        "error": "元素引用属于第 1 次观察，当前会话是第 2 次，元素可能已经换了位置",
        "required_action": "call_inspect_page_again",
    }
    # 过期编号不许发到页面：发过去内容脚本自己也会拒，但那多一次往返，且真按 selector
    # 兜底解析就会点到同位置的另一个元素
    assert [c["type"] for c in bridge.page_calls()] == ["page.observe", "page.observe"]


async def test_content_script_ref_failure_becomes_stale_ref_not_a_crash() -> None:
    """导航后内容脚本重建、ref 表消失，报的是 RuntimeError；对模型只有一个结论：重新观察。"""
    bridge = FakeBridge(
        responses={
            "page.observe": [{}],
            "page.resolveTarget": [RuntimeError("ref 已失效或不存在: e2，请重新 query/find 生成快照")],
        }
    )
    _, ch = await open_with(bridge)
    await ch.observe(None)

    ref, error = await ch.resolve_target("e2", None, 1)

    assert ref is None
    assert error is not None and error["status"] == "stale_element_ref"
    assert error["required_action"] == "call_inspect_page_again"


async def test_selector_hitting_several_elements_is_refused_instead_of_taking_the_first() -> None:
    bridge = FakeBridge(responses={"page.observe": [{}], "page.resolveTarget": [{"matches": 3}]})
    _, ch = await open_with(bridge)
    await ch.observe(None)

    ref, error = await ch.resolve_target(None, "button:has-text('查询')", None)

    assert ref is None
    assert error is not None and error["status"] == "ambiguous_selector"
    assert error["matches"] == 3
    assert error["required_action"] == "narrow_selector_or_use_element_ref"


async def test_selector_hitting_nothing_is_element_not_found() -> None:
    bridge = FakeBridge(responses={"page.observe": [{}], "page.resolveTarget": [{"matches": 0}]})
    _, ch = await open_with(bridge)
    await ch.observe(None)

    ref, error = await ch.resolve_target(None, "#nope", None)

    assert ref is None
    assert error is not None and error["status"] == "element_not_found"


async def test_unique_selector_returns_a_temp_ref_so_the_action_hits_what_was_checked() -> None:
    bridge = FakeBridge(
        responses={"page.observe": [{}], "page.resolveTarget": [{"matches": 1, "element_ref": "t1"}]}
    )
    _, ch = await open_with(bridge)
    await ch.observe(None)

    ref, error = await ch.resolve_target(None, "#start", None)

    assert (ref, error) == ("t1", None)


@pytest.mark.parametrize(
    ("action", "expected_type"),
    [
        ("click", "browser.click"),
        ("fill", "browser.fill"),
        ("select_option", "browser.select"),
        ("press", "browser.press"),
        ("hover", "browser.hover"),
        ("scroll", "browser.scroll"),
    ],
)
async def test_actions_go_out_on_the_same_message_chain_the_run_uses(action: str, expected_type: str) -> None:
    """探索与执行必须共用一条链路：分成两条，探索时点得动、运行时点不动就再也测不出来。"""
    bridge = FakeBridge(responses={"page.observe": [{}]})
    _, ch = await open_with(bridge)
    await ch.observe(None)

    await ch.apply_action(action, "e5", None, "2026-01-01")

    sent = bridge.page_calls()[-1]
    assert sent["type"] == expected_type
    assert sent["ref"] == "e5"
    # 动作也带版本号：观察到动作之间页面重渲染过，内容脚本要能拒掉，而不是打在新 DOM 上
    assert sent["observationVersion"] == 1


async def test_scroll_carries_a_distance_not_an_input_value() -> None:
    bridge = FakeBridge(responses={"page.observe": [{}]})
    _, ch = await open_with(bridge)
    await ch.observe(None)

    await ch.apply_action("scroll", None, None, "-400")

    sent = bridge.page_calls()[-1]
    assert sent["distance"] == -400
    assert "inputValue" not in sent


async def test_unsupported_action_fails_loudly_instead_of_silently_doing_something_else() -> None:
    bridge = FakeBridge(responses={"page.observe": [{}]})
    _, ch = await open_with(bridge)
    await ch.observe(None)

    with pytest.raises(ExtensionCapabilityError):
        await ch.apply_action("drag", "e1", None, None)


async def test_unreadable_target_state_becomes_unknown_not_a_failed_action() -> None:
    """回读不到状态只是这一步没有证据。报成失败，模型会去换一个本来正确的目标。"""
    bridge = FakeBridge(
        responses={"page.observe": [{}], "page.targetState": [RuntimeError("未找到元素: #x")]}
    )
    _, ch = await open_with(bridge)
    await ch.observe(None)

    state = await ch.target_state("e1", None)
    effect = describe_action_effect("click", None, {}, state)

    assert state == {}
    assert effect["status"] == "unknown"


async def test_capability_report_states_what_this_channel_cannot_do() -> None:
    """能力差异必须报出来。静默降级的结果是模型拿「另一个浏览器里的页面」当证据改流程。"""
    bridge = FakeBridge()
    _, ch = await open_with(bridge)

    report = ch.capability_report()

    assert report["channel"] == "extension"
    assert report["frame_targeted_observation"] is False
    assert report["closed_shadow_dom"] is False
    assert any("frame_selector" in note for note in report["notes"])


async def test_exploration_holds_the_extension_lease_so_a_run_sees_it_is_busy() -> None:
    """用户在助手探索期间点运行，看到的是「被占用」，而不是两边同时操作同一个窗口。"""
    bridge = FakeBridge()
    executor, _ = await open_with(bridge)

    with pytest.raises(ExtensionBusyError) as exc:
        await executor.create_context(owner="运行 · 日报")

    assert exc.value.holder == CHANNEL_OWNER
    assert executor.lease_holder == CHANNEL_OWNER


async def test_closing_the_channel_releases_the_lease_for_the_next_run() -> None:
    bridge = FakeBridge()
    executor, _ = await open_with(bridge)

    assert await channel_mod.close_current("turn_end", token="turn-1") == "turn_end"
    assert executor.lease_holder is None
    context = await executor.create_context(owner="运行 · 日报")
    assert context.owner == "运行 · 日报"


async def test_another_turn_can_neither_see_nor_close_this_turn_s_channel() -> None:
    bridge = FakeBridge()
    executor, mine = await open_with(bridge, token="turn-1")

    assert channel_mod.get_channel("turn-2") is None
    assert channel_mod.foreign_channel("turn-2") is mine
    assert await channel_mod.close_current("turn_end", token="turn-2") == "not_owner"
    with pytest.raises(page_session.SessionOwnershipError):
        await open_channel(executor, token="turn-2")
    assert not mine.closed


async def test_reopening_within_the_same_turn_reuses_the_channel_and_its_version() -> None:
    bridge = FakeBridge(responses={"page.observe": [{}]})
    executor, first = await open_with(bridge, token="turn-1")
    await first.observe(None)

    second = await open_channel(executor, token="turn-1")

    assert second is first
    assert second.version == 1


async def test_a_closed_channel_refuses_further_actions() -> None:
    bridge = FakeBridge()
    _, ch = await open_with(bridge)
    await channel_mod.close_current("turn_end", token="turn-1")

    with pytest.raises(channel_mod.ExtensionChannelUnavailable):
        await ch.observe(None)


async def test_both_channels_turn_the_same_probe_payload_into_the_same_observation() -> None:
    """同一份探测返回，两条通道必须给出同一份告警和同一份控件配方。

    加工各写一份就会漂移，而模型会按看到的那份改流程拓扑，两边都自称读的是真实 DOM。
    """
    payload = {
        "url": "https://example.test/list",
        "page_classes": ["app"],
        "all_classes": ["app", "el-date-editor"],
        "inputs": [
            {
                "selector": "#start",
                "placeholder": "开始日期",
                "classes": ["el-input__inner"],
                "ancestors": [{"uid": 1, "selector": "#range", "classes": ["el-date-editor"]}],
                "containers": [{"uid": 1, "selector": "#range"}],
            }
        ],
        "buttons": [{"selector": "#go", "text": "查询"}],
        "links": [],
        "tables": [],
    }
    from_playwright = {**payload, "inputs": [dict(payload["inputs"][0])], "all_classes": list(payload["all_classes"])}
    from_extension = {**payload, "inputs": [dict(payload["inputs"][0])], "all_classes": list(payload["all_classes"])}

    annotate_observation(from_playwright)
    annotate_observation(from_extension)

    assert from_playwright == from_extension
    assert from_playwright["spa_loading"] is False
    assert "warning" not in from_playwright
    # 服务端专用字段两边都得摘掉：留着模型会照抄祖先类名去拼选择器
    assert "ancestors" not in from_playwright["inputs"][0]


async def test_loading_page_warns_before_it_is_judged_empty() -> None:
    result = {"page_classes": ["el-loading-mask"], "all_classes": ["el-loading-mask"], "inputs": [], "buttons": []}

    assert annotate_observation(result) is True
    assert "当前观察可能尚不完整" in result["warning"]
    assert "先添加第二个" not in result["warning"]


async def test_begin_failure_releases_lease() -> None:
    bridge = FakeBridge({"page.begin": [RuntimeError("active tab is not injectable")]})
    executor = ExtensionExecutor(bridge)
    with pytest.raises(RuntimeError, match="not injectable"):
        await open_channel(executor)
    assert executor.lease_holder is None
    assert channel_mod.get_channel() is None


async def test_simultaneous_owners_cannot_share_lease() -> None:
    import asyncio
    bridge = FakeBridge()
    executor = ExtensionExecutor(bridge)
    results = await asyncio.gather(open_channel(executor, token="a"), open_channel(executor, token="b"), return_exceptions=True)
    assert sum(isinstance(result, page_session.SessionOwnershipError) for result in results) == 1
    assert sum(call["type"] == "page.begin" for call in bridge.calls) == 1


async def test_tab_and_document_identity_accompany_actions() -> None:
    bridge = FakeBridge({"page.observe": [{"document_id": "doc-a"}, {"document_id": "doc-b"}]})
    _, ch = await open_with(bridge)
    await ch.observe(None)
    await ch.apply_action("click", "e1", None, None)
    sent = bridge.calls[-1]
    assert sent["explorationTabId"] == 7
    assert sent["documentId"] == "doc-a"
    await ch.observe(None)
    assert "documentId" not in bridge.calls[-1]
    assert ch.document_id == "doc-b"


async def test_close_releases_lease_even_if_tab_disappeared() -> None:
    bridge = FakeBridge({"page.end": [RuntimeError("tab closed")]})
    executor, ch = await open_with(bridge)
    await channel_mod.close_current(token="turn-1")
    assert executor.lease_holder is None
    assert ch.closed


async def test_idle_channel_releases_lease(monkeypatch) -> None:
    import asyncio
    monkeypatch.setattr(page_session, "IDLE_TTL_SECONDS", 0.01)
    executor, ch = await open_with(FakeBridge())
    await asyncio.sleep(0.04)
    assert ch.closed
    assert executor.lease_holder is None


def tool_executor(bridge):
    from types import SimpleNamespace
    from app.services.ai_tools.executor import RpaToolExecutor
    ext = ExtensionExecutor(bridge)
    manager = SimpleNamespace(is_extension_enabled=lambda: True, is_extension_connected=lambda: bridge.is_connected,
                              extension_exploration_executor=lambda: ext)
    return RpaToolExecutor(None, manager), manager


async def test_real_tool_dispatch_reuses_extension_and_reports_action_effect(monkeypatch) -> None:
    from app.services.ai_tools.executor import RpaToolExecutor
    async def wrong_channel(*args, **kwargs):
        pytest.fail("extension observation opened Playwright")
    monkeypatch.setattr(RpaToolExecutor, "_inspect_page_via_browser", wrong_channel)
    bridge = FakeBridge({"page.observe": [{"url": "https://logged-in.test/"}, {"url": "https://logged-in.test/"}],
                         "page.resolveTarget": [{"matches": 1}],
                         "page.targetState": [{"tag": "input", "value": "old"}, {"tag": "input", "value": "new"}],
                         "page.effectSignature": [{"url": "https://logged-in.test/"}, {"url": "https://logged-in.test/"}]})
    executor, _ = tool_executor(bridge)
    observed = await executor.execute("inspect_page", {"browser_executor": "extension"})
    assert observed["inspection_source"] == "extension"
    assert observed["session"]["tab_id"] == 7
    acted = await executor.execute("interact_page", {"action": "fill", "element_ref": "e0", "value": "new", "observation_version": 1, "wait_ms": 0})
    assert acted["action_effect"]["status"] == "target_reached"
    assert acted["input_value_after"] == "new"
    assert acted["observation"]["observation_version"] == 2
    assert all(call["explorationTabId"] == 7 for call in bridge.page_calls())


async def test_extension_disabled_mid_session_prevents_interaction() -> None:
    bridge = FakeBridge({"page.observe": [{}]})
    executor, manager = tool_executor(bridge)
    await executor.execute("inspect_page", {"browser_executor": "extension"})
    manager.is_extension_enabled = lambda: False
    before = len(bridge.calls)
    result = await executor.execute("interact_page", {"action": "click", "element_ref": "e0"})
    assert result["status"] == "blocked_extension_disabled"
    assert len(bridge.calls) == before


@pytest.mark.parametrize("args", [{"frame_selector": "iframe"}, {"tab_index": 1}])
async def test_extension_unsupported_requests_do_not_fallback(args) -> None:
    """frame / tab 是扩展真做不到的（只绑一个标签页的主文档）。url 不在此列——
    它由 page.begin 复用已开着的目标页或新开一个，见下面两条。"""
    bridge = FakeBridge()
    executor, _ = tool_executor(bridge)
    result = await executor.execute("inspect_page", {"browser_executor": "extension", **args})
    assert result["status"] == "unsupported_capability"
    assert bridge.calls == []


async def test_inspect_with_url_sends_target_to_begin_and_reports_requested_url() -> None:
    """带 url 的观察必须走 page.begin 的 targetUrl：扩展那边据它复用已登录的目标标签页，
    换成后端自己发起请求就丢了登录态，抓回的 HTML 与模型正在看的页面无关。"""
    bridge = FakeBridge({
        "page.begin": [{"tab_id": 9, "requested_url": "https://example.com/#/workbench",
                        "url": "https://example.com/#/workbench", "reused": True}],
        "page.observe": [{"url": "https://example.com/#/workbench", "tables": [{"row_count": 3}]}],
    })
    executor, _ = tool_executor(bridge)

    result = await executor.execute(
        "inspect_page", {"browser_executor": "extension", "url": "https://example.com/#/workbench"}
    )

    begin = [c for c in bridge.calls if c["type"] == "page.begin"]
    assert [c.get("targetUrl") for c in begin] == ["https://example.com/#/workbench"]
    assert result["requested_url"] == "https://example.com/#/workbench"
    assert result["session"]["tab_id"] == 9
    assert result["page_outcome"] == "target_content_ready"


async def test_inspect_with_new_url_rebinds_the_session_tab() -> None:
    """同一轮里换目标页要重发 page.begin：绑定可能因此换到另一个标签页，
    tab_id 不跟着更新的话，后续动作会发到上一个页面上。"""
    bridge = FakeBridge({
        "page.begin": [{"tab_id": 9}, {"tab_id": 11}],
        "page.observe": [{"url": "https://a.test/one", "tables": [{}]},
                         {"url": "https://b.test/two", "tables": [{}]}],
    })
    executor, _ = tool_executor(bridge)

    first = await executor.execute("inspect_page", {"browser_executor": "extension", "url": "https://a.test/one"})
    second = await executor.execute("inspect_page", {"browser_executor": "extension", "url": "https://b.test/two"})

    assert sum(c["type"] == "page.begin" for c in bridge.calls) == 2
    assert (first["session"]["tab_id"], second["session"]["tab_id"]) == (9, 11)


async def test_inspect_requests_page_html_but_interact_does_not() -> None:
    """整页 HTML 只在 inspect 时取：交互后那次观察走同一个动作，每次都搬一份整页 HTML
    过 WebSocket，而那份正文绝大多数时候没人读。少取一次就少一次白搬。"""
    bridge = FakeBridge({
        "page.observe": [{"url": "https://example.com/list", "tables": [{}]},
                          {"url": "https://example.com/list", "tables": [{}]}],
        "page.resolveTarget": [{"matches": 1, "element_ref": "t1"}],
    })
    executor, _ = tool_executor(bridge)

    await executor.execute("inspect_page", {"browser_executor": "extension", "url": "https://example.com/list"})
    await executor.execute("interact_page", {"action": "click", "selector": "#go",
                                             "observation_version": 1})

    observes = [c for c in bridge.calls if c["type"] == "page.observe"]
    assert [c.get("includeHtml") for c in observes] == [True, False]


_LOGGED_IN_HTML = """
<html><body>
  <script>var token = 'rc-secret';</script>
  <form><input type="password" value="hunter2"></form>
  <table class="custom-table"><thead><tr><th>编号</th><th>名称</th></tr></thead>
    <tbody><tr><td>A-1</td><td>甲</td></tr><tr><td>A-2</td><td>乙</td></tr></tbody></table>
  <p>备注正文</p>
</body></html>
"""


async def test_logged_in_html_becomes_trimmed_text_and_never_reaches_the_model() -> None:
    """登录态 DOM 就地换成精简正文：原始 HTML 不出现在结果里，script 与 password 的 value
    也不出现。这两样是探测脱敏的下界，服务端这一层不能把它们又带回来。"""
    bridge = FakeBridge({
        "page.observe": [{"url": "https://example.com/list",
                          "tables": [{"row_selector": "tbody > tr", "row_count": 2}],
                          "page_html": _LOGGED_IN_HTML}],
    })
    executor, _ = tool_executor(bridge)

    result = await executor.execute("inspect_page", {"browser_executor": "extension", "url": "https://example.com/list"})

    assert "page_html" not in result
    assert "hunter2" not in repr(result) and "rc-secret" not in repr(result)
    assert "A-1" in result["page_text_sample"] and "备注正文" in result["page_text_sample"]
    # 结构证据是这一轮观察写的，不能被正文快照的副本盖回旧值
    assert result["tables"] == [{"row_selector": "tbody > tr", "row_count": 2}]
    assert result["page_outcome"] == "target_content_ready"


async def test_trimming_failure_keeps_structure_evidence_and_offers_a_reread() -> None:
    """正文取不到不是观察失败：探测能穿开放 shadow root、lxml 的 cssselect 不能，
    同一个 scope_selector 在两边可以一个命中一个不命中。这时结构证据照样成立，
    失败原因要如实写出来，并给回 snapshot_id 让调用方换个范围补读同一份快照。"""
    bridge = FakeBridge({
        "page.observe": [{"url": "https://example.com/list", "tables": [{"row_selector": "tbody > tr"}],
                          "page_html": _LOGGED_IN_HTML}],
    })
    executor, _ = tool_executor(bridge)

    result = await executor.execute("inspect_page", {"browser_executor": "extension",
                                                    "url": "https://example.com/list",
                                                    "scope_selector": ".not-in-serialized-html"})

    assert "page_text_sample" not in result
    assert result["page_content_unavailable"]["reason"]
    assert result["tables"] == [{"row_selector": "tbody > tr"}]
    assert result["snapshot_id"]


async def test_cursor_reread_carries_this_round_warning() -> None:
    """补读回来的正文必须带着本轮的结论：快照拿的就是这份观察对象，补读时原样返回。
    annotate 先跑、或 evidence 不复制，任一条成立即可；两条同时断开，翻页读到的正文
    会不带任何告警——模型于是拿登录表单的 DOM 当目标页结构去改 selector。"""
    bridge = FakeBridge({
        "page.observe": [{"url": "https://example.com/sso/login",
                          "inputs": [{"type": "password"}], "buttons": [], "links": [], "tables": [],
                          "page_html": _LOGGED_IN_HTML}],
    })
    executor, _ = tool_executor(bridge)

    first = await executor.execute("inspect_page", {"browser_executor": "extension", "url": "https://example.com/list"})
    assert first["redirected_to_login"] is True

    again = await executor.execute("inspect_page", {"snapshot_id": first["snapshot_id"]})

    assert again["warning"] == first["warning"]
    assert again["page_outcome"] == "redirected_to_login"


async def test_begin_without_tab_identity_fails_loudly() -> None:
    """扩展没回 tab_id 就必须报错：临时 ref 的归属靠 tab 限定，
    拿 None 当标签页身份会把动作发到用户正在看的任意页面上。"""
    bridge = FakeBridge({"page.begin": [{"url": "https://example.com/"}]})
    executor, _ = tool_executor(bridge)

    result = await executor.execute("inspect_page", {"browser_executor": "extension"})

    assert result["status"] == "extension_observation_failed"
    assert "标签页身份" in result["error"]


async def test_screenshot_uses_bound_extension_document() -> None:
    bridge = FakeBridge({"page.observe": [{}], "browser.screenshot": [{"dataUrl": "data:image/png;base64,aGVsbG8="}]})
    executor, _ = tool_executor(bridge)
    await executor.execute("inspect_page", {"browser_executor": "extension"})
    result = await executor.execute("inspect_screenshot", {})
    assert result["tab_id"] == 7 and result["document_id"] == "doc-1"
    assert result["image_base64"] == "aGVsbG8="
    assert bridge.calls[-1]["documentId"] == "doc-1"


@pytest.mark.parametrize("status", ["satisfied", "timed_out"])
async def test_wait_returns_page_evidence(status):
    _, ch = await open_with(FakeBridge({"page.waitFor": [{"status": status}]}))
    assert await ch.wait_for("#panel") == {"status": status, "selector": "#panel"}


async def test_wait_rejects_missing_evidence():
    _, ch = await open_with(FakeBridge({"page.waitFor": [{}]}))
    with pytest.raises(RuntimeError, match="等待结果"):
        await ch.wait_for("#panel")


async def test_extension_interaction_timeout_still_returns_observation(monkeypatch):
    from app.services.ai_tools.executor import RpaToolExecutor

    bridge = FakeBridge({
        "page.resolveTarget": [{"matches": 1, "element_ref": "e1"}],
        "page.waitFor": [{"status": "timed_out"}],
        "page.observe": [{"url": "https://example.test", "inputs": [], "buttons": []}],
    })
    _, channel = await open_with(bridge)
    monkeypatch.setattr(channel_mod, "get_channel", lambda: channel)
    monkeypatch.setattr(RpaToolExecutor, "_extension_access_error", lambda self: None)
    executor = RpaToolExecutor(flow_service=None, task_manager=None)
    result = await executor._interact_page_via_extension("click", None, "#button", None, None, None, "#panel", 0)
    assert result["status"] == "ok"
    assert result["wait_result"]["status"] == "timed_out"
    assert result["observation"]["inspection_source"] == "extension"
    assert "warning" in result
    assert "business_check" not in result
    assert len([call for call in bridge.calls if call["type"] == "browser.click"]) == 1
