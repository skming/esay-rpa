from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable

import pytest
from fastapi import WebSocketDisconnect

from app.core import storage
from app.services.extension_bridge_service import ExtensionBridgeService


class FakeWebSocket:
    """Minimal stand-in for fastapi.WebSocket driven by an asyncio.Queue, so tests can
    push responses and disconnects without a real network connection."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self._incoming: asyncio.Queue = asyncio.Queue()

    async def accept(self) -> None:
        return None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True

    async def receive_json(self) -> dict:
        item = await self._incoming.get()
        if item is None:
            raise WebSocketDisconnect()
        return item

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    def push_response(self, data: dict) -> None:
        self._incoming.put_nowait(data)

    def disconnect(self) -> None:
        self._incoming.put_nowait(None)


async def wait_until(condition: Callable[[], bool], *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("等待条件超时")


async def test_execute_sends_action_and_resolves_matching_response() -> None:
    service = ExtensionBridgeService()
    ws = FakeWebSocket()
    connection_task = asyncio.create_task(service.handle_connection(ws))
    await asyncio.sleep(0)
    assert service.is_connected

    async def respond_once_sent() -> None:
        while not ws.sent:
            await asyncio.sleep(0)
        request_id = ws.sent[0]["requestId"]
        ws.push_response({"requestId": request_id, "ok": True, "result": {"text": "hi"}})

    responder = asyncio.create_task(respond_once_sent())
    result = await service.execute({"type": "browser.extract", "selector": "#a"}, timeout=2.0)
    await responder

    assert result == {"text": "hi"}
    assert ws.sent[0]["action"] == {"type": "browser.extract", "selector": "#a"}

    ws.disconnect()
    await connection_task
    assert not service.is_connected


async def test_execute_raises_runtime_error_when_response_not_ok() -> None:
    service = ExtensionBridgeService()
    ws = FakeWebSocket()
    connection_task = asyncio.create_task(service.handle_connection(ws))
    await asyncio.sleep(0)

    async def respond_with_failure() -> None:
        while not ws.sent:
            await asyncio.sleep(0)
        request_id = ws.sent[0]["requestId"]
        ws.push_response({"requestId": request_id, "ok": False, "error": "选择器未匹配到元素"})

    responder = asyncio.create_task(respond_with_failure())
    with pytest.raises(RuntimeError, match="选择器未匹配到元素"):
        await service.execute({"type": "browser.click", "selector": "#missing"}, timeout=2.0)
    await responder

    ws.disconnect()
    await connection_task


async def test_execute_times_out_when_no_response_arrives() -> None:
    service = ExtensionBridgeService()
    ws = FakeWebSocket()
    connection_task = asyncio.create_task(service.handle_connection(ws))
    await asyncio.sleep(0)

    with pytest.raises(TimeoutError):
        await service.execute({"type": "browser.click", "selector": "#a"}, timeout=0.05)

    ws.disconnect()
    await connection_task


async def test_execute_raises_connection_error_when_extension_not_connected() -> None:
    service = ExtensionBridgeService()
    with pytest.raises(ConnectionError):
        await service.execute({"type": "browser.click", "selector": "#a"}, timeout=1.0)


async def test_is_connected_for_display_smooths_brief_disconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ExtensionBridgeService()
    ws = FakeWebSocket()
    connection_task = asyncio.create_task(service.handle_connection(ws))
    await asyncio.sleep(0)

    fake_now = 1_000.0
    monkeypatch.setattr(time, "time", lambda: fake_now)

    ws.disconnect()
    await connection_task
    assert not service.is_connected

    # 刚断开的瞬间（宽限期内）仍展示为已连接，避免正常重连抖动闪成"未连接"。
    fake_now += 2.0
    assert service.is_connected_for_display

    # 超过宽限期（8s）后如实展示为未连接。
    fake_now += 10.0
    assert not service.is_connected_for_display


async def test_second_connection_replaces_first_and_fails_old_pending() -> None:
    """新连接到达时立即顶替旧连接，避免僵尸 socket 持续占用桥接通道。

    旧连接上的未完成请求必须马上失败，否则调用方会继续等待一个不会再响应的
    socket，直到动作超时才暴露问题。
    """
    service = ExtensionBridgeService()
    first = FakeWebSocket()
    first_task = asyncio.create_task(service.handle_connection(first))
    await asyncio.sleep(0)
    assert service.is_connected

    pending_execute = asyncio.create_task(service.execute({"type": "browser.click", "selector": "#old"}, timeout=2.0))
    while not first.sent:
        await asyncio.sleep(0)

    second = FakeWebSocket()
    second_task = asyncio.create_task(service.handle_connection(second))
    await asyncio.sleep(0)

    assert service.is_connected
    await wait_until(lambda: first.closed)
    with pytest.raises(ConnectionError, match="顶替"):
        await pending_execute

    first.disconnect()
    await first_task

    second.disconnect()
    await second_task
    assert not service.is_connected


async def test_replaced_connection_finally_does_not_fail_new_pending() -> None:
    """旧连接被顶替后，它的 receive loop 可能晚于新连接请求退出。

    旧连接的 finally 只能清理自己的状态；如果无条件清空全局 pending，会把新连接
    刚创建的请求误报为"扩展连接已断开"。
    """
    service = ExtensionBridgeService()
    first = FakeWebSocket()
    first_task = asyncio.create_task(service.handle_connection(first))
    await asyncio.sleep(0)

    second = FakeWebSocket()
    second_task = asyncio.create_task(service.handle_connection(second))
    await asyncio.sleep(0)
    assert service.is_connected
    await wait_until(lambda: first.closed)

    pending_execute = asyncio.create_task(service.execute({"type": "browser.extract", "selector": "#new"}, timeout=2.0))
    while not second.sent:
        await asyncio.sleep(0)

    first.disconnect()
    await first_task

    request_id = second.sent[0]["requestId"]
    second.push_response({"requestId": request_id, "ok": True, "result": {"text": "fresh"}})
    assert await pending_execute == {"text": "fresh"}

    second.disconnect()
    await second_task


async def test_audit_log_never_contains_input_value_or_extracted_text() -> None:
    service = ExtensionBridgeService()
    ws = FakeWebSocket()
    connection_task = asyncio.create_task(service.handle_connection(ws))
    await asyncio.sleep(0)

    async def respond_with_sensitive_result() -> None:
        while not ws.sent:
            await asyncio.sleep(0)
        request_id = ws.sent[0]["requestId"]
        ws.push_response({"requestId": request_id, "ok": True, "result": {"text": "招商银行 6222 **** **** 1234"}})

    responder = asyncio.create_task(respond_with_sensitive_result())
    await service.execute(
        {"type": "browser.fill", "selector": "#password", "inputValue": "s3cr3t-password"}, timeout=2.0
    )
    await responder

    ws.disconnect()
    await connection_task

    audit_path = storage.resolve_logs_dir() / "extension_bridge_audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[-1])

    assert set(record.keys()) == {"requestId", "timestamp", "actionType", "selector", "ok", "error", "durationMs"}
    assert record["actionType"] == "browser.fill"
    assert record["selector"] == "#password"
    assert "s3cr3t-password" not in json.dumps(record)
    assert "招商银行" not in json.dumps(record)
