from __future__ import annotations

import asyncio
from playwright.async_api import Error as PlaywrightError

from app.services import picker_service as picker_module
from app.services.browser_action_runner import launch_persistent_chrome
from app.services.picker_service import PickerOpenRequest, PickerService


async def test_playwright_picker_captures_in_isolated_profile_and_navigation_cancels(tmp_path, monkeypatch):
    async def launch(playwright, profile, *, headless):
        context = await launch_persistent_chrome(playwright, profile, headless=True)
        await context.route("https://picker.test/**", lambda route: route.fulfill(
            body='<html><body><button id="capture">真实拾取</button><a href="/next">下一页</a></body></html>',
            content_type="text/html; charset=utf-8",
        ))
        return context

    monkeypatch.setattr(picker_module, "launch_persistent_chrome", launch)
    service = PickerService(str(tmp_path / "profile"))
    def request(request_id):
        return PickerOpenRequest(requestId=request_id, targetUrl="https://picker.test/page",
                                 flowId="flow", nodeId="node", field="selector")
    try:
        await service.open(request("capture"))
        page = service._session.page
        try:
            await page.click("#capture")
        except PlaywrightError:
            pass  # capture 会关闭所属浏览器；最终是否成功由下面的回执断言确认。
        captured = await asyncio.wait_for(service.wait_for_result("capture"), 5)
        assert captured["type"] == "capture", captured
        assert captured["selector"] == "#capture"
        assert captured["matches"] == 1 and captured["selectedIncluded"] is True
        await service.open(request("navigate"))
        page = service._session.page
        try:
            await page.goto("https://picker.test/next")
        except PlaywrightError:
            pass
        cancelled = await asyncio.wait_for(service.wait_for_result("navigate"), 5)
        assert cancelled["type"] == "cancel"
        assert cancelled["reason"] == "page_navigated"
    finally:
        await service.close()
