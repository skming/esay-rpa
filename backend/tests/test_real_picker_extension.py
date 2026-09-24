"""真实加载构建扩展，验证 picker 长请求的标签页、文档和租约边界。"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from playwright.async_api import Page, async_playwright
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from app.core import storage
from app.services.extension_bridge_service import ExtensionBridgeService
from app.services.extension_executor import ExtensionBusyError, ExtensionExecutor
from app.services.picker_service import PickerOpenRequest, PickerService


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


def _request(request_id: str) -> PickerOpenRequest:
    return PickerOpenRequest(
        requestId=request_id,
        mode="pick",
        browserExecutor="extension",
        flowId="flow-picker",
        nodeId="node-picker",
        field="selector",
        selectionMode="single",
    )


async def _wait_for_overlay(page: Page) -> None:
    await page.wait_for_selector("#rpa-picker-overlay", state="attached", timeout=5_000)


async def _wait_for_bridge(bridge: ExtensionBridgeService) -> None:
    for _ in range(100):
        if bridge.is_connected:
            return
        await asyncio.sleep(0.1)
    raise AssertionError("构建扩展没有连接隔离桥接服务器")


async def test_real_extension_picker_keeps_request_tab_document_and_lease_boundaries(tmp_path, monkeypatch):
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

        async with async_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            if not executable.exists():
                found = sorted((Path.home() / "Library/Caches/ms-playwright").glob(
                    "chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
                ))
                if not found:
                    pytest.skip("环境缺件：没有可加载扩展的 Chromium，先执行 playwright install chromium")
                executable = found[-1]
            context = await playwright.chromium.launch_persistent_context(
                str(tmp_path / "profile"),
                executable_path=str(executable),
                headless=True,
                args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"],
            )
            html = """<html><body>
              <button id="pick-one">原标签目标</button>
              <button id="pick-two">新请求目标</button>
            </body></html>"""
            await context.route("https://picker.test/**", lambda route: route.fulfill(
                body=html, content_type="text/html; charset=utf-8"
            ))
            original = context.pages[0]
            await original.goto("https://picker.test/original")
            other = await context.new_page()
            await other.goto("https://picker.test/other")
            await original.bring_to_front()
            await _wait_for_bridge(bridge)

            extension_executor = ExtensionExecutor(bridge)
            picker = PickerService(str(tmp_path / "profile"), extension_provider=lambda: extension_executor)
            try:
                await picker.open(_request("bound-tab"))
                await _wait_for_overlay(original)
                assert not await original.evaluate(
                    "document.documentElement.classList.contains('rpa-studio-page-blocked')"
                )
                await other.bring_to_front()
                bound_result_task = asyncio.create_task(picker.wait_for_result("bound-tab"))
                await original.locator("#pick-one").click()
                bound = await asyncio.wait_for(bound_result_task, 5)
                assert bound["type"] == "capture"
                assert bound["url"] == "https://picker.test/original"
                assert bound["selector"] == "#pick-one"
                assert await other.evaluate("document.visibilityState") == "visible"
                assert not original.is_closed() and not other.is_closed()

                await original.bring_to_front()
                await picker.open(_request("escape-cancel"))
                await _wait_for_overlay(original)
                cancel_task = asyncio.create_task(picker.wait_for_result("escape-cancel"))
                await original.keyboard.press("Escape")
                cancelled = await asyncio.wait_for(cancel_task, 5)
                assert cancelled["type"] == "cancel"
                assert not original.is_closed()

                await picker.open(_request("old-request"))
                await _wait_for_overlay(original)
                old_task = asyncio.create_task(picker.wait_for_result("old-request"))
                await picker.open(_request("new-request"))
                old = await asyncio.wait_for(old_task, 5)
                assert old["type"] == "cancel" and old["reason"] == "replaced"
                await _wait_for_overlay(original)
                active_session = picker._session
                assert active_session is not None and active_session.tab_id is not None
                stale_cancel = await extension_executor.page_action({
                    "type": "page.pickerCancel",
                    "explorationTabId": active_session.tab_id,
                    "pickerRequestId": "old-request",
                })
                assert stale_cancel == {"ok": True, "cancelled": False}
                new_task = asyncio.create_task(picker.wait_for_result("new-request"))
                await original.locator("#pick-two").click()
                new = await asyncio.wait_for(new_task, 5)
                assert new["type"] == "capture" and new["selector"] == "#pick-two"

                await original.bring_to_front()
                await picker.open(_request("navigated-request"))
                await _wait_for_overlay(original)
                navigated_task = asyncio.create_task(picker.wait_for_result("navigated-request"))
                await original.goto("https://picker.test/after-navigation")
                navigated = await asyncio.wait_for(navigated_task, 5)
                assert navigated["type"] in {"cancel", "error"}
                assert await original.locator("#rpa-picker-overlay").count() == 0

                closing = await context.new_page()
                await closing.goto("https://picker.test/closing")
                await closing.bring_to_front()
                await picker.open(_request("closed-request"))
                await _wait_for_overlay(closing)
                closed_task = asyncio.create_task(picker.wait_for_result("closed-request"))
                await closing.close()
                closed = await asyncio.wait_for(closed_task, 5)
                assert closed["type"] in {"cancel", "error"}

                foreign = await extension_executor.create_context(owner="另一运行", manage_tabs=False)
                try:
                    with pytest.raises(ExtensionBusyError):
                        await picker.open(_request("must-not-release-foreign"))
                    assert extension_executor.lease_holder == "另一运行"
                finally:
                    await extension_executor.close_context(foreign)
                assert extension_executor.lease_holder is None
                assert not original.is_closed() and not other.is_closed()
            finally:
                await picker.close()
                await context.close()
