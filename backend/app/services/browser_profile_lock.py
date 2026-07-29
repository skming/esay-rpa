"""同一个 Chrome user-data-dir 只能被一个进程打开，谁占着 profile 由这里单点记账。

Chrome 的 ProcessSingleton 遇到已被占用的 user-data-dir 时不会报错，而是把请求交给
已在运行的那个实例、自己直接退出；Playwright 看到的是子进程凭空消失，抛出的
"Target page, context or browser has been closed"，既不说 profile 被占，也不说被谁占。
用户拿到的是一屏浏览器启动参数，看不出该去关哪个窗口。

各处 launch_persistent_context 之前都按状态自查（"有没有 status == running 的任务"）行不通：
paused_for_human 的任务照样开着浏览器，拾取器、inspect_page 也都占着同一个 profile，
每加一种占用方就要去所有调用点补一次判断，漏一个就退化成上面那条天书。
所以改成占用方登记：谁开谁登记，拿不到就带着占用方是谁当场失败。
"""
from __future__ import annotations

from pathlib import Path

# 命中其一即认定「profile 被别的 Chrome 占着」——前两条是 Chrome 让位时打印的原文（中英文各一），
# 第三条是 Playwright 看到子进程退出后的说法，单独出现时也只可能是这个原因（其他失败会带自己的报错）
_BUSY_ERROR_MARKERS = (
    "opening in existing browser session",
    "正在现有的浏览器会话中打开",
    "target page, context or browser has been closed",
    "singletonlock",
)

_holders: dict[str, str] = {}


class BrowserProfileBusyError(RuntimeError):
    """profile 已被占用。holder 是占用方的可读描述，供调用方转成对用户的操作指引。"""

    def __init__(self, holder: str, message: str) -> None:
        super().__init__(message)
        self.holder = holder


def _key(profile_dir: str) -> str:
    return str(Path(profile_dir).expanduser().resolve())


def holder(profile_dir: str) -> str | None:
    return _holders.get(_key(profile_dir))


def acquire(profile_dir: str, owner: str) -> None:
    key = _key(profile_dir)
    current = _holders.get(key)
    if current is not None and current != owner:
        raise BrowserProfileBusyError(current, busy_message(current))
    _holders[key] = owner


def release(profile_dir: str, owner: str) -> None:
    """只有登记者本人能释放：抢不到锁的一方在 finally 里释放会把占用方的登记抹掉。"""
    key = _key(profile_dir)
    if _holders.get(key) == owner:
        _holders.pop(key, None)


def busy_message(holder_label: str) -> str:
    return (
        f"浏览器窗口正被「{holder_label}」占用，同一个浏览器用户目录只能被一个运行打开。"
        "如果它在等待人工接管，请在页面顶部的接管卡片点「已完成，继续」；"
        "否则先停止该运行，再重试。"
    )


def translate_launch_error(profile_dir: str, error: BaseException) -> str | None:
    """把 Chrome 让位导致的启动失败翻译成人话；不是这个原因则返回 None，由调用方原样抛出。

    走到这里说明进程内没有登记占用方，占用的多半是用户自己开着的、共用同一目录的浏览器窗口。
    """
    text = str(error).lower()
    if not any(marker in text for marker in _BUSY_ERROR_MARKERS):
        return None
    return (
        f"浏览器启动失败：用户目录 {profile_dir} 已被另一个浏览器进程占用。"
        "请关闭由本应用打开的浏览器窗口（或任何使用该目录的 Chrome）后重试。"
    )
