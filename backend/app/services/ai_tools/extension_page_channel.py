"""在扩展当前标签页取证；tab、文档、观察版本共同限定临时引用。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import app.services.ai_tools.page_session as _page_session
from app.services.ai_tools.page_observation import (
    ambiguous_selector_error,
    element_not_found_error,
    stale_ref_error,
)

CHANNEL_OWNER = "AI 助手 · 扩展探索会话"
_ACTION_TIMEOUT_SECONDS = 15.0

_CONTENT_ACTION_FOR = {
    "click": "browser.click",
    "fill": "browser.fill",
    "select_option": "browser.select",
    "press": "browser.press",
    "hover": "browser.hover",
    "scroll": "browser.scroll",
}


class ExtensionChannelUnavailable(RuntimeError):
    """扩展通道当前不可用（未注册、未开启、未连接、被运行占用）。"""


class ExtensionCapabilityError(RuntimeError):
    """扩展通道做不到这件事。必须明确失败，不许悄悄换成另一种做法。"""


class ExtensionPageChannel:
    """一次扩展探索会话：观察版本、临时 ref 归属、动作与回读都挂在它身上。"""

    def __init__(self, executor: Any, context: Any, *, token: str) -> None:
        self._executor = executor
        self._context = context
        self.token = token
        self.version = 0
        self.last_url: str | None = None
        self._closed = False
        self.tab_id: int | None = None
        self.document_id: str | None = None
        self._idle_handle: asyncio.TimerHandle | None = None
        self._last_screenshot_at = 0.0

    @property
    def closed(self) -> bool:
        return self._closed

    def touch(self) -> None:
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        if not self.closed:
            self._idle_handle = asyncio.get_running_loop().call_later(
                _page_session.IDLE_TTL_SECONDS,
                lambda: asyncio.create_task(close_current("idle_timeout", token=self.token)),
            )

    async def close(self, reason: str = "explicit") -> str:
        if self._closed:
            return "already_closed"
        self._closed = True
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        try:
            if self.tab_id is not None:
                try:
                    await self._executor.page_action({"type": "page.end", "explorationTabId": self.tab_id})
                except (RuntimeError, ConnectionError):
                    # 标签页或连接消失仍须释放租约，不能让清理错误覆盖本轮结果。
                    pass
        finally:
            await self._executor.close_context(self._context)
        return reason

    async def _send(self, action: dict[str, Any], *, timeout: float = _ACTION_TIMEOUT_SECONDS) -> Any:
        if self._closed:
            raise ExtensionChannelUnavailable("扩展探索会话已结束")
        payload = {**action, "explorationTabId": self.tab_id}
        if self.document_id is not None and action["type"] != "page.observe":
            payload["documentId"] = self.document_id
        self.touch()
        try:
            return await self._executor.page_action(payload, timeout=timeout)
        finally:
            self.touch()

    async def observe(self, scope_selector: str | None) -> dict[str, Any]:
        """在扩展当前标签页上跑一次探测。版本号由这里递增，内容脚本据它作废旧 ref 表。"""
        self.version += 1
        result = await self._send(
            {"type": "page.observe", "scope": scope_selector, "observationVersion": self.version}
        )
        if not isinstance(result, dict):
            raise RuntimeError("扩展页面探测没有返回对象")
        document_id = result.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            raise RuntimeError("扩展页面探测缺少 document_id，请更新扩展")
        self.document_id = document_id
        result["observation_version"] = self.version
        result["tab_id"] = self.tab_id
        self.last_url = result.get("url") if isinstance(result.get("url"), str) else None
        return result

    async def effect_signature(self) -> dict[str, Any]:
        result = await self._send({"type": "page.effectSignature"})
        return result if isinstance(result, dict) else {}

    async def target_state(self, element_ref: str | None, selector: str | None) -> dict[str, Any]:
        action: dict[str, Any] = {"type": "page.targetState"}
        if element_ref is not None:
            action["ref"] = element_ref
            action["observationVersion"] = self.version
        elif selector is not None:
            action["selector"] = selector
        try:
            result = await self._send(action)
        except RuntimeError:
            # 目标消失只说明无法回读，不能据此判断业务失败。
            return {}
        return result if isinstance(result, dict) else {}

    async def resolve_target(
        self, element_ref: str | None, selector: str | None, observation_version: int | None
    ) -> tuple[str | None, dict[str, Any] | None]:
        """以临时 ref 固定目标，防止校验后重新解析 selector 命中另一元素。"""
        if element_ref is not None:
            want = self.version if observation_version is None else int(observation_version)
            if want != self.version:
                return None, stale_ref_error(
                    f"元素引用属于第 {want} 次观察，当前会话是第 {self.version} 次，元素可能已经换了位置"
                )
            try:
                await self._send(
                    {"type": "page.resolveTarget", "ref": element_ref, "observationVersion": want}
                )
            except RuntimeError as exc:
                # 导航或重渲染会使旧 ref 失效。
                return None, stale_ref_error(str(exc))
            return element_ref, None
        if not selector:
            return None, {"error": "必须提供 element_ref 或 selector"}
        result = await self._send({"type": "page.resolveTarget", "selector": selector})
        matches = int((result or {}).get("matches") or 0)
        if matches == 0:
            return None, element_not_found_error(selector)
        if matches > 1:
            return None, ambiguous_selector_error(selector, matches)
        resolved = (result or {}).get("element_ref")
        return (str(resolved) if resolved else None), None

    async def apply_action(
        self, action: str, element_ref: str | None, selector: str | None, value: str | None
    ) -> None:
        """把动作发到内容脚本。参数缺失等约束由内容脚本自己报错，不在这里重复一遍判据。"""
        content_type = _CONTENT_ACTION_FOR.get(action)
        if content_type is None:
            raise ExtensionCapabilityError(f"扩展通道不支持的动作：{action}")
        payload: dict[str, Any] = {"type": content_type}
        if element_ref is not None:
            payload["ref"] = element_ref
            payload["observationVersion"] = self.version
        elif selector is not None:
            payload["selector"] = selector
        if action == "scroll":
            payload["distance"] = int(value) if value and value.lstrip("-").isdigit() else 800
        elif value is not None:
            payload["inputValue"] = value
        await self._send(payload)

    async def wait_for(self, selector: str) -> dict[str, Any]:
        result = await self._send({"type": "page.waitFor", "selector": selector}, timeout=12.0)
        if not isinstance(result, dict) or result.get("status") not in {"satisfied", "timed_out"}:
            raise RuntimeError("扩展等待结果缺少有效状态")
        return {"status": result["status"], "selector": selector}

    async def screenshot(self) -> dict[str, Any]:
        remaining = 1.0 - (time.monotonic() - self._last_screenshot_at)
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last_screenshot_at = time.monotonic()
        result = await self._send({"type": "browser.screenshot"})
        if not isinstance(result, dict) or not result.get("dataUrl"):
            raise RuntimeError("扩展没有返回截图")
        return result

    def capability_report(self) -> dict[str, Any]:
        return {
            "channel": "extension",
            "observes": "会话开始时用户 Chrome 活动标签页的真实 DOM 与登录态",
            "frame_targeted_observation": False,
            "closed_shadow_dom": False,
            "notes": [
                "绑定开始时的标签页；用户切换标签页不会转移动作，关闭目标页后必须重新建立会话。",
                "仅观察主文档和 open Shadow DOM；不支持 frame_selector、tab_index 或 full_page 截图。",
                "截图与 DOM 是同一标签页的两次取证，不保证页面状态在两次取证之间不变。",
                "配方中的 Playwright 专用 selector 必须按扩展语法验证后才能保存运行。",
            ],
        }


_current: ExtensionPageChannel | None = None
_open_lock = asyncio.Lock()


def _live() -> ExtensionPageChannel | None:
    global _current
    if _current is not None and _current.closed:
        _current = None
    return _current


def get_channel(token: str | None = None) -> ExtensionPageChannel | None:
    """当前归属自己的通道；别人的通道在这里等于「没有通道」，与 page_session 同语义。"""
    channel = _live()
    if channel is None or channel.token != _page_session.current_owner(token):
        return None
    return channel


def foreign_channel(token: str | None = None) -> ExtensionPageChannel | None:
    channel = _live()
    if channel is None or channel.token == _page_session.current_owner(token):
        return None
    return channel


async def open_channel(executor: Any, *, token: str | None = None) -> ExtensionPageChannel:
    global _current
    mine = _page_session.current_owner(token)
    async with _open_lock:
        existing = _live()
        if existing is not None:
            if existing.token != mine:
                raise _page_session.SessionOwnershipError("扩展探索会话属于另一轮对话")
            existing.touch()
            return existing
        context = await executor.create_context(owner=CHANNEL_OWNER, manage_tabs=False)
        channel = ExtensionPageChannel(executor, context, token=mine)
        try:
            result = await executor.page_action({"type": "page.begin"})
            tab_id = result.get("tab_id") if isinstance(result, dict) else None
            if type(tab_id) is not int:
                raise RuntimeError("扩展未返回活动标签页身份，请更新扩展")
            channel.tab_id = tab_id
        except BaseException:
            await executor.close_context(context)
            raise
        _current = channel
        channel.touch()
        return channel


async def close_current(reason: str = "explicit", token: str | None = None) -> str:
    """关掉自己的通道。别人的通道返回 not_owner 而不是关掉它。"""
    global _current
    channel = _live()
    if channel is None:
        return "no_channel"
    if channel.token != _page_session.current_owner(token):
        return "not_owner"
    _current = None
    return await channel.close(reason)
