"""桥接浏览器扩展（见 /extension）：扩展的 background service worker 与本服务保持一条持久 WebSocket，
按 requestId 做请求/响应配对（同一时刻只有一个扩展实例连接）。
这是 Playwright 驱动的 BrowserActionRunner 之外的可选执行器，只能在用户真实 Chrome 窗口打开时工作，
不支持无人值守/定时执行；调用方需先检查 `is_connected` 再路由节点到此执行器。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from app.core import storage

logger = logging.getLogger(__name__)

# 仅用于平滑 UI 展示的"已连接"状态（避免正常重连瞬间抖动为"未连接"）；execute() 的真实路由判断始终直接检查 self._socket。
_DISPLAY_DISCONNECT_GRACE_SECONDS = 8.0

# 需明显大于插件 20s 心跳间隔：service worker 被系统终止/睡眠唤醒/NAT 静默丢包都不会触发 TCP FIN/RST，
# 无超时会导致 self._socket 假性"已连接"直到下次真实动作卡满 30s 才暴露。
_RECEIVE_TIMEOUT_SECONDS = 45.0

# 新连接到达时立即顶替旧连接（而非等待判活窗口）：曾用"30s 无心跳判僵尸"方案，
# 但插件重连远快于该窗口，导致真实重连被反复拒绝、动作发往死 socket 干等超时。
# 单例桥接前提是同一时刻只有一个真实浏览器，新连接几乎总是同一插件重连而非竞争方。

# 插件操作的是用户真实登录态浏览器，单独留痕供事后安全审查；只记录类型/选择器/耗时/成败，
# 不记录 inputValue 或页面文本（可能含密码、验证码等敏感数据）。
_AUDIT_LOG_FILENAME = "extension_bridge_audit.jsonl"


def _write_audit_record(record: dict[str, Any]) -> None:
    try:
        path = storage.resolve_logs_dir() / _AUDIT_LOG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("写入插件执行器审计日志失败")


class ExtensionBridgeService:
    def __init__(self) -> None:
        self._socket: WebSocket | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._connected_since: float | None = None
        self._disconnected_at: float | None = None
        self._last_seen_at: float | None = None

    @property
    def is_connected(self) -> bool:
        """真实连接状态，可安全用于节点执行门控。"""
        return self._socket is not None

    @property
    def is_connected_for_display(self) -> bool:
        """带宽限期平滑的连接状态，仅供 UI 展示，不可用于判断能否真正发送动作。"""
        if self._socket is not None:
            return True
        if self._disconnected_at is None:
            return False
        return (time.time() - self._disconnected_at) < _DISPLAY_DISCONNECT_GRACE_SECONDS

    @property
    def connected_since(self) -> float | None:
        return self._connected_since

    async def handle_connection(self, websocket: WebSocket) -> None:
        """接受插件 WS 连接并持续处理响应直到断开；新连接总是立即顶替已注册的旧连接。"""
        if self._socket is not None:
            logger.info("新插件桥接连接到达，立即顶替旧连接")
            self._evict_current_socket()
        await websocket.accept()
        self._socket = websocket
        self._connected_since = time.time()
        self._disconnected_at = None
        self._last_seen_at = time.time()
        try:
            while True:
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=_RECEIVE_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    logger.warning("插件桥接连接 %.0fs 内无任何消息（含 keepalive），判定为已失活，主动断开", _RECEIVE_TIMEOUT_SECONDS)
                    break
                self._last_seen_at = time.time()
                self._resolve(message)
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
        finally:
            if self._socket is websocket:
                self._socket = None
                self._connected_since = None
                self._disconnected_at = time.time()
                self._last_seen_at = None
                self._fail_all_pending("扩展连接已断开")
            try:
                await websocket.close()
            except Exception:
                pass

    def _evict_current_socket(self) -> None:
        old_socket = self._socket
        self._socket = None
        self._connected_since = None
        self._disconnected_at = time.time()
        self._last_seen_at = None
        self._fail_all_pending("扩展连接已被新连接顶替")
        if old_socket is not None:
            asyncio.ensure_future(self._close_quietly(old_socket))

    @staticmethod
    async def _close_quietly(websocket: WebSocket) -> None:
        try:
            await websocket.close()
        except Exception:
            pass

    def _resolve(self, message: dict[str, Any]) -> None:
        request_id = message.get("requestId")
        future = self._pending.pop(request_id, None) if request_id is not None else None
        if future is not None and not future.done():
            future.set_result(message)

    def _fail_all_pending(self, reason: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError(reason))
        self._pending.clear()

    async def execute(self, action: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        """向已连接插件发送动作并等待结果；action 形状与 BrowserActionRunner 节点参数一致（selector/inputValue）。"""
        if self._socket is None:
            raise ConnectionError("没有已连接的浏览器扩展")

        request_id = str(uuid4())
        started_at = time.monotonic()
        audit_base = {
            "requestId": request_id,
            "timestamp": time.time(),
            "actionType": action.get("type"),
            "selector": action.get("selector"),
        }
        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._socket.send_json({"requestId": request_id, "action": action})
            response = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            self._pending.pop(request_id, None)
            _write_audit_record({**audit_base, "ok": False, "error": "timeout", "durationMs": int((time.monotonic() - started_at) * 1000)})
            raise TimeoutError(f"扩展执行动作超时（{timeout}s）: {action.get('type')}") from None
        finally:
            self._pending.pop(request_id, None)

        ok = bool(response.get("ok", False))
        _write_audit_record(
            {
                **audit_base,
                "ok": ok,
                "error": None if ok else (response.get("error") or "扩展执行动作失败"),
                "durationMs": int((time.monotonic() - started_at) * 1000),
            }
        )
        if not ok:
            raise RuntimeError(response.get("error") or "扩展执行动作失败")
        return response.get("result", {})
