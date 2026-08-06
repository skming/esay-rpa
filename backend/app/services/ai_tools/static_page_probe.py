from __future__ import annotations

import asyncio
import re
from typing import Any

from lxml import html
from lxml.etree import ParserError

from app.models.schemas import AnalyzeSiteRequest
from app.services.site_analyzer import SiteAnalyzer


_BLOCK_TEXT_MARKERS = (
    "access denied",
    "attention required",
    "enable javascript and cookies",
    "just a moment",
    "人机验证",
    "访问被拒绝",
)
# 这些只出现在 HTML 源码（script src、form id、挑战脚本路径），text_content() 取不到；
# 拿正文找它们等于永远不命中，所以必须对原始 HTML 匹配。
_BLOCK_HTML_MARKERS = (
    "cf-chl-",
    "cf-browser-verification",
    "challenge-platform",
    "__cf_chl",
    "id=\"challenge-form\"",
    "turnstile",
)
_MAX_TEXT_SAMPLE_CHARS = 6_000


async def _fetch_static_page(url: str) -> Any:
    from scrapling import AsyncFetcher

    return await AsyncFetcher.get(url)


def _response_status(page: Any) -> int | None:
    for field in ("status", "status_code"):
        value = getattr(page, field, None)
        if isinstance(value, int):
            return value
    return None


def _page_html(page: Any) -> str:
    return str(getattr(page, "html_content", None) or getattr(page, "html", None) or page)


def _page_url(page: Any, requested_url: str) -> str:
    value = getattr(page, "url", None)
    return str(value or requested_url)


def _looks_like_block_page(document: Any, page_text: str, title: str, html_text: str) -> bool:
    """拦截页判据分三层：正文关键词、HTML 结构特征、薄页且无业务结构。"""
    fingerprint = f"{title}\n{page_text[:2_000]}".lower()
    if any(marker in fingerprint for marker in _BLOCK_TEXT_MARKERS):
        return True
    lowered_html = html_text[:20_000].lower()
    if any(marker in lowered_html for marker in _BLOCK_HTML_MARKERS):
        return True
    if "cloudflare" in fingerprint and any(
        marker in fingerprint
        for marker in ("checking your browser", "performance & security", "ray id")
    ):
        return True
    # 未知验证页变体不带任何已知关键词，只表现为「正文极薄」。但短正文本身不是判据：
    # 公告列表、单条详情页同样短，误判会把能抓的站点判成不可达，所以要求同时没有业务结构。
    if len(page_text) < 500:
        has_structure = (
            len(document.xpath("//ul | //ol | //table | //article | //main")) > 0
            or len(document.xpath("//p")) >= 3
        )
        if not has_structure:
            return True
    return False


async def inspect_static_page(url: str, *, timeout_ms: int = 20_000) -> dict[str, Any]:
    """用独立 HTTP 通道获取静态 HTML，避免把浏览器单通道失败误判成站点不可达。"""
    try:
        page = await asyncio.wait_for(_fetch_static_page(url), timeout=timeout_ms / 1000)
    except asyncio.TimeoutError:
        return {"status": "error", "error": f"静态抓取超时（{timeout_ms}ms）"}
    except ModuleNotFoundError:
        return {"status": "error", "error": "未安装 Scrapling"}
    except Exception as exc:
        return {"status": "error", "error": f"静态抓取失败：{exc}"}

    status = _response_status(page)
    final_url = _page_url(page, url)
    if isinstance(status, int) and status >= 400:
        return {
            "status": "blocked",
            "http_status": status,
            "url": final_url,
            "error": f"静态抓取返回 HTTP {status}",
        }

    html_text = _page_html(page)
    try:
        document = html.fromstring(html_text)
    except (ParserError, ValueError) as exc:
        return {"status": "error", "url": final_url, "error": f"静态 HTML 无法解析：{exc}"}

    title_nodes = document.xpath("//title/text()")
    title = str(title_nodes[0]).strip() if title_nodes else ""
    page_text = re.sub(r"\s+", " ", document.text_content()).strip()
    if _looks_like_block_page(document, page_text, title, html_text):
        return {
            "status": "blocked",
            "http_status": status,
            "url": final_url,
            "title": title or None,
            "error": "静态抓取得到的是访问验证或拒绝页面，而不是真实业务内容",
        }
    if not page_text:
        return {
            "status": "error",
            "http_status": status,
            "url": final_url,
            "error": "静态抓取未取得可用正文",
        }

    request = AnalyzeSiteRequest(targetUrl=url, fetcher="static", timeoutMs=timeout_ms)
    analysis = SiteAnalyzer().analyze_html(html_text=html_text, request=request)
    return {
        "status": "success",
        "inspection_source": "scrapling_static",
        "http_status": status,
        "url": final_url,
        "title": title or analysis.title,
        "page_text_sample": page_text[:_MAX_TEXT_SAMPLE_CHARS],
        "selector_candidates": [item.model_dump() for item in analysis.candidates],
        "has_css_in_js": analysis.has_css_in_js,
        "warnings": analysis.warnings,
        "recommended_node_type": "browser.fetch",
        "static_only": True,
        "note": (
            "浏览器通道失败后，静态 HTTP 抓取已取得真实 HTML。"
            "只能据此创建 browser.fetch；不得声称已验证点击、登录或 JavaScript 渲染交互。"
        ),
    }
