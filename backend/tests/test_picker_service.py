from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.services.picker_service import PickerOpenRequest, PickerService


def request(request_id="pick-one", **extra):
    return PickerOpenRequest(**{"requestId": request_id, "flowId": "flow-one", "nodeId": "node-one", "field": "selector",
                                "browserExecutor": "extension", **extra})


class FakeExtension:
    def __init__(self):
        self.owner = None
        self.pending = {}
        self.calls = []
        self.closed = []

    async def create_context(self, *, owner, manage_tabs):
        assert manage_tabs is False
        if self.owner:
            raise RuntimeError("浏览器已被任务占用")
        self.owner = owner
        return SimpleNamespace(owner=owner)

    async def close_context(self, context):
        self.closed.append(context)
        if context is not None and self.owner == context.owner:
            self.owner = None

    async def page_action(self, action, *, timeout=15):
        self.calls.append(action)
        if action["type"] == "page.begin":
            return {"tab_id": 7}
        if action["type"] == "page.observe":
            return {"document_id": "document-one"}
        if action["type"] == "page.picker":
            future = asyncio.get_running_loop().create_future()
            self.pending[action["pickerRequestId"]] = future
            return await future
        return {}


async def wait_until(check):
    for _ in range(50):
        if check():
            return
        await asyncio.sleep(0)
    assert check()


async def test_extension_capture_preserves_request_identity_and_releases_lease(tmp_path):
    ext = FakeExtension()
    service = PickerService(str(tmp_path), extension_provider=lambda: ext)
    await service.open(request())
    await wait_until(lambda: "pick-one" in ext.pending)
    ext.pending["pick-one"].set_result({"type": "capture", "requestId": "pick-one", "selector": "#submit",
                                      "matches": 1, "selectedIncluded": True, "usesPosition": False,
                                      "text": "提交", "url": "https://fixture.test"})
    result = await service.wait_for_result("pick-one")
    assert result["type"] == "capture"
    assert (result["flowId"], result["nodeId"], result["field"]) == ("flow-one", "node-one", "selector")
    assert result["tabId"] == 7 and result["documentId"] == "document-one"
    assert "confidence" not in result
    await wait_until(lambda: ext.owner is None)
    assert any(a["type"] == "page.end" for a in ext.calls)


async def test_replace_and_late_close_only_affect_own_request(tmp_path):
    ext = FakeExtension()
    service = PickerService(str(tmp_path), extension_provider=lambda: ext)
    await service.open(request())
    first = service._session
    waiter = asyncio.create_task(service.wait_for_result("pick-one"))
    await asyncio.sleep(0)
    await service.open(request("pick-two"))
    assert (await waiter)["type"] == "cancel"
    await service.close("pick-one")
    await service._complete(first, {"type": "cancel"})
    assert not service._session.result.done()
    assert ext.owner.endswith("pick-two")
    await service.close("pick-two")
    assert (await service.wait_for_result("pick-two"))["type"] == "cancel"
    assert ext.owner is None


async def test_busy_extension_does_not_release_someone_elses_context(tmp_path):
    ext = FakeExtension()
    ext.owner = "运行中的任务"
    service = PickerService(str(tmp_path), extension_provider=lambda: ext)
    with pytest.raises(RuntimeError, match="占用"):
        await service.open(request())
    assert ext.owner == "运行中的任务"
    assert not ext.calls


@pytest.mark.parametrize("mode,matches,included,expected", [
    ("single", 2, True, "error"), ("single", 1, False, "error"),
    ("multiple", 2, True, "capture"), ("multiple", 1, True, "capture"),
])
async def test_capture_validates_real_match_evidence(tmp_path, mode, matches, included, expected):
    ext = FakeExtension()
    service = PickerService(str(tmp_path), extension_provider=lambda: ext)
    await service.open(request(selectionMode=mode))
    await service._complete(service._session, {"type": "capture", "selector": "tr", "matches": matches,
                                             "selectedIncluded": included, "usesPosition": False})
    assert (await service.wait_for_result("pick-one"))["type"] == expected
    await service.close()


async def test_browse_extension_leaves_user_tab_and_releases_lease(tmp_path):
    ext = FakeExtension()
    service = PickerService(str(tmp_path), extension_provider=lambda: ext)
    await service.open(PickerOpenRequest(requestId="browse", mode="browse", browserExecutor="extension"))
    assert ext.owner is None
    assert [a["type"] for a in ext.calls] == ["page.begin", "page.end"]
    await service.close()


def test_request_requires_target_identity_and_playwright_url():
    with pytest.raises(ValidationError, match="flowId"):
        PickerOpenRequest(requestId="missing", browserExecutor="extension")
    with pytest.raises(ValidationError, match="targetUrl"):
        request(browserExecutor="playwright")


async def test_navigation_callback_cannot_cancel_replacement(tmp_path):
    service = PickerService(str(tmp_path))
    service._open_playwright = AsyncMock()
    await service.open(request("old", targetUrl="https://fixture.test").model_copy(update={"browserExecutor": "playwright"}))
    old = service._session
    await service.open(request("new", targetUrl="https://fixture.test").model_copy(update={"browserExecutor": "playwright"}))
    await service._complete(old, {"type": "cancel", "reason": "page_navigated"})
    assert not service._session.result.done()
    await service.close()


class FakeSocket:
    def __init__(self, service, request_id):
        self.app = SimpleNamespace(state=SimpleNamespace(picker_service=service))
        self.query_params = {"requestId": request_id}
        self.disconnected = asyncio.get_running_loop().create_future()
        self.messages = []

    async def accept(self):
        pass

    async def receive(self):
        return await self.disconnected

    async def send_json(self, data):
        self.messages.append(data)

    async def close(self, **kwargs):
        pass


async def test_websocket_disconnect_releases_only_matching_request(tmp_path):
    from app.api.websockets import picker_socket

    ext = FakeExtension()
    service = PickerService(str(tmp_path), extension_provider=lambda: ext)
    await service.open(request())
    socket = FakeSocket(service, "pick-one")
    task = asyncio.create_task(picker_socket(socket))
    await asyncio.sleep(0)
    socket.disconnected.set_result({"type": "websocket.disconnect"})
    await task
    assert ext.owner is None
    assert (await service.wait_for_result("pick-one"))["type"] == "cancel"
    await service.open(request("pick-two"))
    old_socket = FakeSocket(service, "pick-one")
    await picker_socket(old_socket)
    assert old_socket.messages[0]["type"] == "error"
    assert ext.owner.endswith("pick-two")
    await service.close()
