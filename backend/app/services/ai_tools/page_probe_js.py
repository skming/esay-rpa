"""inspect_page 注入页面的探测脚本。

只提取 HTML 客观事实、不做结构预解读，语义判断交给 AI；
元素引用（ref）绑定在一次观察版本上，页面一变旧 ref 立刻失效，
避免模型拿着上一次观察的编号去操作已经重渲染过的 DOM。

脚本本体在同目录的 page_probe.js：扩展内容脚本要 import 它（MV3 的扩展 CSP 禁 unsafe-eval，
下发字符串再 eval 这条路走不通），这里把它和共享 DOM factory 组合成 Playwright evaluate 用的表达式。
"""
from __future__ import annotations

from pathlib import Path

from app.services.js_expression_loader import load_factory_entry_expression

_SOURCE_PATH = Path(__file__).with_name("page_probe.js")

PAGE_PROBE_JS = load_factory_entry_expression(
    _SOURCE_PATH,
    entry_import="import { createDomLocator } from '../dom_locator.js';",
    entry_marker="export const PAGE_PROBE = ",
)
