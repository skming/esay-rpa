"""按对话轮次持有浏览器探索会话，观察与截图复用交互后的页面状态。

空闲超时、关闭、失败和取消时释放 profile 锁。同一 profile 只允许一个会话；
其他轮次不得接管页面或关闭其浏览器。"""

from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from typing import Any, Callable

from app.services import browser_profile_lock

# 空闲上限：模型一轮工具调用之间通常几秒，180s 足够跨过一次模型思考；
# 再长就等于把用户的浏览器锁在助手手里。
IDLE_TTL_SECONDS = 180.0
_TICK_SECONDS = 15.0

# profile 锁的登记名：用户在助手探索期间点运行，看到的是这一行。
SESSION_OWNER = "AI 助手 · 浏览器探索会话"

# 归属挂在 contextvar 上而不是 execute() 的参数上：工具执行器的调用签名是模型工具契约的
# 一部分，为了传一个内部标记去改它，连 MockToolExecutor 和评测桩都得跟着改。
# 没有归属的直接调用（单元测试、脚本）共用这一个匿名归属，否则它们各自开一个会话，
# 互相都是「别人的」，teardown 关不掉自己刚开的浏览器。
ANONYMOUS_OWNER = "direct-call"
_owner: contextvars.ContextVar[str] = contextvars.ContextVar("rpa_page_session_owner")
# 创建期的串行化：guard 只保护「已经存在的会话」上的操作，两个并发的 open_session 会
# 同时通过「当前没有会话」的检查、各 acquire 一次锁、各开一个 browser context，
# 后写入的那个把前一个变成没人引用的孤儿进程——它不在登记表里，谁也关不掉。
_open_lock = asyncio.Lock()


def new_owner_token() -> str:
    return uuid.uuid4().hex[:12]


def set_owner(token: str) -> contextvars.Token[str]:
    return _owner.set(token)


def reset_owner(reset: contextvars.Token[str]) -> None:
    """归属复位。异步生成器的 aclose() 可能由另一个任务触发（事件循环回收被丢弃的生成器），
    那时的 Context 不是 set 时的那个，reset 会抛 ValueError，把真正的退出原因盖掉。
    """
    try:
        _owner.reset(reset)
    except ValueError:
        pass


def current_owner(token: str | None = None) -> str:
    return token or _owner.get(ANONYMOUS_OWNER)


class SessionExpiredError(RuntimeError):
    """会话已不存在（超时关闭、被显式关闭、或从未打开）。"""


class SessionOwnershipError(RuntimeError):
    """探索会话属于另一轮对话，不能接管。"""


class StaleRefError(RuntimeError):
    """元素引用来自更早的一次观察，页面已经变过，必须重新观察。"""


class PageSession:
    def __init__(
        self,
        *,
        profile: str,
        owner: str,
        context: Any,
        exit_context: Callable[[], Any],
        ttl: float = IDLE_TTL_SECONDS,
        token: str | None = None,
    ) -> None:
        self.profile = profile
        self.owner = owner
        self.token = current_owner(token)
        self.context = context
        self.version = 0
        self.opened_at = time.monotonic()
        self._exit = exit_context
        self._ttl = ttl
        self._last_used = time.monotonic()
        self._closed = False
        self._page: Any = None
        self._frame_selector: str | None = None
        self._watchdog: asyncio.Task | None = None

    def touch(self) -> None:
        self._last_used = time.monotonic()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_used

    def start_watchdog(self) -> None:
        if self._watchdog is None:
            self._watchdog = asyncio.create_task(self._watch())

    async def _watch(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(_TICK_SECONDS)
                if self._closed:
                    return
                if self.idle_seconds > self._ttl:
                    await self.close("idle_timeout")
                    return
        except asyncio.CancelledError:
            return

    async def close(self, reason: str = "explicit") -> str:
        if self._closed:
            return "already_closed"
        self._closed = True
        watchdog = self._watchdog
        self._watchdog = None
        # 看门狗自己触发的关闭不能取消自己：cancel 会在 await 处抛进这行的调用栈，
        # 浏览器就漏在这里不关了。
        if watchdog is not None and watchdog is not asyncio.current_task():
            watchdog.cancel()
        try:
            await self._exit()
        finally:
            browser_profile_lock.release(self.profile, self.owner)
        return reason

    def _require_open(self) -> None:
        if self._closed:
            raise SessionExpiredError("浏览器探索会话已关闭")

    async def page(self) -> Any:
        self._require_open()
        pages = [p for p in list(self.context.pages) if not _is_closed(p)]
        if self._page is None or self._page not in pages:
            # 点击弹出新标签页时旧页往往还在，所以只在当前页确实没了才自动跟随；
            # 想切标签页要显式调 switch_tab，否则模型会以为自己还在原来那页上操作。
            self._page = pages[-1] if pages else await self.context.new_page()
            self._frame_selector = None
        return self._page

    async def switch_tab(self, index: int) -> Any:
        self._require_open()
        pages = [p for p in list(self.context.pages) if not _is_closed(p)]
        if not pages:
            raise RuntimeError("当前会话没有打开的标签页")
        if index < 0 or index >= len(pages):
            raise RuntimeError(f"标签页序号 {index} 超出范围，当前共 {len(pages)} 个")
        self._page = pages[index]
        self._frame_selector = None
        return self._page

    async def switch_frame(self, selector: str | None) -> None:
        """selector=None 回到主文档。iframe 里的元素引用与主文档不通用，切换后必须重新观察。"""
        self._require_open()
        if selector is not None:
            await self._frame_for(await self.page(), selector)
        self._frame_selector = selector
        self.version += 1  # 换了文档，旧 ref 表不再属于当前目标

    @staticmethod
    async def _frame_for(page: Any, selector: str) -> Any:
        handle = await page.query_selector(selector)
        frame = await handle.content_frame() if handle is not None else None
        if frame is None:
            raise RuntimeError(f"selector {selector} 没有对应的 iframe")
        return frame

    async def target(self) -> Any:
        page = await self.page()
        if self._frame_selector is None:
            return page
        return await self._frame_for(page, self._frame_selector)

    @property
    def frame_selector(self) -> str | None:
        return self._frame_selector

    def tab_count(self) -> int:
        return len([p for p in list(self.context.pages) if not _is_closed(p)])

    async def observe(self, probe_js: str, scope_selector: str | None,
                      include_html: bool = False) -> dict[str, Any]:
        """在当前目标（页面或 iframe）上跑一次探测，并把这次的观察版本号写进结果。"""
        self._require_open()
        self.touch()
        self.version += 1
        target = await self.target()
        result = await target.evaluate(
            probe_js, {"scope": scope_selector, "version": self.version, "includeHtml": include_html}
        )
        if not isinstance(result, dict):
            raise RuntimeError("页面探测没有返回对象")
        result["observation_version"] = self.version
        if self._frame_selector is not None:
            result["frame_selector"] = self._frame_selector
        return result

    async def probe_version(self) -> int | None:
        """页面上真实存在的观察版本号；导航或刷新过就是 None——此刻的截图配不上任何 ref。"""
        self._require_open()
        target = await self.target()
        try:
            live = await target.evaluate("() => (window.__rpaProbe ? window.__rpaProbe.version : null)")
        except Exception:
            return None
        return None if live is None else int(live)

    async def element_for_ref(self, ref: str, version: int | None) -> Any:
        """把 ref 还原成元素句柄。ref 表随每次观察整表替换，所以版本不符就是页面已经变过。"""
        self._require_open()
        self.touch()
        target = await self.target()
        live = await target.evaluate("() => (window.__rpaProbe ? window.__rpaProbe.version : null)")
        if live is None:
            raise StaleRefError("当前页面上没有观察记录（可能已导航或刷新），请重新调用 inspect_page")
        want = self.version if version is None else version
        if int(live) != int(want) or int(live) != int(self.version):
            raise StaleRefError(
                f"元素引用属于第 {want} 次观察，页面当前是第 {live} 次，元素可能已经换了位置"
            )
        handle = await target.evaluate_handle(
            "(r) => { const reg = window.__rpaProbe; const i = Number(String(r).slice(1));"
            " return (reg && reg.els[i]) || null; }",
            ref,
        )
        element = handle.as_element()
        if element is None:
            raise StaleRefError(f"元素引用 {ref} 在页面上已经不存在，请重新观察")
        return element


def _is_closed(page: Any) -> bool:
    checker = getattr(page, "is_closed", None)
    try:
        return bool(checker()) if callable(checker) else False
    except Exception:
        return True


_current: PageSession | None = None
guard = asyncio.Lock()  # 同一时刻只允许一个工具调用操作会话，否则两次观察会互相覆盖 ref 表


def _live() -> PageSession | None:
    global _current
    if _current is not None and _current.closed:
        _current = None
    return _current


def get_session(token: str | None = None) -> PageSession | None:
    """当前归属自己的会话；别人的会话在这里等于「没有会话」。

    不按归属过滤的话，第二轮对话不带 url 调 inspect_page 就直接观察到第一轮打开的页面，
    还会 close 掉它——两轮各自都看不出异常，只是各自的页面莫名其妙换了内容。
    """
    session = _live()
    if session is None or session.token != current_owner(token):
        return None
    return session


def foreign_session(token: str | None = None) -> PageSession | None:
    """存在但不属于自己的会话。调用方据此给出明确的 busy，而不是当成「还没打开」。"""
    session = _live()
    if session is None or session.token == current_owner(token):
        return None
    return session


async def open_session(
    *,
    profile: str,
    owner: str,
    context_factory: Callable[..., Any],
    headless: bool = True,
    ttl: float = IDLE_TTL_SECONDS,
    token: str | None = None,
) -> PageSession:
    """打开（或复用自己的）探索会话。

    profile 被别的运行占着时抛 BrowserProfileBusyError；会话属于另一轮对话时抛
    SessionOwnershipError，都由调用方翻译成给用户看的话。
    """
    global _current
    mine = current_owner(token)
    async with _open_lock:
        existing = _live()
        if existing is not None:
            if existing.token != mine:
                raise SessionOwnershipError("浏览器探索会话属于另一轮对话")
            existing.touch()
            return existing

        browser_profile_lock.acquire(profile, owner)
        cm = context_factory(profile, headless=headless)
        try:
            context = await cm.__aenter__()
        except BaseException:
            browser_profile_lock.release(profile, owner)
            raise

        async def _exit() -> None:
            await cm.__aexit__(None, None, None)

        session = PageSession(
            profile=profile, owner=owner, context=context, exit_context=_exit, ttl=ttl, token=mine
        )
        session.start_watchdog()
        _current = session
        return session


async def close_current(reason: str = "explicit", token: str | None = None) -> str:
    """关掉自己的会话。别人的会话返回 not_owner 而不是关掉它。

    可重入：会话已关或已被空闲超时收走时返回 no_session / already_closed，不抛异常——
    正常结束、取消、异常三条退出路径会重叠走到这里。
    """
    from app.services.ai_tools.static_page_content import clear_static_snapshot

    clear_static_snapshot(token)
    global _current
    session = _live()
    if session is None:
        return "no_session"
    if session.token != current_owner(token):
        return "not_owner"
    _current = None
    return await session.close(reason)
