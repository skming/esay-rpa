"""把共享 ESM DOM factory 与单个入口组合成 Playwright 可执行表达式。"""
from __future__ import annotations

from pathlib import Path

_FACTORY_PATH = Path(__file__).with_name("dom_locator.js")
_FACTORY_MARKER = "export const createDomLocator = "


def load_factory_entry_expression(
    entry_path: Path,
    *,
    entry_import: str,
    entry_marker: str,
) -> str:
    """组合唯一的 factory 与入口表达式，模块边界漂移时在导入期失败。"""
    factory_source = _FACTORY_PATH.read_text(encoding="utf-8")
    entry_source = entry_path.read_text(encoding="utf-8")
    _factory_expression = _export_expression(factory_source, _FACTORY_MARKER, _FACTORY_PATH)
    entry_expression = _export_expression(entry_source, entry_marker, entry_path)
    if entry_source.count(entry_import) != 1:
        raise RuntimeError(f"{entry_path.name} 必须恰好 import 一次共享 DOM factory：{entry_import}")
    return (
        "(args) => {\n"
        f"const createDomLocator = {_factory_expression};\n"
        f"return ({entry_expression})(args);\n"
        "}"
    )


def load_factory_callback_entry_expression(
    entry_path: Path,
    *,
    entry_import: str,
    entry_marker: str,
    callback_name: str,
) -> str:
    """组合会调用页面桥接函数的入口；evaluate 只返回 null，disposer 留在页面上。"""
    factory_source = _FACTORY_PATH.read_text(encoding="utf-8")
    entry_source = entry_path.read_text(encoding="utf-8")
    factory_expression = _export_expression(factory_source, _FACTORY_MARKER, _FACTORY_PATH)
    entry_expression = _export_expression(entry_source, entry_marker, entry_path)
    if entry_source.count(entry_import) != 1:
        raise RuntimeError(f"{entry_path.name} 必须恰好 import 一次共享 DOM factory：{entry_import}")
    return (
        "(args) => {\n"
        f"const createDomLocator = {factory_expression};\n"
        f"const startPicker = {entry_expression};\n"
        f"window.__rpaPickerDispose = startPicker(args, event => window.{callback_name}(event));\n"
        "return null;\n"
        "}"
    )


def _export_expression(source: str, marker: str, path: Path) -> str:
    if source.count(marker) != 1:
        raise RuntimeError(f"{path.name} 必须恰好有一行 `{marker}...`")
    _head, _found, expression = source.partition(marker)
    result = expression.strip().rstrip(";")
    if not _found or not result.startswith("(") or not result.endswith("}"):
        raise RuntimeError(f"{path.name} 里没有取到完整的表达式：`{marker}` 后必须是箭头函数")
    return result
