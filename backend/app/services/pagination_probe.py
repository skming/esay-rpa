"""翻页在第 1 页就停下时，判断到底是「本来只有一页」还是「下一页 selector 找错了」。

两种情况交出的东西一模一样：任务 success、变量非空、只有第 1 页，谁也看不出少了什么。
判错的代价不对称——把真·单页判成失败只多一轮修复，把漏抓判成成功会把残缺数据交给
用户、还附一句「验收通过」。所以这里不信任节点上配的那个 selector，改问页面本身：
DOM 里还能不能找到指向第 2 页及以后的控件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# 停在第 1 页的三种原因都说明「这里有下一页」这个断言没有兑现，都要送去证据裁决。
FIRST_PAGE_STOP_REASONS = frozenset({
    "next_selector_not_found",
    "next_button_hidden",
    "next_button_disabled",
})

SINGLE_PAGE_VERDICT = "single_page_confirmed"

# 插件执行器没有 evaluate 通道，只能拿这批固定 CSS 逐个问 elementState。
# 覆盖 rel=next、aria/title 标注的下一页，以及最常见的分页容器内链接。
EXTENSION_EVIDENCE_SELECTORS: tuple[str, ...] = (
    'a[rel="next"]',
    '[aria-label*="下一页"]',
    '[aria-label*="next" i]',
    '[title*="下一页"]',
    '[title*="Next" i]',
    '.pagination a[href]',
    '.pager a[href]',
    '.page a[href]',
    '[class*="paginat"] a[href]',
    'a[href*="?p=2"]',
    'a[href*="&p=2"]',
    'a[href*="page=2"]',
)

# 页码参数写法各站不同，但都逃不过「?键=数字」或「/page/数字」这两种形状。
_PAGE_PARAM_PATTERN = re.compile(
    r"[?&](?:p|page|pageno|pagenum|pageindex|page_index|pn|current)=(\d{1,6})",
    re.IGNORECASE,
)
_PAGE_PATH_PATTERN = re.compile(r"/page[/\-_](\d{1,6})", re.IGNORECASE)

PAGINATION_PROBE_JS = """() => {
  const NEXT_TEXT = /^(下一页|下页|後頁|次へ|next|next page|›|»|>|→|❯|＞)$/i;
  const seen = new Set();
  const out = [];
  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const describe = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const classes = (el.getAttribute('class') || '').trim().split(/\\s+/).filter(Boolean).slice(0, 2);
    const suffix = classes.map((c) => '.' + CSS.escape(c)).join('');
    if (suffix) return el.tagName.toLowerCase() + suffix;
    if (el.getAttribute('rel') === 'next') return el.tagName.toLowerCase() + '[rel="next"]';
    return el.tagName.toLowerCase();
  };
  const record = (el, kind) => {
    if (!isVisible(el)) return;
    const text = (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 24);
    const href = (el.tagName === 'A' && el.href) ? el.href : (el.getAttribute('href') || '');
    const key = kind + '|' + text + '|' + href;
    if (seen.has(key) || out.length >= 12) return;
    seen.add(key);
    out.push({ kind, text, href: href.slice(0, 300), selector: describe(el) });
  };

  document
    .querySelectorAll('a[rel="next"], [aria-label*="下一页"], [aria-label*="next" i], [title*="下一页"], [title*="Next" i]')
    .forEach((el) => record(el, 'next_control'));
  document.querySelectorAll('a[href]').forEach((el) => {
    const text = (el.textContent || '').trim();
    if (NEXT_TEXT.test(text)) {
      record(el, 'next_control');
    } else if (/^\\d{1,4}$/.test(text)) {
      record(el, 'page_number');
    }
  });
  return out;
}"""


def _page_number_in_href(href: str) -> int | None:
    match = _PAGE_PARAM_PATTERN.search(href) or _PAGE_PATH_PATTERN.search(href)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


@dataclass(frozen=True)
class PaginationEvidence:
    """页面上还能找到的、指向第 2 页及以后的控件。"""

    candidates: list[dict[str, Any]]

    @property
    def has_more_pages(self) -> bool:
        return bool(self.next_controls or self.page_links)

    @property
    def next_controls(self) -> list[dict[str, Any]]:
        return [item for item in self.candidates if item.get("kind") == "next_control"]

    @property
    def page_links(self) -> list[dict[str, Any]]:
        # 页码 1 是当前页，不构成「还有下一页」的证据
        return [
            item for item in self.candidates
            if item.get("kind") == "page_number" and (_page_number_in_href(str(item.get("href") or "")) or 0) >= 2
        ]

    def url_template(self) -> str | None:
        """把某个第 N 页链接的页码换成 ${page}，直接可填进节点的 urlTemplate。"""
        for item in self.page_links:
            href = str(item.get("href") or "")
            # 插件执行器只能交回 CSS 选择器充当证据，拿它拼出来的模板是废的
            if not href.startswith("http"):
                continue
            match = _PAGE_PARAM_PATTERN.search(href) or _PAGE_PATH_PATTERN.search(href)
            if match is None:
                continue
            start, end = match.span(1)
            return f"{href[:start]}${{page}}{href[end:]}"
        return None


def _format_candidates(items: list[dict[str, Any]], limit: int = 4) -> str:
    return "、".join(
        f"`{item.get('selector')}`" + (f"（文字「{item.get('text')}」）" if item.get("text") else "")
        for item in items[:limit]
    )


def build_first_page_stop_error(
    *,
    next_selector: str,
    stop_reason: str,
    row_count: int,
    evidence: PaginationEvidence,
) -> str:
    reason_text = {
        "next_selector_not_found": f"下一页 selector `{next_selector}` 在页面上没有匹配到任何元素",
        "next_button_hidden": f"下一页 selector `{next_selector}` 匹配到的元素是隐藏的，一次也没点成",
        "next_button_disabled": f"下一页 selector `{next_selector}` 匹配到的元素是禁用的，一次也没点成",
    }.get(stop_reason, f"下一页 selector `{next_selector}` 没能翻动页面")

    lines = [f"分页未生效：{reason_text}，只采集到第 1 页共 {row_count} 条。"]

    next_controls = evidence.next_controls
    page_links = evidence.page_links
    if next_controls:
        lines.append(f"但页面上存在可见的下一页控件：{_format_candidates(next_controls)}，改用它作为 selector 重跑。")
    if page_links:
        template = evidence.url_template()
        lines.append(
            f"页面用的是数字页码分页：{_format_candidates(page_links)}。"
            + (
                f"点击式翻页对这种站点不可靠（点到第 2 页后按钮位置就变了），改用 URL 翻页："
                f"给该节点填 urlTemplate=`{template}`（`${{page}}` 会被替换成页号），并删掉 selector。"
                if template
                else "点击式翻页对这种站点不可靠，改用 URL 翻页：给该节点填 urlTemplate（含 `${page}` 占位），并删掉 selector。"
            )
        )
    if not next_controls and not page_links:
        lines.append("页面上也找不到任何指向第 2 页的控件，请用 inspect_page 确认真实的分页方式后再改 selector。")
    return "".join(lines)


async def probe_pagination_evidence_playwright(page: Any) -> PaginationEvidence:
    try:
        raw = await page.evaluate(PAGINATION_PROBE_JS)
    except Exception:
        # 探测本身失败不该改变结论：交空证据，让调用方按 stop_reason 原有的严格程度处理
        return PaginationEvidence(candidates=[])
    if not isinstance(raw, list):
        return PaginationEvidence(candidates=[])
    return PaginationEvidence(candidates=[item for item in raw if isinstance(item, dict)])
