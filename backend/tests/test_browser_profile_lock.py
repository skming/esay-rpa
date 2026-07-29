from __future__ import annotations

import pytest

from app.services import browser_profile_lock
from app.services.browser_action_runner import BrowserActionRunner


@pytest.fixture(autouse=True)
def _clear_holders() -> None:
    browser_profile_lock._holders.clear()  # noqa: SLF001
    yield
    browser_profile_lock._holders.clear()  # noqa: SLF001


def test_second_owner_is_told_who_holds_the_profile(tmp_path) -> None:
    browser_profile_lock.acquire(str(tmp_path), "抓取帖子 · 运行 t_1")

    with pytest.raises(browser_profile_lock.BrowserProfileBusyError) as excinfo:
        browser_profile_lock.acquire(str(tmp_path), "元素拾取器")

    assert excinfo.value.holder == "抓取帖子 · 运行 t_1"
    # 报错必须点名占用方并给出去哪操作，否则用户只能靠猜关窗口
    assert "抓取帖子 · 运行 t_1" in str(excinfo.value)
    assert "已完成，继续" in str(excinfo.value)


def test_same_owner_can_reacquire(tmp_path) -> None:
    browser_profile_lock.acquire(str(tmp_path), "运行 t_1")
    browser_profile_lock.acquire(str(tmp_path), "运行 t_1")
    assert browser_profile_lock.holder(str(tmp_path)) == "运行 t_1"


def test_release_by_non_owner_keeps_the_registration(tmp_path) -> None:
    """抢锁失败的一方在 finally 里释放，不能把真正占用方的登记抹掉——
    否则第三方会拿到「空闲」的假象，直接撞上 Chrome 让位。"""
    browser_profile_lock.acquire(str(tmp_path), "运行 t_1")

    browser_profile_lock.release(str(tmp_path), "元素拾取器")

    assert browser_profile_lock.holder(str(tmp_path)) == "运行 t_1"


def test_release_by_owner_frees_the_profile(tmp_path) -> None:
    browser_profile_lock.acquire(str(tmp_path), "运行 t_1")
    browser_profile_lock.release(str(tmp_path), "运行 t_1")
    assert browser_profile_lock.holder(str(tmp_path)) is None


def test_translate_launch_error_only_claims_busy_for_chrome_handoff(tmp_path) -> None:
    handoff = RuntimeError(
        "BrowserType.launch_persistent_context: Target page, context or browser has been closed\n"
        "[pid=39907][out] 正在现有的浏览器会话中打开。"
    )
    assert "已被另一个浏览器进程占用" in (browser_profile_lock.translate_launch_error(str(tmp_path), handoff) or "")

    # 其他失败原样抛出：翻译成「被占用」会把真实原因（如缺少内核）盖掉
    assert browser_profile_lock.translate_launch_error(str(tmp_path), RuntimeError("Executable doesn't exist")) is None


async def test_create_context_refuses_before_launching_when_profile_is_busy(tmp_path) -> None:
    """判在拉起浏览器之前：等到 Chrome 让位再翻译，已经多了一个要善后的进程。"""
    browser_profile_lock.acquire(str(tmp_path), "抓取帖子 · 运行 t_1")

    with pytest.raises(browser_profile_lock.BrowserProfileBusyError):
        await BrowserActionRunner(session_dir=str(tmp_path)).create_context(headless=True, owner="运行 t_2")

    assert browser_profile_lock.holder(str(tmp_path)) == "抓取帖子 · 运行 t_1"
