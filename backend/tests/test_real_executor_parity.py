"""同一节点、同一本地页面，通过真实 Playwright 和构建扩展验证执行契约。"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright
from websockets.asyncio.server import serve

from app.core import storage
from app.services.browser_action_runner import BrowserActionContext, BrowserActionRunner
from app.services.extension_bridge_service import ExtensionBridgeService
from app.services.extension_config_service import ExtensionConfigService
from app.services.extension_executor import ExtensionExecutor
from app.services.runtime_variables import RuntimeVariableStore
from test_real_extension_page_channel import SocketAdapter


HTML = """<!doctype html><html><body>
<form onsubmit="event.preventDefault();document.querySelector('#result').textContent='submitted'">
<input id="input"><button type="submit">Submit</button></form>
<input id="readonly" value="original" readonly><div id="plain">original</div>
<div id="editable" contenteditable="true">original</div>
<select id="select"><option value="a">A</option><option value="b">B</option></select>
<input id="check" type="checkbox" onclick="event.preventDefault()">
<button class="duplicate" hidden onclick="window.clicked='hidden'">Hidden</button>
<button class="duplicate" onclick="window.clicked='visible'">Visible</button>
<div id="late" hidden>Ready</div><div id="result"></div>
<div id="text"><style>.irrelevant { color: red }</style>Visible<span hidden>secret</span><script>void 0;</script></div>
<script>setTimeout(() => window.settled=true, 150);</script>
</body></html>"""


@pytest_asyncio.fixture(params=["playwright", "extension"])
async def executor_page(request, tmp_path, monkeypatch):
    build = Path(__file__).parents[2] / "extension/.output/chrome-mv3"
    if not build.exists():
        pytest.skip("先构建 extension/.output/chrome-mv3")
    monkeypatch.setattr(storage, "resolve_logs_dir", lambda: tmp_path / "logs")
    extension_id = ExtensionConfigService(extension_manifest_path=build / "manifest.json").trusted_extension_id()
    bridge = ExtensionBridgeService(lambda: extension_id)

    async def connected(socket):
        await bridge.handle_connection(SocketAdapter(socket))

    async with serve(
        connected, "127.0.0.1", 0,
        process_request=lambda connection, req: connection.respond(200, '{"status":"ok"}')
        if req.path == "/api/health" else None,
    ) as server, async_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        if not executable.exists():
            found = sorted((Path.home() / "Library/Caches/ms-playwright").glob(
                "chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
            ))
            if not found:
                pytest.skip("没有可加载扩展的 Chromium")
            executable = found[-1]
        args = []
        if request.param == "extension":
            extension = tmp_path / "extension"
            shutil.copytree(build, extension)
            background = extension / "background.js"
            source = background.read_text()
            assert "http://127.0.0.1:8765" in source
            port = server.sockets[0].getsockname()[1]
            background.write_text(source.replace("http://127.0.0.1:8765", f"http://127.0.0.1:{port}"))
            args = [f"--disable-extensions-except={extension}", f"--load-extension={extension}"]
        browser = await playwright.chromium.launch_persistent_context(
            str(tmp_path / "profile"), executable_path=str(executable), headless=True, args=args,
        )
        try:
            await browser.route("https://parity.test/**", lambda route: route.fulfill(
                body='<input type="password">' if route.request.url.endswith("/login") else HTML,
                content_type="text/html; charset=utf-8",
            ))
            if request.param == "extension":
                for _ in range(100):
                    if bridge.is_connected:
                        break
                    await asyncio.sleep(0.1)
                assert bridge.is_connected
                executor = ExtensionExecutor(bridge)
                context = await executor.create_context(owner="parity-test")
            else:
                executor = BrowserActionRunner()
                context = BrowserActionContext(playwright=None, browser=browser, page=browser.pages[0])

            async def run(node, timeout_ms=1800):
                return await executor.run(node, RuntimeVariableStore.from_initial({}), context, timeout_ms=timeout_ms)

            await run({"type": "browser.open", "targetUrl": "https://parity.test/page"})
            page = next(page for page in browser.pages if page.url == "https://parity.test/page")
            yield SimpleNamespace(run=run, page=page, bridge=bridge, channel=request.param)
        finally:
            await browser.close()


async def test_ensure_login_navigates_to_the_requested_page(executor_page):
    result = await executor_page.run({"type": "browser.ensureLogin", "targetUrl": "https://parity.test/login"})
    assert result.values == ["login_required"]
    assert executor_page.page.url == "https://parity.test/login"


async def test_wait_requires_visibility(executor_page):
    await executor_page.page.evaluate("setTimeout(() => document.querySelector('#late').hidden=false, 150)")
    await executor_page.run({"type": "browser.wait", "selector": "#late"})
    assert await executor_page.page.locator("#late").is_visible()


@pytest.mark.parametrize("selector", ["#readonly", "#plain"])
async def test_fill_rejects_noneditable_targets(executor_page, selector):
    with pytest.raises(Exception):
        await executor_page.run({"type": "browser.fill", "selector": selector, "inputValue": "changed"})
    assert await executor_page.page.locator("#readonly").input_value() == "original"
    assert await executor_page.page.locator("#plain").text_content() == "original"


async def test_fill_contenteditable(executor_page):
    await executor_page.run({"type": "browser.fill", "selector": "#editable", "inputValue": "changed"})
    assert await executor_page.page.locator("#editable").text_content() == "changed"


async def test_select_rejects_missing_options_without_clearing_selection(executor_page):
    with pytest.raises(Exception):
        await executor_page.run({"type": "browser.select", "selector": "#select", "inputValue": "missing"}, timeout_ms=300)
    assert await executor_page.page.locator("#select").input_value() == "a"


async def test_check_requires_the_requested_state(executor_page):
    with pytest.raises(Exception):
        await executor_page.run({"type": "browser.check", "selector": "#check", "checked": True}, timeout_ms=300)
    assert not await executor_page.page.locator("#check").is_checked()


async def test_click_uses_first_visible_match(executor_page):
    await executor_page.run({"type": "browser.click", "selector": ".duplicate"})
    assert await executor_page.page.evaluate("window.clicked") == "visible"


async def test_text_extract_excludes_style_scripts_and_hidden_text(executor_page):
    result = await executor_page.run({"type": "browser.extract", "selector": "#text"})
    assert result.values == ["Visible"]


async def test_open_delay_applies_after_navigation(executor_page):
    await executor_page.run({"type": "browser.open", "targetUrl": "https://parity.test/page?next", "delayMs": 250})
    assert await executor_page.page.evaluate("window.settled") is True


async def test_closing_last_owned_tab_preserves_it(executor_page):
    result = await executor_page.run({"type": "browser.tab.close"})
    assert result.values == ["https://parity.test/page"]
    assert not executor_page.page.is_closed()


async def test_closing_a_second_owned_tab_restores_the_first(executor_page):
    await executor_page.run({"type": "browser.tab.open", "targetUrl": "https://parity.test/second"})
    result = await executor_page.run({"type": "browser.tab.close"})
    assert result.values == ["https://parity.test/page"]
    await executor_page.run({"type": "browser.fill", "selector": "#input", "inputValue": "first page"})
    assert await executor_page.page.locator("#input").input_value() == "first page"
