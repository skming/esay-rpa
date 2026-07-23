from __future__ import annotations

import base64

import pytest

from app.services.extension_executor import ExtensionExecutionContext, ExtensionExecutor
from app.services.runtime_variables import RuntimeVariableStore


class FakeBridge:
    """Records every dispatched action and replays scripted responses per action type,
    standing in for the real WebSocket round-trip to the browser extension."""

    def __init__(self, responses: dict[str, list[dict]] | None = None) -> None:
        self.is_connected = True
        self.calls: list[dict] = []
        self._responses = {key: list(value) for key, value in (responses or {}).items()}

    async def execute(self, action: dict, timeout: float = 30.0) -> dict:
        self.calls.append(action)
        queue = self._responses.get(action["type"])
        if queue:
            return queue.pop(0)
        return {}


async def make_context(bridge: FakeBridge) -> tuple[ExtensionExecutor, ExtensionExecutionContext]:
    executor = ExtensionExecutor(bridge)  # type: ignore[arg-type]
    context = await executor.create_context()
    return executor, context


def action_calls(bridge: FakeBridge) -> list[dict]:
    return [call for call in bridge.calls if not str(call.get("type", "")).startswith("automation.group.")]


async def test_create_context_rejects_when_extension_not_connected() -> None:
    bridge = FakeBridge()
    bridge.is_connected = False
    executor = ExtensionExecutor(bridge)  # type: ignore[arg-type]
    with pytest.raises(ConnectionError):
        await executor.create_context()


async def test_unsupported_action_type_raises_value_error() -> None:
    bridge = FakeBridge()
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})
    with pytest.raises(ValueError, match="暂不支持节点类型"):
        await executor.run({"type": "browser.notARealAction"}, variables, context, timeout_ms=1000)


async def test_max_steps_per_run_stops_runaway_loops() -> None:
    bridge = FakeBridge(responses={"browser.scroll": [{}]})
    executor = ExtensionExecutor(bridge, max_steps_per_run=2)  # type: ignore[arg-type]
    context = await executor.create_context()
    variables = RuntimeVariableStore.from_initial({})

    await executor.run({"type": "browser.scroll"}, variables, context, timeout_ms=1000)
    await executor.run({"type": "browser.scroll"}, variables, context, timeout_ms=1000)
    with pytest.raises(RuntimeError, match="已达上限"):
        await executor.run({"type": "browser.scroll"}, variables, context, timeout_ms=1000)


async def test_browser_check_toggles_and_reports_actual_state() -> None:
    bridge = FakeBridge(responses={"browser.check": [{"checked": True}]})
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {"type": "browser.check", "selector": "#agree", "checked": True}, variables, context, timeout_ms=1000
    )

    assert bridge.calls[-1] == {"type": "browser.check", "selector": "#agree", "checked": True}
    assert result.values == ["true"]


async def test_browser_drag_forwards_source_and_target_selectors() -> None:
    bridge = FakeBridge()
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {"type": "browser.drag", "selector": "#card-1", "targetSelector": "#column-done"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert bridge.calls[-1] == {"type": "browser.drag", "selector": "#card-1", "targetSelector": "#column-done"}
    assert result.detail == "#card-1 -> #column-done"


async def test_browser_ensure_login_returns_status_from_bridge() -> None:
    bridge = FakeBridge(responses={"browser.ensureLogin": [{"status": "login_required"}]})
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {"type": "browser.ensureLogin", "selector": ".avatar", "targetSelector": "#login-form"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert bridge.calls[-1] == {
        "type": "browser.ensureLogin",
        "selector": ".avatar",
        "targetSelector": "#login-form",
    }
    assert result.values == ["login_required"]


async def test_browser_extract_attribute_forwards_mode_and_attribute() -> None:
    bridge = FakeBridge(responses={"browser.extract": [{"values": ["password"]}]})
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {"type": "browser.extract", "selector": "input[type='password']", "extractMode": "attribute", "attribute": "type"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert bridge.calls[-1] == {
        "type": "browser.extract",
        "selector": "input[type='password']",
        "extractMode": "attribute",
        "attribute": "type",
    }
    assert result.values == ["password"]


async def test_browser_extract_count_returns_count_value() -> None:
    bridge = FakeBridge(responses={"browser.extract": [{"values": ["2"], "count": 2}]})
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {"type": "browser.extract", "selector": ".row", "extractMode": "count"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert bridge.calls[-1] == {"type": "browser.extract", "selector": ".row", "extractMode": "count"}
    assert result.values == ["2"]


async def test_browser_extract_table_preserves_structured_rows() -> None:
    rows = [{"姓名": "张三", "金额": "100"}, {"姓名": "李四", "金额": "200"}]
    bridge = FakeBridge(responses={"browser.extract": [{"values": rows, "count": 2}]})
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {"type": "browser.extract", "selector": "table.orders", "extractMode": "table"},
        variables,
        context,
        timeout_ms=1000,
    )

    assert bridge.calls[-1] == {"type": "browser.extract", "selector": "table.orders", "extractMode": "table"}
    assert result.structured == rows
    assert result.values == ['{"姓名": "张三", "金额": "100"}', '{"姓名": "李四", "金额": "200"}']


async def test_browser_dismiss_skips_hidden_or_disabled_candidates() -> None:
    bridge = FakeBridge(
        responses={
            "browser.elementState": [
                {"exists": True, "hidden": True, "disabled": False},  # 第一个候选：隐藏，跳过
                {"exists": True, "hidden": False, "disabled": False},  # 第二个候选：可点
            ]
        }
    )
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {
            "type": "browser.dismiss",
            "selector": ".cookie-banner button\n.newsletter-modal .close",
            "dismissedCountVariable": "dismissed_count",
        },
        variables,
        context,
        timeout_ms=1000,
    )

    click_calls = [call for call in bridge.calls if call["type"] == "browser.click"]
    assert click_calls == [{"type": "browser.click", "selector": ".newsletter-modal .close"}]
    assert result.values == ["1"]
    assert variables.get("dismissed_count") == 1


async def test_browser_click_load_more_stops_when_button_hidden() -> None:
    bridge = FakeBridge(
        responses={
            "browser.extract": [
                {"values": ["a", "b"]},
                {"values": ["a", "b", "c"]},
            ],
            "browser.elementState": [
                {"exists": True, "hidden": False, "disabled": False},
                {"exists": True, "hidden": True, "disabled": False},
            ],
        }
    )
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {
            "type": "browser.clickLoadMore",
            "selector": ".load-more",
            "targetSelector": ".item",
            "maxIterations": 5,
            "delayMs": 0,
            "loadedCountVariable": "loaded_count",
        },
        variables,
        context,
        timeout_ms=1000,
    )

    assert result.values == ["a", "b", "c"]
    assert variables.get("loaded_count") == 3
    click_calls = [call for call in bridge.calls if call["type"] == "browser.click"]
    assert click_calls == [{"type": "browser.click", "selector": ".load-more"}]


async def test_browser_paginate_next_stops_on_repeated_fingerprint() -> None:
    bridge = FakeBridge(
        responses={
            "browser.extract": [
                {"values": ["row1", "row2"]},  # 第一页初次抓取
                {"values": ["row1", "row2"]},  # 点下一页后抓取 → 与点击前相同，判定翻页无效
            ],
            "browser.elementState": [
                {"exists": True, "hidden": False, "disabled": False},
            ],
        }
    )
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {
            "type": "browser.paginateNext",
            "selector": ".next-page",
            "targetSelector": ".row",
            "maxIterations": 10,
            "delayMs": 0,
            "pageCountVariable": "page_count",
        },
        variables,
        context,
        timeout_ms=1000,
    )

    assert result.values == ["row1", "row2"]
    assert variables.get("page_count") == 1
    click_calls = [call for call in bridge.calls if call["type"] == "browser.click"]
    assert click_calls == [{"type": "browser.click", "selector": ".next-page"}]


async def test_browser_paginate_next_preserves_table_rows() -> None:
    rows = [{"序号": "1", "合约编号": "Y001"}, {"序号": "2", "合约编号": "Y002"}]
    bridge = FakeBridge(
        responses={
            "browser.extract": [
                {"values": rows},
            ],
            "browser.elementState": [
                {"exists": True, "hidden": True, "disabled": False},
            ],
        }
    )
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {
            "type": "browser.paginateNext",
            "selector": ".next-page",
            "targetSelector": ".contract-row",
            "extractMode": "table",
            "delayMs": 0,
            "pageCountVariable": "page_count",
        },
        variables,
        context,
        timeout_ms=1000,
    )

    assert bridge.calls[-2] == {"type": "browser.extract", "selector": ".contract-row", "extractMode": "table"}
    assert result.structured == rows
    assert result.values == ['{"序号": "1", "合约编号": "Y001"}', '{"序号": "2", "合约编号": "Y002"}']
    assert variables.get("page_count") == 1


async def test_browser_wait_for_visible_polls_until_element_shows() -> None:
    bridge = FakeBridge(
        responses={
            "browser.elementState": [
                {"exists": False, "hidden": True, "disabled": False},
                {"exists": True, "hidden": False, "disabled": False},
            ],
        }
    )
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {"type": "browser.waitFor", "selector": "#result"},
        variables,
        context,
        timeout_ms=2000,
    )

    assert result.values == ["#result"]
    assert len([c for c in bridge.calls if c["type"] == "browser.elementState"]) == 2


async def test_browser_wait_for_hidden_times_out_when_element_stays_visible() -> None:
    bridge = FakeBridge(
        responses={
            "browser.elementState": [
                {"exists": True, "hidden": False, "disabled": False},
                {"exists": True, "hidden": False, "disabled": False},
            ],
        }
    )
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    with pytest.raises(TimeoutError, match="等待元素消失超时"):
        await executor.run(
            {"type": "browser.waitFor", "selector": ".loading-mask", "waitCondition": "hidden"},
            variables,
            context,
            timeout_ms=100,
        )


async def test_browser_wait_for_text_contains_succeeds_once_text_matches() -> None:
    bridge = FakeBridge(
        responses={
            "browser.extract": [
                {"text": "正在处理..."},
                {"text": "处理完成，共导出 12 条"},
            ],
        }
    )
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {
            "type": "browser.waitFor",
            "selector": "#status",
            "waitCondition": "textContains",
            "inputValue": "处理完成",
        },
        variables,
        context,
        timeout_ms=2000,
    )

    assert result.values == ["#status"]
    assert len([c for c in bridge.calls if c["type"] == "browser.extract"]) == 2


class FailOnceThenHealBridge(FakeBridge):
    """primary selector 上第一次真实动作调用（非 highlight 视觉反馈）抛异常（模拟元素未命中），
    之后按 FakeBridge 正常脚本化响应（用于驱动 _heal_selector 探测备选 candidate）。"""

    def __init__(self, *, fails_selector: str, fails_type: str = "browser.click", responses: dict[str, list[dict]] | None = None) -> None:
        super().__init__(responses)
        self._fails_selector = fails_selector
        self._fails_type = fails_type
        self._has_failed = False

    async def execute(self, action: dict, timeout: float = 30.0) -> dict:
        if not self._has_failed and action.get("type") == self._fails_type and action.get("selector") == self._fails_selector:
            self._has_failed = True
            self.calls.append(action)
            raise RuntimeError(f"selector 未命中: {self._fails_selector}")
        return await super().execute(action, timeout)


async def test_click_heals_via_fallback_selector_when_primary_missing() -> None:
    bridge = FailOnceThenHealBridge(
        fails_selector="#gone",
        responses={
            "browser.elementState": [
                {"exists": True, "hidden": False, "disabled": False},  # 备选 selector：可用
            ],
        },
    )
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {
            "type": "browser.click",
            "selector": "#gone",
            "fallbackSelectors": "#backup-button",
        },
        variables,
        context,
        timeout_ms=1000,
    )

    assert "selector 自愈" in result.detail
    assert "#backup-button" in result.detail
    click_calls = [call for call in bridge.calls if call["type"] == "browser.click"]
    assert click_calls == [
        {"type": "browser.click", "selector": "#gone", "trusted": False},
        {"type": "browser.click", "selector": "#backup-button", "trusted": False},
    ]


async def test_click_heals_via_anchor_text_when_primary_missing() -> None:
    bridge = FailOnceThenHealBridge(
        fails_selector="#gone",
        responses={
            "browser.elementState": [
                {"exists": True, "hidden": False, "disabled": False},  # role=button 候选命中
            ],
        },
    )
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run(
        {
            "type": "browser.click",
            "selector": "#gone",
            "anchorText": "提交",
        },
        variables,
        context,
        timeout_ms=1000,
    )

    assert "selector 自愈" in result.detail
    click_calls = [call for call in bridge.calls if call["type"] == "browser.click"]
    assert click_calls[0] == {"type": "browser.click", "selector": "#gone", "trusted": False}
    assert click_calls[1] == {"type": "browser.click", "selector": 'role=button[name="提交"]', "trusted": False}


async def test_non_healable_action_propagates_original_exception() -> None:
    bridge = FailOnceThenHealBridge(fails_selector="#gone")
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    with pytest.raises(ValueError, match="缺少 targetUrl"):
        await executor.run(
            {"type": "browser.open", "selector": "#gone", "fallbackSelectors": "#backup"},
            variables,
            context,
            timeout_ms=1000,
        )

    assert not any(call["type"] == "browser.elementState" for call in action_calls(bridge))
    assert action_calls(bridge) == []


async def test_healing_gives_up_when_no_candidate_resolves() -> None:
    bridge = FailOnceThenHealBridge(
        fails_selector="#gone",
        responses={
            "browser.elementState": [
                {"exists": False, "hidden": True, "disabled": False},
            ],
        },
    )
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    with pytest.raises(RuntimeError, match="selector 未命中"):
        await executor.run(
            {
                "type": "browser.click",
                "selector": "#gone",
                "fallbackSelectors": "#also-gone",
            },
            variables,
            context,
            timeout_ms=1000,
        )


async def test_browser_screenshot_node_type_is_supported_as_diagnostic_noop() -> None:
    bridge = FakeBridge()
    executor, context = await make_context(bridge)
    variables = RuntimeVariableStore.from_initial({})

    result = await executor.run({"type": "browser.screenshot"}, variables, context, timeout_ms=1000)

    assert result.action_type == "browser.screenshot"
    assert action_calls(bridge) == []


async def test_screenshot_throttles_successive_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    encoded = base64.b64encode(b"png-bytes").decode()
    bridge = FakeBridge(
        responses={"browser.screenshot": [{"dataUrl": f"data:image/png;base64,{encoded}"}] * 2}
    )
    executor, context = await make_context(bridge)

    clock = {"now": 0.0}
    monkeypatch.setattr("app.services.extension_executor.time.monotonic", lambda: clock["now"])
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr("app.services.extension_executor.asyncio.sleep", fake_sleep)

    await executor.screenshot(context)
    clock["now"] += 0.2
    await executor.screenshot(context)

    assert sleeps == [pytest.approx(0.8)]


async def test_screenshot_does_not_throttle_when_interval_already_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = base64.b64encode(b"png-bytes").decode()
    bridge = FakeBridge(
        responses={"browser.screenshot": [{"dataUrl": f"data:image/png;base64,{encoded}"}] * 2}
    )
    executor, context = await make_context(bridge)

    clock = {"now": 0.0}
    monkeypatch.setattr("app.services.extension_executor.time.monotonic", lambda: clock["now"])

    async def fake_sleep(seconds: float) -> None:
        raise AssertionError("不应该节流：距离上次截图已超过最小间隔")

    monkeypatch.setattr("app.services.extension_executor.asyncio.sleep", fake_sleep)

    await executor.screenshot(context)
    clock["now"] += 2.0
    await executor.screenshot(context)
