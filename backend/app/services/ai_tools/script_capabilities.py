"""脚本节点能产出哪些文件格式，只在这里说一次。

同一份清单既写进 list_node_types 给模型看，又用来在 lint 里拦——两处分开写，
迟早出现「清单说支持、拦截规则不知道」或者反过来，模型撞上一条它无从预料的墙。

可用与否不写死：script.python 跑的是后端自己的解释器（script_action_runner
用 sys.executable 起子进程），装没装 import 一下就知道。往 pyproject 里加一个库，
清单和放行范围同时生效，不必再回来改这个文件。
"""
from __future__ import annotations

from functools import lru_cache
from importlib.util import find_spec

# 扩展名 → (能生成它的候选库, 人话名字)。任一候选库装上了，该格式即视为可用。
# 只收「不靠库就写不对」的格式：.csv/.json/.md/.txt/.html 标准库能写，不在此列。
_FORMAT_LIBRARIES: dict[str, tuple[tuple[str, ...], str]] = {
    ".xlsx": (("openpyxl", "xlsxwriter", "pandas"), "Excel 工作簿"),
    ".pdf": (("reportlab", "fpdf", "weasyprint", "pypdf"), "PDF 文档"),
    ".docx": (("docx",), "Word 文档"),
    ".pptx": (("pptx",), "PowerPoint 演示文稿"),
}

# 这些格式不靠库也能写对，任何时候都放行
_STDLIB_FORMATS = (".csv", ".json", ".jsonl", ".md", ".txt", ".html", ".xml", ".zip")

# import 名与安装名不一致的，提示里要给能直接拿去装的那个
_PIP_NAMES = {"docx": "python-docx", "pptx": "python-pptx"}


@lru_cache(maxsize=1)
def _installed() -> frozenset[str]:
    """当前解释器里真正 import 得到的库。

    find_spec 只查找不执行，装了一堆重库也不会拖慢 lint；结果缓存一次，
    因为进程运行期间不会有人往 venv 里装东西。
    """
    names: set[str] = set()
    for candidates, _label in _FORMAT_LIBRARIES.values():
        for name in candidates:
            try:
                if find_spec(name) is not None:
                    names.add(name)
            except (ImportError, ValueError):
                continue
    return frozenset(names)


def library_for_format(suffix: str) -> str | None:
    """该格式当前可用的库名；没有可用的返回 None。"""
    candidates, _label = _FORMAT_LIBRARIES.get(suffix.lower(), ((), ""))
    return next((name for name in candidates if name in _installed()), None)


def unsupported_formats() -> tuple[str, ...]:
    return tuple(sorted(fmt for fmt in _FORMAT_LIBRARIES if library_for_format(fmt) is None))


def missing_library_hint(suffix: str) -> str:
    """这个格式缺什么库，按候选顺序报第一个——给用户装的时候有个明确的名字。"""
    candidates, _label = _FORMAT_LIBRARIES.get(suffix.lower(), ((), ""))
    if not candidates:
        return "对应的第三方库"
    return _PIP_NAMES.get(candidates[0], candidates[0])


def describe_script_capabilities() -> str:
    """写进 script.python 的节点说明：能做什么、不能做什么，都点名到格式。

    只说「可以执行 Python」等于没说边界，模型只能靠猜；猜错的代价不是报错而是
    交付一个打不开的文件——手搓的 PDF 字节流照样 success。
    """
    supported = [
        f"{fmt}（{label}，用 {library_for_format(fmt)}）"
        for fmt, (_candidates, label) in _FORMAT_LIBRARIES.items()
        if library_for_format(fmt) is not None
    ]
    blocked = [
        f"{fmt}（{label}，需要 {missing_library_hint(fmt)}）"
        for fmt, (_candidates, label) in _FORMAT_LIBRARIES.items()
        if library_for_format(fmt) is None
    ]
    parts = [
        "【产出格式】标准库能写的格式随便用：" + "、".join(_STDLIB_FORMATS) + "。",
        "另有可用第三方库：" + ("；".join(supported) if supported else "无") + "。",
    ]
    if blocked:
        parts.append(
            "当前环境**不能**生成：" + "；".join(blocked) + "。"
            "这些格式不要自己拼字节流——手搓出来的文件多数查看器打不开，而且照样跑成 success，"
            "用户拿到才发现。需要它们时改用可写的格式（.md/.html/.xlsx…），"
            "或者直接告诉用户「当前环境缺 X 库，装上后我再做」，由用户决定，不要自行绕过。"
        )
    return "".join(parts)
