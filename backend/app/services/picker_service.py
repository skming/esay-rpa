"""请求绑定的元素拾取会话；两种通道共享页面拾取脚本，资源只由所属会话释放。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services import browser_profile_lock
from app.services.browser_action_runner import launch_persistent_chrome


class PickerOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: str = Field(min_length=1, max_length=120)
    mode: Literal["pick", "browse"] = "pick"
    browserExecutor: Literal["playwright", "extension"] = "playwright"
    targetUrl: str | None = None
    flowId: str | None = None
    nodeId: str | None = None
    field: Literal["selector", "targetSelector"] | None = None
    selectionMode: Literal["single", "multiple"] = "single"

    @model_validator(mode="after")
    def validate_target(self) -> "PickerOpenRequest":
        if self.mode == "pick" and not (self.flowId and self.nodeId and self.field):
            raise ValueError("拾取请求必须指定 flowId、nodeId 和 field")
        if self.targetUrl:
            self.targetUrl = self.targetUrl.strip()
            if not self.targetUrl.startswith(("http://", "https://")) or "${" in self.targetUrl:
                raise ValueError("targetUrl 必须是已解析的 http/https 地址")
        elif self.browserExecutor == "playwright":
            raise ValueError("Playwright 拾取必须指定 targetUrl")
        return self


class PickerCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: str = Field(min_length=1, max_length=120)


@dataclass
class _Session:
    request: PickerOpenRequest
    result: asyncio.Future
    context: Any = None
    page: Any = None
    playwright: Any = None
    extension: Any = None
    tab_id: int | None = None
    document_id: str | None = None
    waiter: asyncio.Task | None = None
    timeout: asyncio.TimerHandle | None = None
    released: bool = False
    profile_acquired: bool = False

    @property
    def owner(self) -> str:
        return f"元素拾取器 · {self.request.requestId}"


class PickerService:
    def __init__(self, session_dir: str, *, extension_provider: Callable[[], Any] | None = None) -> None:
        self._session_dir = session_dir
        self._extension_provider = extension_provider
        self._session: _Session | None = None
        self._lock = asyncio.Lock()

    async def open(self, request: PickerOpenRequest) -> dict[str, Any]:
        async with self._lock:
            if self._session is not None:
                if self._session.request.requestId == request.requestId:
                    raise ValueError("requestId 已使用，请为新拾取生成新的请求标识")
                await self._finish(self._session, {"type": "cancel", "reason": "replaced"})
            session = _Session(request, asyncio.get_running_loop().create_future())
            self._session = session
            try:
                if request.browserExecutor == "extension":
                    await self._open_extension(session)
                else:
                    await self._open_playwright(session)
                if request.mode == "pick":
                    session.timeout = asyncio.get_running_loop().call_later(
                        300, lambda: asyncio.create_task(self._complete(session, {"type": "error", "message": "拾取超时，请重新开始"})),
                    )
            except Exception as exc:
                await self._finish(session, {"type": "cancel", "reason": "open_failed"})
                if isinstance(exc, (RuntimeError, ConnectionError)):
                    raise
                translated = browser_profile_lock.translate_launch_error(self._session_dir, exc)
                raise RuntimeError(translated or str(exc)) from exc
            except asyncio.CancelledError:
                await self._finish(session, {"type": "cancel", "reason": "open_cancelled"})
                raise
            return {"status": "opened", "mode": request.mode, "requestId": request.requestId}

    async def _open_playwright(self, session: _Session) -> None:
        from playwright.async_api import async_playwright
        from app.services.picker_overlay_js import PICKER_OVERLAY_JS

        profile = Path(self._session_dir)
        profile.mkdir(parents=True, exist_ok=True)
        browser_profile_lock.acquire(str(profile), session.owner)
        session.profile_acquired = True
        session.playwright = await async_playwright().start()
        session.context = await launch_persistent_chrome(session.playwright, str(profile), headless=False)
        session.page = await session.context.new_page()
        await session.page.goto(session.request.targetUrl)
        session.page.on("close", lambda: asyncio.create_task(self._complete(session, {"type": "cancel", "reason": "page_closed"})))
        if session.request.mode == "browse":
            return
        await session.page.expose_function("__rpaPickerEvent__", lambda event: self._complete(session, event))
        await session.page.evaluate(PICKER_OVERLAY_JS, {
            "requestId": session.request.requestId, "selectionMode": session.request.selectionMode,
        })
        session.page.on("framenavigated", lambda frame: asyncio.create_task(self._complete(
            session, {"type": "cancel", "reason": "page_navigated"},
        )) if frame == session.page.main_frame else None)

    async def _open_extension(self, session: _Session) -> None:
        if self._extension_provider is None:
            raise RuntimeError("扩展拾取通道未配置")
        session.extension = self._extension_provider()
        session.context = await session.extension.create_context(owner=session.owner, manage_tabs=False)
        action: dict[str, Any] = {"type": "page.begin"}
        if session.request.targetUrl:
            action["targetUrl"] = session.request.targetUrl
        begun = await session.extension.page_action(action)
        if not isinstance(begun, dict) or type(begun.get("tab_id")) is not int:
            raise RuntimeError("扩展没有返回目标标签页身份，请更新扩展")
        session.tab_id = begun["tab_id"]
        if session.request.mode == "browse":
            # browse 只负责唤起页面，不能无限持有用户浏览器租约。
            await self._release(session)
            return
        observation = await session.extension.page_action({"type": "page.observe", "explorationTabId": session.tab_id})
        session.document_id = observation.get("document_id") if isinstance(observation, dict) else None
        if not session.document_id:
            raise RuntimeError("扩展没有返回文档身份，请更新扩展")
        session.waiter = asyncio.create_task(self._wait_extension(session))

    async def _wait_extension(self, session: _Session) -> None:
        try:
            event = await session.extension.page_action({
                "type": "page.picker", "explorationTabId": session.tab_id,
                "documentId": session.document_id, "pickerRequestId": session.request.requestId,
                "selectionMode": session.request.selectionMode,
            }, timeout=300)
            await self._complete(session, event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._complete(session, {"type": "error", "message": str(exc)})

    async def wait_for_result(self, request_id: str) -> dict[str, Any]:
        session = self._session
        if session is None or session.request.requestId != request_id:
            raise ValueError("拾取请求已结束或不存在")
        return await asyncio.shield(session.result)

    async def close(self, request_id: str | None = None) -> None:
        async with self._lock:
            session = self._session
            if session is not None and (request_id is None or session.request.requestId == request_id):
                await self._finish(session, {"type": "cancel", "reason": "closed"})

    async def _complete(self, session: _Session, event: Any) -> None:
        # 页面 expose_function 必须先返回；在回调内关 context 会打断回执。
        if session is not self._session or session.result.done():
            return
        asyncio.create_task(self._complete_locked(session, event))

    async def _complete_locked(self, session: _Session, event: Any) -> None:
        async with self._lock:
            if session is self._session:
                await self._finish(session, event)

    async def _finish(self, session: _Session, event: Any) -> None:
        if session.result.done():
            return
        payload = self._event(session, event)
        try:
            await self._release(session)
        except Exception as exc:
            payload = self._event(session, {"type": "error", "message": f"拾取会话清理失败：{exc}"})
        session.result.set_result(payload)

    @staticmethod
    def _event(session: _Session, event: Any) -> dict[str, Any]:
        request = session.request
        result = {key: getattr(request, key) for key in ("requestId", "flowId", "nodeId", "field", "browserExecutor")}
        if not isinstance(event, dict) or event.get("type") not in {"capture", "cancel", "error"}:
            event = {"type": "error", "message": "拾取器返回了无效结果"}
        if event.get("requestId", request.requestId) != request.requestId:
            event = {"type": "error", "message": "拾取结果不属于当前请求"}
        result["type"] = event["type"]
        if event["type"] == "capture":
            matches = event.get("matches")
            if not (isinstance(event.get("selector"), str) and event["selector"].strip()
                    and type(matches) is int and matches > 0 and event.get("selectedIncluded") is True
                    and (request.selectionMode == "multiple" or matches == 1)):
                return {**result, "type": "error", "message": "拾取结果未通过定位校验"}
            result.update({key: event.get(key) for key in ("selector", "matches", "selectedIncluded", "usesPosition", "text", "url")})
            result.update(strategy="css", capturedAt=datetime.now(UTC).isoformat(), documentId=session.document_id, tabId=session.tab_id)
        else:
            result.update({key: event[key] for key in ("message", "reason", "code") if key in event})
        return result

    async def _release(self, session: _Session) -> None:
        if session.released:
            return
        session.released = True
        if session.timeout:
            session.timeout.cancel()
        if session.waiter and session.waiter is not asyncio.current_task():
            session.waiter.cancel()
        try:
            if session.extension is not None:
                try:
                    if session.tab_id is not None:
                        if session.request.mode == "pick":
                            try:
                                await session.extension.page_action({"type": "page.pickerCancel", "explorationTabId": session.tab_id,
                                                                     "pickerRequestId": session.request.requestId}, timeout=3)
                            except Exception:
                                pass
                        await session.extension.page_action({"type": "page.end", "explorationTabId": session.tab_id}, timeout=3)
                except Exception:
                    pass
                finally:
                    await session.extension.close_context(session.context)
            else:
                if session.context is not None:
                    try:
                        await session.context.close()
                    except Exception:
                        pass
        finally:
            try:
                if session.playwright is not None:
                    await session.playwright.stop()
            finally:
                if session.profile_acquired:
                    browser_profile_lock.release(self._session_dir, session.owner)
