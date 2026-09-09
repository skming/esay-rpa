"""inspect_page 注入页面的探测脚本。

只提取 HTML 客观事实、不做结构预解读，语义判断交给 AI；
元素引用（ref）绑定在一次观察版本上，页面一变旧 ref 立刻失效，
避免模型拿着上一次观察的编号去操作已经重渲染过的 DOM。

脚本本体在同目录的 page_probe.js：扩展内容脚本要 import 它（MV3 的扩展 CSP 禁 unsafe-eval，
下发字符串再 eval 这条路走不通），这里把同一个文件读成 Playwright evaluate 用的表达式。
"""
from __future__ import annotations

from pathlib import Path

_SOURCE_PATH = Path(__file__).with_name("page_probe.js")
_MARKER = "export const PAGE_PROBE = "

_head, _found, _expression = _SOURCE_PATH.read_text(encoding="utf-8").partition(_MARKER)
PAGE_PROBE_JS = _expression.strip().rstrip(";")

# 导入期就炸：拿着半截脚本去 evaluate 只会在真实页面上报一句语法错误，
# 而那时的现场是「浏览器打不开页面」，查不到这里。
if not _found or not PAGE_PROBE_JS.startswith("(args) => {") or not PAGE_PROBE_JS.endswith("}"):
    raise RuntimeError(
        f"{_SOURCE_PATH.name} 里没有取到完整的探测表达式："
        f"该文件必须恰好有一行 `{_MARKER}(args) => {{...}};`"
    )
