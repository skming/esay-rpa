from __future__ import annotations

import asyncio

import pytest

from app.services import browser_profile_lock
from app.services.browser_action_runner import BrowserActionRunner


@pytest.fixture(autouse=True)
def _clear_holders() -> None:
    browser_profile_lock._holders.clear()  # noqa: SLF001
    browser_profile_lock._serial_locks.clear()  # noqa: SLF001
    yield
    browser_profile_lock._holders.clear()  # noqa: SLF001
    browser_profile_lock._serial_locks.clear()  # noqa: SLF001


def test_second_owner_is_told_who_holds_the_profile(tmp_path) -> None:
    browser_profile_lock.acquire(str(tmp_path), "抓取帖子 · 运行 t_1")

    with pytest.raises(browser_profile_lock.BrowserProfileBusyError) as excinfo:
        browser_profile_lock.acquire(str(tmp_path), "元素拾取器")

    assert excinfo.value.holder == "抓取帖子 · 运行 t_1"
    # 报错必须点名占用方并给出去哪操作，否则用户只能靠猜关窗口
    assert "抓取帖子 · 运行 t_1" in str(excinfo.value)
    assert "确认并继续" in str(excinfo.value)
    assert "等待该运行完成" in str(excinfo.value)


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


async def test_acquire_exclusive_serializes_runs_sharing_one_dir(tmp_path) -> None:
    """同一目录的两个运行必须串行：后到的排队等前一个 release_exclusive，而不是像 acquire 那样撞上占用就抛错。"""
    order: list[str] = []
    await browser_profile_lock.acquire_exclusive(str(tmp_path), "运行 t_1")
    order.append("t_1 acquired")

    async def second() -> None:
        order.append("t_2 waiting")
        await browser_profile_lock.acquire_exclusive(str(tmp_path), "运行 t_2")
        order.append("t_2 acquired")

    task2 = asyncio.create_task(second())
    # 让出控制权，second 跑到 await acquire_exclusive 并阻塞在已被占用的目录锁上
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert order == ["t_1 acquired", "t_2 waiting"]
    assert not task2.done()
    assert browser_profile_lock.holder(str(tmp_path)) == "运行 t_1"

    browser_profile_lock.release_exclusive(str(tmp_path), "运行 t_1")
    await asyncio.wait_for(task2, timeout=1.0)

    assert order == ["t_1 acquired", "t_2 waiting", "t_2 acquired"]
    assert browser_profile_lock.holder(str(tmp_path)) == "运行 t_2"
    browser_profile_lock.release_exclusive(str(tmp_path), "运行 t_2")


async def test_acquire_exclusive_different_dirs_run_in_parallel(tmp_path) -> None:
    """不同 user-data-dir 不互相阻塞——串行只发生在共享同一目录时。"""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    await browser_profile_lock.acquire_exclusive(str(dir_a), "运行 t_1")
    await asyncio.wait_for(browser_profile_lock.acquire_exclusive(str(dir_b), "运行 t_2"), timeout=1.0)

    assert browser_profile_lock.holder(str(dir_a)) == "运行 t_1"
    assert browser_profile_lock.holder(str(dir_b)) == "运行 t_2"


async def test_acquire_exclusive_releases_lock_when_front_path_holds_profile(tmp_path) -> None:
    """前台路径（拾取器等）已直接 acquire 占着时，acquire_exclusive 排到锁却被记账挡下抛 Busy，
    且必须把刚拿的目录锁放掉——否则这把锁永久悬挂，后续同目录运行全被锁死。"""
    browser_profile_lock.acquire(str(tmp_path), "元素拾取器")

    with pytest.raises(browser_profile_lock.BrowserProfileBusyError):
        await browser_profile_lock.acquire_exclusive(str(tmp_path), "运行 t_1")

    assert browser_profile_lock.holder(str(tmp_path)) == "元素拾取器"
    assert not browser_profile_lock._serial_lock(str(tmp_path)).locked()  # noqa: SLF001
