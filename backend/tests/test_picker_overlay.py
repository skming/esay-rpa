"""共享 picker 定位与注入表达式的真实浏览器回归。"""
from __future__ import annotations

import asyncio
from importlib.util import find_spec
from typing import Any

import pytest

from app.services.ai_tools.page_probe_js import PAGE_PROBE_JS
from app.services.picker_overlay_js import PICKER_OVERLAY_JS


@pytest.fixture
async def picker_page(tmp_path) -> Any:
    if find_spec("playwright") is None:
        pytest.skip("环境未安装 playwright")
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        from app.services.browser_action_runner import launch_persistent_chrome
        context = await launch_persistent_chrome(playwright, str(tmp_path / "profile"), headless=True)
        page = await context.new_page()
        try:
            yield page
        finally:
            await context.close()


async def _start_picker(page: Any, events: list[dict[str, Any]], mode: str) -> None:
    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)

    if not await page.evaluate("() => typeof window.__rpaPickerEvent__ === 'function'"):
        await page.expose_function("__rpaPickerEvent__", on_event)
    await page.evaluate(PICKER_OVERLAY_JS, {"requestId": "picker-test", "selectionMode": mode})


async def _event(events: list[dict[str, Any]]) -> dict[str, Any]:
    for _ in range(100):
        if events:
            return events[0]
        await asyncio.sleep(0.01)
    raise AssertionError("picker 没有回传事件")


async def test_composed_probe_and_picker_expression_keep_shared_selector_facts(picker_page: Any) -> None:
    page = picker_page
    await page.set_content("""
      <input id="keyword" placeholder="关键字">
      <button id="one">唯一元素</button>
      <table id="orders"><tbody>
        <tr><td><button>第一行</button></td></tr>
        <tr id="selected-row"><td><button id="row-button">第二行</button></td></tr>
      </tbody></table>
    """)

    observation = await page.evaluate(PAGE_PROBE_JS, {"version": 7})
    assert any(item["selector"] == "#keyword" for item in observation["inputs"])

    events: list[dict[str, Any]] = []
    await _start_picker(page, events, "single")
    await page.locator("#one").click()
    single = await _event(events)
    assert single == {
        "type": "capture",
        "requestId": "picker-test",
        "selector": "#one",
        "matches": 1,
        "selectedIncluded": True,
        "usesPosition": False,
        "text": "唯一元素",
        "url": page.url,
    }
    assert "confidence" not in single
    assert await page.locator("#rpa-picker-overlay").count() == 0
    assert await page.evaluate("document.documentElement.style.marginTop") == ""

    events.clear()
    await _start_picker(page, events, "multiple")
    await page.locator("#row-button").click()
    multiple = await _event(events)
    assert multiple["type"] == "capture"
    assert multiple["selector"] == "#orders > tbody > tr"
    assert multiple["matches"] == 2
    assert multiple["selectedIncluded"] is True
    assert multiple["usesPosition"] is False


async def test_picker_rebuilds_open_shadow_roots_after_pause_and_uses_composed_target(picker_page: Any) -> None:
    page = picker_page
    await page.set_content("<div id='host'></div>")
    events: list[dict[str, Any]] = []
    await _start_picker(page, events, "single")
    await page.locator("#rpa-picker-overlay").locator("button.toggle").click()
    await page.evaluate("""() => {
      const host = document.querySelector('#host');
      host.attachShadow({ mode: 'open' }).innerHTML = '<button id="late-shadow-button">影子元素</button>';
    }""")
    await page.locator("#rpa-picker-overlay").locator("button.toggle").click()
    await page.locator("#host").locator("#late-shadow-button").click()
    capture = await _event(events)
    assert capture["type"] == "capture"
    assert capture["selector"] == "#late-shadow-button"
    assert capture["matches"] == 1
    assert capture["selectedIncluded"] is True


async def test_picker_rejects_same_origin_iframe_from_a_real_frame_click(picker_page: Any) -> None:
    page = picker_page
    await page.set_content('<iframe id="child" srcdoc="<button id=inside>iframe 内元素</button>"></iframe>')
    await page.frame_locator("#child").locator("#inside").wait_for()
    events: list[dict[str, Any]] = []
    await _start_picker(page, events, "single")
    await page.frame_locator("#child").locator("#inside").click()
    error = await _event(events)
    assert error == {
        "type": "error",
        "requestId": "picker-test",
        "code": "frame_unsupported",
        "message": "当前拾取器不支持 iframe 内容，请回到主页面选择元素。",
    }
