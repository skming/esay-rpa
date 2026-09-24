"""加载构建后的扩展，经真实 WebSocket 和内容脚本验证助手入口。"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from app.core import storage
from app.services.ai_tools import extension_page_channel, page_session
from app.services.ai_tools.executor import RpaToolExecutor
from app.services.extension_bridge_service import ExtensionBridgeService
from app.services.extension_executor import ExtensionExecutor

# 与 test_real_page_flow_replay 的 Playwright 回放共用同一份片段：空表头列的命名与列位
# 是两条通道漂移过的地方，页面各写一份就会各自漂各自的，比不出不一致。
PRICING_TABLE = (Path(__file__).parent / "pages/table_blank_header_col.html").read_text(encoding="utf-8")


def _item(result, key, selector):
    return next(item for item in result.get(key) or [] if item.get("selector") == selector)


def _action(item, operation):
    return next(value for value in item.get("actions") or [] if value.startswith(f"{operation}:"))


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
    if not build.exists():
        pytest.skip("环境缺件：extension/.output/chrome-mv3 不存在，先在 extension/ 执行 pnpm build")
    monkeypatch.setattr(storage, "resolve_logs_dir", lambda: tmp_path / "logs")
    bridge = ExtensionBridgeService()
    async def connected(socket):
        await bridge.handle_connection(SocketAdapter(socket))
    async with serve(
        connected,
        "127.0.0.1",
        0,
        process_request=lambda connection, request: connection.respond(200, '{"status":"ok"}')
        if request.path == "/api/health" else None,
    ) as server:
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
                if not found:
                    pytest.skip("环境缺件：没有可加载扩展的 Chromium，先执行 playwright install chromium")
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
                """ + PRICING_TABLE + """
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
                assert len(first["tables"]) == 1, first["tables"]
                assert await page.locator(first["tables"][0]["row_selector"]).count() == 2
                table_result = await bridge.execute({"type": "browser.extract", "selector": "#pricing tr", "extractMode": "table"})
                assert table_result["values"] == [
                    {"列1": "", "Model Name": "model-a", "Ratio": "1.5", "Price": "$3", "Usage": "4,572 194 296.2K"},
                    {"列1": "", "Model Name": "model-b", "Ratio": "3", "Price": "$6", "Usage": "1,208 77 41.9K"},
                ]
                assert first.get("date_controls"), first
                recipe = first["date_controls"][0]["interaction_recipe"]
                assert recipe["trigger"] == "#start" and recipe["end_input"] == "#end"
                assert any(item["selector"] == "#shadow-button" for item in first["buttons"])
                duplicates = [item for item in first["buttons"] if item.get("text") == "查询"]
                assert len(duplicates) == 2
                assert len({_action(item, "click") for item in duplicates}) == 2
                second_page = await context.new_page()
                await second_page.goto("https://fixture.test/other")
                fill_action = _action(_item(first, "inputs", "#start"), "fill")
                filled = await executor.execute(
                    "interact_page", {"action_id": fill_action, "value": "2026-01-01"}
                )
                assert "error" not in filled, filled
                assert filled["action_effect"]["status"] == "target_reached", filled
                assert await page.input_value("#start") == "2026-01-01"
                assert await second_page.input_value("#start") == ""
                stale = await executor.execute("interact_page", {"action_id": fill_action, "value": "ignored"})
                assert stale["status"] == "stale_action", stale
                open_action = _action(_item(filled["observation"], "buttons", "#open"), "click")
                opened = await executor.execute(
                    "interact_page", {"action_id": open_action, "wait_selector": "#layer"}
                )
                assert "error" not in opened, opened
                assert opened["observation"]["open_layers"]
                assert await page.locator("#layer").is_visible()
                day_action = _action(_item(opened["observation"], "buttons", "#day"), "click")
                picked = await executor.execute("interact_page", {"action_id": day_action})
                assert "error" not in picked, picked
                assert await page.input_value("#start") == "2026-01-02"
                assert not any(
                    action.startswith("fill:")
                    for action in _item(picked["observation"], "inputs", "#readonly").get("actions") or []
                )
                password = _item(picked["observation"], "inputs", "#password")
                assert not any(action.startswith("fill:") for action in password.get("actions") or [])
                assert "fixture-secret" not in json.dumps(password)
                scroll_action = _action(_item(picked["observation"], "scrollables", "#scroll"), "scroll")
                scroll = await executor.execute("interact_page", {"action_id": scroll_action})
                assert scroll["action_effect"]["status"] == "target_reached", scroll
                assert await page.locator("#scroll").evaluate("el => el.scrollTop") > 0
                await page.bring_to_front()
                shot = await executor.execute("inspect_screenshot", {})
                assert shot["image_base64"], shot
                ch = extension_page_channel.get_channel()
                doc = ch.document_id
                reload_action = _action(_item(scroll["observation"], "buttons", "#open"), "click")
                await page.reload()
                rejected = await executor.execute("interact_page", {"action_id": reload_action})
                assert rejected["status"] in {"stale_action", "extension_interaction_failed"}, rejected
                current = await executor.execute("inspect_page", {})
                assert current["document_id"] != doc
                ambiguous = await executor.execute("inspect_page", {"scope_selector": ".duplicate"})
                assert ambiguous["required_action"] == "narrow_scope_selector", ambiguous
                close_action = _action(_item(current, "inputs", "#start"), "fill")
                await page.close()
                missing = await executor.execute(
                    "interact_page", {"action_id": close_action, "value": "wrong"}
                )
                assert "error" in missing
                assert await second_page.input_value("#start") == ""
            finally:
                try:
                    await extension_page_channel.close_current(token="real-extension-test")
                except RuntimeError:
                    pass
                page_session.reset_owner(reset)
                await context.close()
