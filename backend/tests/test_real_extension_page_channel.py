"""加载构建后的扩展，经真实 WebSocket 和内容脚本验证助手入口。"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from playwright.async_api import async_playwright
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from app.core import storage
from app.services.ai_tools import extension_page_channel, page_session
from app.services.ai_tools.executor import RpaToolExecutor
from app.services.extension_bridge_service import ExtensionBridgeService
from app.services.extension_executor import ExtensionExecutor


class SocketAdapter:
    def __init__(self, socket):
        self.socket = socket
        self.headers = socket.request.headers

    async def accept(self):
        pass

    async def receive_json(self):
        try:
            return json.loads(await self.socket.recv())
        except ConnectionClosed as exc:
            raise WebSocketDisconnect() from exc

    async def send_json(self, payload):
        await self.socket.send(json.dumps(payload))

    async def close(self, code=1000):
        await self.socket.close(code=code)


async def test_extension_observe_act_and_document_boundaries(tmp_path, monkeypatch):
    build = Path(__file__).parents[2] / "extension/.output/chrome-mv3"
    assert build.exists(), "先在 extension/ 执行 pnpm build"
    monkeypatch.setattr(storage, "resolve_logs_dir", lambda: tmp_path / "logs")
    bridge = ExtensionBridgeService()
    async def connected(socket):
        await bridge.handle_connection(SocketAdapter(socket))
    async with serve(connected, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        extension = tmp_path / "extension"
        shutil.copytree(build, extension)
        background = extension / "background.js"
        source = background.read_text()
        assert "http://127.0.0.1:8765" in source
        background.write_text(source.replace("http://127.0.0.1:8765", f"http://127.0.0.1:{port}"))
        # Only the copied test bundle changes endpoint; the installed extension remains untouched.
        async with async_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            if not executable.exists():
                found = sorted((Path.home() / "Library/Caches/ms-playwright").glob("chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))
                assert found, "缺少支持加载扩展的 Chromium"
                executable = found[-1]
            context = await playwright.chromium.launch_persistent_context(
                str(tmp_path / "profile"), executable_path=str(executable), headless=True,
                args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"],
            )
            ext = ExtensionExecutor(bridge)
            manager = SimpleNamespace(is_extension_enabled=lambda: True,
                                      is_extension_connected=lambda: bridge.is_connected,
                                      extension_exploration_executor=lambda: ext)
            executor = RpaToolExecutor(None, manager)
            reset = page_session.set_owner("real-extension-test")
            try:
                page = context.pages[0]
                await context.add_cookies([{"name": "session", "value": "fixture-login", "url": "https://fixture.test"}])
                html = """<html><body><div id="range"><input id="start" placeholder="开始日期"><input id="end" placeholder="结束日期"></div>
                <button class="duplicate">查询</button><button class="duplicate">查询</button>
                <button id="open" onclick="document.getElementById('layer').hidden=false">打开</button>
                <div id="layer" role="dialog" hidden style="position:fixed;top:100px;background:white">选择日期<button id="day" onclick="document.querySelector('#start').value='2026-01-02'">2</button></div>
                <section>Login credentials<input id="password" type="password" value="fixture-secret"></section><input id="readonly" readonly>
                <div id="scroll" style="overflow:auto;height:40px"><div style="height:800px">滚动</div></div>
                <div id="shadow"></div><script>document.querySelector('#shadow').attachShadow({mode:'open'}).innerHTML='<button id="shadow-button">shadow</button>';</script>
                </body></html>"""
                await context.route("https://fixture.test/**", lambda route: route.fulfill(body=html, content_type="text/html; charset=utf-8"))
                await page.goto("https://fixture.test/page")
                await page.bring_to_front()
                for _ in range(100):
                    if bridge.is_connected:
                        break
                    await asyncio.sleep(0.1)
                assert bridge.is_connected, "构建扩展没有连接隔离桥接服务器"
                assert await page.evaluate("document.cookie") == "session=fixture-login"
                first = await executor.execute("inspect_page", {"browser_executor": "extension"})
                assert "error" not in first, first
                assert first["url"] == page.url
                assert "fixture-secret" not in json.dumps(first)
                assert first.get("date_controls"), first
                recipe = first["date_controls"][0]["interaction_recipe"]
                assert recipe["trigger"] == "#start" and recipe["end_input"] == "#end"
                ref = next(item["ref"] for item in first["inputs"] if item["selector"] == "#start")
                assert any(item["selector"] == "#shadow-button" for item in first["buttons"])
                duplicate = await executor.execute("interact_page", {"action": "click", "selector": ".duplicate", "wait_ms": 0})
                assert duplicate["status"] == "ambiguous_selector", duplicate
                second_page = await context.new_page()
                await second_page.goto("https://fixture.test/other")
                filled = await executor.execute("interact_page", {"action": "fill", "element_ref": ref, "observation_version": first["observation_version"], "value": "2026-01-01", "wait_ms": 0})
                assert filled["action_effect"]["status"] == "target_reached", filled
                assert await page.input_value("#start") == "2026-01-01"
                assert await second_page.input_value("#start") == ""
                stale = await executor.execute("interact_page", {"action": "click", "element_ref": ref, "observation_version": first["observation_version"], "wait_ms": 0})
                assert stale["status"] == "stale_element_ref", stale
                opened = await executor.execute("interact_page", {"action": "click", "selector": "#open", "wait_selector": "#layer"})
                assert "error" not in opened, opened
                assert opened["observation"]["open_layers"]
                assert await page.locator("#layer").is_visible()
                picked = await executor.execute("interact_page", {"action": "click", "selector": "#day", "wait_ms": 0})
                assert "error" not in picked, picked
                assert await page.input_value("#start") == "2026-01-02"
                readonly = await executor.execute("interact_page", {"action": "fill", "selector": "#readonly", "value": "bad", "wait_ms": 0})
                assert "只读" in readonly["error"]
                password = await executor.execute("interact_page", {"action": "fill", "selector": "#password", "value": "bad", "wait_ms": 0})
                assert "凭据" in password["error"]
                assert "fixture-secret" not in json.dumps(password)
                scroll = await executor.execute("interact_page", {"action": "scroll", "selector": "#scroll", "value": "100", "wait_ms": 0})
                assert scroll["action_effect"]["status"] == "target_reached", scroll
                assert await page.locator("#scroll").evaluate("el => el.scrollTop") == 100
                await page.bring_to_front()
                shot = await executor.execute("inspect_screenshot", {})
                assert shot["image_base64"], shot
                ch = extension_page_channel.get_channel()
                doc = ch.document_id
                await page.reload()
                rejected = await executor.execute("interact_page", {"action": "click", "element_ref": "e0", "observation_version": ch.version, "wait_ms": 0})
                assert rejected["status"] == "stale_element_ref", rejected
                current = await executor.execute("inspect_page", {})
                assert current["document_id"] != doc
                ambiguous = await executor.execute("inspect_page", {"scope_selector": ".duplicate"})
                assert ambiguous["required_action"] == "narrow_scope_selector", ambiguous
                await page.close()
                missing = await executor.execute("interact_page", {"action": "fill", "selector": "#start", "value": "wrong", "wait_ms": 0})
                assert "error" in missing
                assert await second_page.input_value("#start") == ""
            finally:
                try:
                    await extension_page_channel.close_current(token="real-extension-test")
                except RuntimeError:
                    pass
                page_session.reset_owner(reset)
                await context.close()
