"""浏览器探索会话的归属、生命周期与元素引用时效。

会话占着 browser profile 的进程内互斥锁，所以「一定会消失」和「过期引用一定被拒」是两条
不能靠人记得调用的性质：漏掉前者，用户点运行只会看到「被 AI 助手占用」；漏掉后者，
流程会拿上一次观察的 ref 去点这一次的页面，点到的是另一个元素，而且不报错。

归属是第三条：聊天流与自愈流共用同一个 orchestrator 实例，不按归属隔离时，第二轮会观察
第一轮打开的页面、并在结束时把它关掉，两轮都不报错，只是各自看到自己没做过的页面变化。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core import storage
from app.services import browser_profile_lock
from app.services.ai_orchestrator import AiOrchestrator
from app.services.ai_tools import page_session
from app.services.ai_tools.executor import RpaToolExecutor
from app.services.ai_tools.page_observation import EFFECT_SIGNATURE_JS, TARGET_STATE_JS

PROFILE = "/tmp/easy-rpa-test-profile"


class FakeElement:
    def __init__(self, page: "FakePage") -> None:
        self._page = page

    def as_element(self) -> "FakeElement":
        return self

    async def click(self, **_: Any) -> None:
        self._page.clicks += 1

    async def hover(self, **_: Any) -> None:
        return None

    async def input_value(self) -> str:
        return self._page.value

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        # 动作前后的目标状态回读。假元素不会因为 click 改变自己的状态，于是「点了个死元素」
        # 在这里得到的是空 state_diff——正是 no_observable_change 这条判据要落在的输入。
        if script == TARGET_STATE_JS:
            return {"tag": "div", "focused": False, "value": self._page.value,
                    "scroll": {"top": 0, "left": 0, "max": 0}}
        return None

    async def content_frame(self) -> "FakePage | None":
        return self._page.frame


class FakePage:
    """只实现探索路径真正调用到的那几个方法，其余一律不实现——多写一个假方法，
    就多一处「测试里通、真浏览器上不通」的机会。"""

    def __init__(self, *, matches: int = 1, effect_changes: bool = False) -> None:
        self.url = "https://example.test/list"
        self.clicks = 0
        self.value = ""
        self.matches = matches
        self.effect_changes = effect_changes
        self.probe_version: int | None = None
        self.frame: "FakePage | None" = None
        self._effect_calls = 0

    def is_closed(self) -> bool:
        return False

    async def title(self) -> str:
        return "列表页"

    async def query_selector_all(self, _selector: str) -> list[FakeElement]:
        return [FakeElement(self) for _ in range(self.matches)]

    async def query_selector(self, _selector: str) -> FakeElement | None:
        return FakeElement(self)

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        if script == EFFECT_SIGNATURE_JS:
            self._effect_calls += 1
            layers = 1 if (self.effect_changes and self._effect_calls > 1) else 0
            return {"url": self.url, "elements": 100, "options": 3, "layers": layers, "active": None}
        if script == TARGET_STATE_JS:
            # 整页滚动没有元素可回读，执行器把同一段脚本发给 page
            return {"tag": "body", "focused": False, "scroll": {"top": 0, "left": 0, "max": 0}}
        if arg is None:
            return self.probe_version  # 只读版本号那次调用不带参数
        version = arg.get("version")
        self.probe_version = version
        return {"url": self.url, "title": "列表页", "inputs": [], "all_classes": [], "buttons": []}

    async def evaluate_handle(self, _script: str, _ref: str) -> FakeElement:
        return FakeElement(self)


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.pages = [page]

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page


def _make_session(page: FakePage, *, ttl: float = 60.0) -> page_session.PageSession:
    exited: list[bool] = []

    async def _exit() -> None:
        exited.append(True)

    browser_profile_lock.acquire(PROFILE, page_session.SESSION_OWNER)
    session = page_session.PageSession(
        profile=PROFILE,
        owner=page_session.SESSION_OWNER,
        context=FakeContext(page),
        exit_context=_exit,
        ttl=ttl,
    )
    session.exited = exited  # type: ignore[attr-defined]
    return session


@pytest.fixture(autouse=True)
async def _no_leaked_session() -> Any:
    yield
    leftover = page_session._current
    if leftover is not None:
        # 归属化之后 close_current 不再关别人的会话，teardown 必须点名关：
        # 漏在这里的会话会占着 profile 锁，把后面每个用例都变成「浏览器被占用」。
        await page_session.close_current("test_cleanup", token=leftover.token)
    browser_profile_lock.release(PROFILE, page_session.SESSION_OWNER)


async def test_idle_timeout_closes_the_session_and_releases_the_profile_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空闲超时必须自己释放锁。模型这一轮不再调工具时没有任何代码会来关它，
    唯一的兜底就是看门狗；它不生效，用户下一次手动运行就被自己的助手挡住。"""
    monkeypatch.setattr(page_session, "_TICK_SECONDS", 0.01)
    session = _make_session(FakePage(), ttl=0.0)
    session.start_watchdog()

    for _ in range(200):
        if session.closed:
            break
        await asyncio.sleep(0.01)

    assert session.closed
    assert session.exited == [True]  # type: ignore[attr-defined]
    assert browser_profile_lock.holder(PROFILE) is None


async def test_close_is_idempotent_and_releases_only_once() -> None:
    session = _make_session(FakePage())
    assert await session.close("explicit") == "explicit"
    assert await session.close("explicit") == "already_closed"
    assert session.exited == [True]  # type: ignore[attr-defined]


async def test_a_new_observation_invalidates_the_previous_refs() -> None:
    """ref 表每次观察整表替换，e3 在下一次观察里是另一个元素。

    不按版本拒绝，模型「上一次看到的第 3 个按钮」会点到这一次页面的第 3 个按钮上，
    Playwright 照样返回成功——错的成功比失败难查。
    """
    page = FakePage()
    session = _make_session(page)

    await session.observe("() => ({})", None)
    assert await session.element_for_ref("e3", 1) is not None

    await session.observe("() => ({})", None)
    with pytest.raises(page_session.StaleRefError):
        await session.element_for_ref("e3", 1)


async def test_ref_is_rejected_when_the_page_lost_its_probe() -> None:
    """导航或刷新后页面上不再有 __rpaProbe：此刻任何 ref 都无从还原。"""
    page = FakePage()
    session = _make_session(page)
    await session.observe("() => ({})", None)

    page.probe_version = None  # 相当于页面已经跳转/刷新
    assert await session.probe_version() is None
    with pytest.raises(page_session.StaleRefError):
        await session.element_for_ref("e1", 1)


async def test_switching_into_an_iframe_invalidates_main_document_refs() -> None:
    """iframe 与主文档各有自己的引用表，切进去必须重新观察。

    不换表就会拿主文档的 e1 去 iframe 里还原：ref 编号在两个文档里都存在，
    还原得到的却是完全不相干的元素。
    """
    page = FakePage()
    page.frame = FakePage()
    session = _make_session(page)
    await session.observe("() => ({})", None)

    await session.switch_frame("#pay-frame")
    assert session.frame_selector == "#pay-frame"
    assert await session.target() is page.frame
    with pytest.raises(page_session.StaleRefError):
        await session.element_for_ref("e1", 1)


def _executor() -> RpaToolExecutor:
    return RpaToolExecutor(flow_service=None, task_manager=None)  # type: ignore[arg-type]


async def test_interact_page_without_a_session_asks_for_a_url() -> None:
    """没有会话时不能自己开一个：模型以为在操作刚看过的页面，实际是一张空白新页。"""
    result = await _executor().execute("interact_page", {"action": "click", "selector": "#q"})
    assert result["required_action"] == "call_inspect_page_with_url"


async def test_interact_page_refuses_an_ambiguous_selector() -> None:
    """同一页面上「查询」按钮往往有两三个。取第一个在探索阶段看着能过，
    写进流程后点的是另一行的按钮，运行结果还是绿的。"""
    page = FakePage(matches=3)
    page_session._current = _make_session(page)

    result = await _executor().execute("interact_page", {"action": "click", "selector": "button.query"})

    assert result["status"] == "ambiguous_selector"
    assert result["matches"] == 3
    assert result["required_action"] == "narrow_selector_or_use_element_ref"
    assert page.clicks == 0  # 拒绝就必须真的没点


async def test_interact_page_flags_an_action_that_changed_nothing() -> None:
    """点了个没绑事件的元素不会报错。没有证据就必须说出来，否则模型接着照「面板已打开」建流程。

    这里的结论只能是「没观察到变化」，不能是「操作失败，换目标」：变化也可能落在观测不到的
    地方。两者的处置不同——后者会让模型丢掉一次其实成功了的操作。
    """
    page_session._current = _make_session(FakePage(effect_changes=False))

    result = await _executor().execute("interact_page", {"action": "click", "selector": "#trigger"})

    assert result["status"] == "ok"
    assert result["effect"]["changed"] is False
    assert result["action_effect"]["status"] == "no_observable_change"
    assert "warning" in result
    assert "business_check" not in result  # 没有证据就不能给出「业务未验证」这种像是成功的反馈


async def test_interact_page_reobserves_after_a_real_change() -> None:
    """交互后自动重新观察：下拉面板、日历格这些 DOM 只在操作之后才存在。"""
    page = FakePage(effect_changes=True)
    page_session._current = _make_session(page)

    result = await _executor().execute("interact_page", {"action": "click", "selector": "#trigger"})

    assert result["effect"]["changed"] is True
    assert result["action_effect"]["status"] == "state_changed"
    assert "warning" not in result
    # 页面变了也只到「动作被接收」，业务后置条件仍未验证；少了这句，模型会拿面板打开当筛选生效
    assert "business_check" in result
    assert result["observation"]["observation_version"] == 1
    assert page.clicks == 1


class _FakeContextManager:
    def __init__(self, factory: "RecordingFactory") -> None:
        self._factory = factory

    async def __aenter__(self) -> FakeContext:
        context = FakeContext(FakePage())
        self._factory.opened.append(context)
        return context

    async def __aexit__(self, *_: Any) -> None:
        self._factory.exited += 1


class RecordingFactory:
    """假的 browser context 工厂，记录开了几次、关了几次。

    并发缺陷只能从这里看出来：两次并发 open_session 各开一个 context，真浏览器上是
    两个进程，其中一个不在登记表里也没人引用，谁都关不掉它。
    """

    def __init__(self) -> None:
        self.opened: list[FakeContext] = []
        self.exited = 0

    def __call__(self, _profile: str, *, headless: bool = True) -> _FakeContextManager:
        return _FakeContextManager(self)


def _real_profile() -> str:
    """用产品真正解析出的 profile 目录：_profile_busy_block 查的是这个键，
    换成别的路径就绕过了「占用方是不是自己」那段判断，等于没测。"""
    return str(storage.resolve_browser_profile_dir())


async def _open(
    factory: RecordingFactory, token: str | None = None, *, ttl: float = 60.0
) -> page_session.PageSession:
    return await page_session.open_session(
        profile=_real_profile(),
        owner=page_session.SESSION_OWNER,
        context_factory=factory,
        headless=True,
        ttl=ttl,
        token=token,
    )


async def test_another_turn_can_neither_reuse_nor_close_this_turns_session() -> None:
    """审查复现的两条：省略参数再 open 拿到同一个实例；另一轮 close 把这一轮也关了。"""
    factory = RecordingFactory()
    mine = await _open(factory, "turn-a")

    with pytest.raises(page_session.SessionOwnershipError):
        await _open(factory, "turn-b")

    assert page_session.get_session("turn-b") is None  # 别人的会话等于「没有会话」
    assert page_session.foreign_session("turn-b") is mine
    assert await page_session.close_current("turn_end", token="turn-b") == "not_owner"
    assert mine.closed is False
    assert len(factory.opened) == 1

    assert await page_session.close_current("turn_end", token="turn-a") == "turn_end"
    assert mine.closed is True
    # 可重入：正常结束、取消、异常三条退出路径会重叠走到这里
    assert await page_session.close_current("turn_end", token="turn-a") == "no_session"
    assert factory.exited == 1
    assert browser_profile_lock.holder(_real_profile()) is None


async def test_two_turns_opening_at_the_same_time_leave_exactly_one_browser() -> None:
    """并发 open：原来两边都通过「当前没有会话」的检查，各开一个 context。"""
    factory = RecordingFactory()
    results = await asyncio.gather(
        _open(factory, "turn-a"), _open(factory, "turn-b"), return_exceptions=True
    )

    sessions = [r for r in results if isinstance(r, page_session.PageSession)]
    refused = [r for r in results if isinstance(r, page_session.SessionOwnershipError)]
    assert len(sessions) == 1 and len(refused) == 1
    assert len(factory.opened) == 1
    assert browser_profile_lock.holder(_real_profile()) == page_session.SESSION_OWNER


async def test_same_turn_opening_twice_reuses_its_own_session() -> None:
    factory = RecordingFactory()
    first, second = await asyncio.gather(_open(factory, "turn-a"), _open(factory, "turn-a"))
    assert first is second
    assert len(factory.opened) == 1


async def test_the_three_page_tools_report_busy_instead_of_taking_over() -> None:
    """走真实工具分派：三个页面工具都必须给出「属于另一轮」，而不是「还没打开页面」。

    错报成后者，模型会照着 required_action 再调一次 inspect_page(url=...)，拿到同一个拒绝，
    于是换 url、换工具反复试——用户看到的是助手在原地打转。
    """
    factory = RecordingFactory()
    other = await _open(factory, "turn-a")

    reset = page_session.set_owner("turn-b")
    try:
        executor = _executor()
        calls = (
            ("inspect_page", {}),
            ("interact_page", {"action": "click", "selector": "#q"}),
            ("inspect_screenshot", {}),
        )
        for name, args in calls:
            result = await executor.execute(name, args)
            assert result.get("status") == "blocked_page_session_busy", (name, result)
            assert result.get("required_action") != "call_inspect_page_with_url", name
            assert "另一轮对话" in result["error"], name
    finally:
        page_session.reset_owner(reset)

    assert other.closed is False  # 三次调用都不许碰别人的会话
    assert factory.exited == 0


async def test_idle_timeout_frees_the_slot_for_the_next_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超时收走的会话不能继续占着归属：否则下一轮永远拿到「属于另一轮」，
    而那一轮早就没有浏览器了，用户只能重启服务。"""
    monkeypatch.setattr(page_session, "_TICK_SECONDS", 0.01)
    factory = RecordingFactory()
    stale = await _open(factory, "turn-a", ttl=0.0)

    for _ in range(200):
        if stale.closed:
            break
        await asyncio.sleep(0.01)
    assert stale.closed

    assert await page_session.close_current("turn_end", token="turn-a") == "no_session"
    fresh = await _open(factory, "turn-b")
    assert fresh is not stale
    assert len(factory.opened) == 2


def _orchestrator_with_inner(
    monkeypatch: pytest.MonkeyPatch, inner: Any
) -> AiOrchestrator:
    """真实的 stream 入口 + 桩化的模型循环：归属的建立与释放都写在 stream 里，
    模型循环只是它包着的那段。不桩化就得给测试配一个真模型。"""
    orchestrator = AiOrchestrator(_executor())
    monkeypatch.setattr(orchestrator, "_stream_inner", inner)
    return orchestrator


async def test_a_turn_releases_the_session_it_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """本轮开的会话在本轮结束时一定关掉：留着就让用户手动点运行被自己的助手挡住。"""
    factory = RecordingFactory()
    opened: list[page_session.PageSession] = []

    async def _inner(*_a: Any, **_k: Any) -> Any:
        opened.append(await _open(factory))  # 不传 token：归属取 stream 刚设好的那个
        yield {"type": "text", "text": "看完了"}

    events = [event async for event in _orchestrator_with_inner(monkeypatch, _inner).stream([], "m")]

    assert [e["type"] for e in events] == ["text"]
    assert opened[0].closed is True
    assert factory.exited == 1
    assert browser_profile_lock.holder(_real_profile()) is None


async def test_a_turn_does_not_touch_another_turns_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """审查复现的那条：另一轮结束时把第一轮的会话也关了。"""
    factory = RecordingFactory()
    other = await _open(factory, "turn-a")
    refused: list[bool] = []

    async def _inner(*_a: Any, **_k: Any) -> Any:
        # 省略 token 也不能复用别人的会话
        try:
            await _open(factory)
        except page_session.SessionOwnershipError:
            refused.append(True)
        yield {"type": "text", "text": "浏览器被另一轮占着"}

    async for _ in _orchestrator_with_inner(monkeypatch, _inner).stream([], "m"):
        pass

    assert refused == [True]
    assert other.closed is False
    assert page_session.get_session("turn-a") is other
    assert factory.exited == 0


async def test_a_failed_turn_still_releases_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = RecordingFactory()
    opened: list[page_session.PageSession] = []

    async def _inner(*_a: Any, **_k: Any) -> Any:
        opened.append(await _open(factory))
        yield {"type": "text", "text": "开始看页面"}
        raise RuntimeError("模型调用炸了")

    with pytest.raises(RuntimeError):
        async for _ in _orchestrator_with_inner(monkeypatch, _inner).stream([], "m"):
            pass

    assert opened[0].closed is True
    assert browser_profile_lock.holder(_real_profile()) is None


async def test_a_cancelled_turn_still_releases_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前端断开连接是最常走到的退出路径：generator 被 aclose，没有任何代码再来关会话。"""
    factory = RecordingFactory()
    opened: list[page_session.PageSession] = []

    async def _inner(*_a: Any, **_k: Any) -> Any:
        opened.append(await _open(factory))
        yield {"type": "text", "text": "第一段"}
        yield {"type": "text", "text": "永远发不出去的第二段"}

    stream = _orchestrator_with_inner(monkeypatch, _inner).stream([], "m")
    assert (await stream.__anext__())["type"] == "text"
    await stream.aclose()

    assert opened[0].closed is True
    assert factory.exited == 1
    assert browser_profile_lock.holder(_real_profile()) is None


async def test_interaction_wait_timeout_returns_observation_in_the_selected_frame() -> None:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    page = FakePage()
    page.frame = FakePage()
    calls = []

    async def timeout(selector, **kwargs):
        calls.append(selector)
        raise PlaywrightTimeoutError("fixture timeout")

    page.frame.wait_for_selector = timeout
    session = _make_session(page)
    page_session._current = session
    await session.switch_frame("iframe")
    result = await _executor().execute("interact_page", {
        "action": "click", "selector": "#trigger", "wait_selector": "#panel",
    })
    assert calls == ["#panel"]
    assert result["status"] == "ok"
    assert result["wait_result"] == {"status": "timed_out", "selector": "#panel"}
    assert "observation_version" in result["observation"]
    assert page.frame.clicks == 1


async def test_interaction_wait_does_not_swallow_invalid_selector() -> None:
    page = FakePage()

    async def invalid(*args, **kwargs):
        raise ValueError("invalid selector fixture")

    page.wait_for_selector = invalid
    page_session._current = _make_session(page)
    result = await _executor().execute("interact_page", {
        "action": "click", "selector": "#trigger", "wait_selector": "[",
    })
    assert "invalid selector fixture" in result["error"]
    assert result.get("status") != "ok"


async def test_interaction_explicit_zero_wait_is_preserved() -> None:
    page = FakePage()
    waits = []

    async def record(ms):
        waits.append(ms)

    page.wait_for_timeout = record
    page_session._current = _make_session(page)
    await _executor().execute("interact_page", {"action": "click", "selector": "#trigger", "wait_ms": 0})
    assert waits == [0]


@pytest.mark.parametrize("reply,status", [(None, "satisfied"), ({"status": "timed_out"}, "timed_out"),
                                         (TimeoutError("transport fixture"), "unknown")])
async def test_wait_distinguishes_page_evidence_from_transport_timeout(reply, status):
    from app.services.ai_tools.executor import _observe_wait

    async def wait():
        if isinstance(reply, Exception):
            raise reply
        return reply

    assert await _observe_wait(wait(), "#panel") == {"status": status, "selector": "#panel"}


async def test_cancelled_wait_propagates():
    from app.services.ai_tools.executor import _observe_wait

    async def wait():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _observe_wait(wait(), "#panel")


async def test_driver_timeout_outside_playwrights_class_is_still_evidence():
    """持久化上下文首选 patchright 分支，它的 TimeoutError 与 playwright 的类无继承关系；
    漏判会让整次观察被 `_inspect_page_via_browser` 的兜底吞成一句超时。"""
    from app.services.ai_tools.executor import _observe_wait

    async def wait():
        raise type("TimeoutError", (Exception,), {})("patchright fixture")

    assert await _observe_wait(wait(), "#panel") == {"status": "timed_out", "selector": "#panel"}


async def test_non_timeout_driver_error_propagates():
    from app.services.ai_tools.executor import _observe_wait

    async def wait():
        raise type("Error", (Exception,), {})("selector 语法无法解析")

    with pytest.raises(Exception, match="selector 语法无法解析"):
        await _observe_wait(wait(), "#panel")
