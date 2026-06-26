from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from lxml import html
from lxml.etree import ParserError

from app.models.schemas import AnalyzeSiteRequest, SelectorCandidate, SelectorCheck, SiteAnalysisResult


# ── Fetcher protocols ─────────────────────────────────────────────────────────

class PageFetcher(Protocol):
    """Returns a Scrapling page object (Selector) directly."""
    def fetch_page(self, request: AnalyzeSiteRequest) -> Any: ...


class HtmlFetcher(Protocol):
    """Legacy protocol: returns raw HTML string."""
    def fetch_html(self, request: AnalyzeSiteRequest) -> str: ...


# ── Scrapling fetcher ─────────────────────────────────────────────────────────

class ScraplingFetcher:
    """Fetches pages via Scrapling and exposes both the page object and raw HTML."""

    def fetch_page(self, request: AnalyzeSiteRequest) -> Any:
        if request.fetcher == "dynamic":
            from scrapling.fetchers import DynamicFetcher
            return DynamicFetcher.fetch(str(request.target_url), headless=True, network_idle=True)

        if request.fetcher == "stealthy":
            from scrapling.fetchers import StealthyFetcher
            return StealthyFetcher.fetch(str(request.target_url), headless=True, network_idle=True)

        from scrapling.fetchers import Fetcher
        return Fetcher.get(str(request.target_url))

    def fetch_html(self, request: AnalyzeSiteRequest) -> str:
        page = self.fetch_page(request)
        return str(getattr(page, "html_content", None) or getattr(page, "html", None) or page)


# Backward-compatible alias
ScraplingHtmlFetcher = ScraplingFetcher


# ── Candidate data class ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class _RawCandidate:
    selector: str
    match_count: int
    sample_text: str
    stability_score: int
    reasons: list[str]


# ── Site analyzer ─────────────────────────────────────────────────────────────

class SiteAnalyzer:
    def __init__(self, fetcher: PageFetcher | HtmlFetcher | None = None) -> None:
        self._fetcher = fetcher or ScraplingFetcher()
        # Scrapling page object retained during analyze() for _scrapling_extra_candidates
        self._scrapling_page: Any = None

    async def analyze(self, request: AnalyzeSiteRequest) -> SiteAnalysisResult:
        try:
            # P2: fetch the rich Scrapling page object instead of discarding it
            if hasattr(self._fetcher, "fetch_page"):
                page = await asyncio.wait_for(
                    asyncio.to_thread(self._fetcher.fetch_page, request),
                    timeout=request.timeout_ms / 1000,
                )
                self._scrapling_page = page
                html_text = str(
                    getattr(page, "html_content", None)
                    or getattr(page, "html", None)
                    or page
                )
            else:
                html_text = await asyncio.wait_for(
                    asyncio.to_thread(self._fetcher.fetch_html, request),  # type: ignore[union-attr]
                    timeout=request.timeout_ms / 1000,
                )
                self._scrapling_page = None
        except ModuleNotFoundError as exc:
            raise RuntimeError('未安装 Scrapling，请执行 uv pip install "scrapling[all]"') from exc

        return self.analyze_html(html_text=html_text, request=request)

    def analyze_html(self, *, html_text: str, request: AnalyzeSiteRequest) -> SiteAnalysisResult:
        try:
            document = html.fromstring(html_text)
        except (ParserError, ValueError) as exc:
            raise ValueError("HTML 内容无法解析") from exc

        checked_selector = self._check_selector(document, request.selector) if request.selector is not None else None
        has_css_in_js = self._detect_css_in_js(document)
        warnings = self._build_warnings(has_css_in_js=has_css_in_js, checked_selector=checked_selector)
        candidates = self._build_candidates(document, limit=request.max_candidates)

        return SiteAnalysisResult(
            url=str(request.target_url),
            title=self._extract_title(document),
            fetcher=request.fetcher,
            has_css_in_js=has_css_in_js,
            risk_level=self._risk_level(has_css_in_js=has_css_in_js, checked_selector=checked_selector),
            warnings=warnings,
            checked_selector=checked_selector,
            candidates=[
                SelectorCandidate(
                    selector=item.selector,
                    match_count=item.match_count,
                    sample_text=item.sample_text,
                    stability_score=item.stability_score,
                    reasons=item.reasons,
                )
                for item in candidates
            ],
        )

    # ── Candidate building ────────────────────────────────────────────────────

    def _build_candidates(self, document, *, limit: int) -> list[_RawCandidate]:
        scored: dict[str, _RawCandidate] = {}
        elements = [node for node in document.iter() if isinstance(getattr(node, "tag", None), str)]
        tag_counter = Counter(node.tag for node in elements)

        for element in elements:
            if element.tag in {"html", "head", "body", "script", "style", "meta", "link"}:
                continue
            for selector, reasons, base_score in self._candidate_selectors(element):
                matches = self._css(document, selector)
                if not matches:
                    continue
                text = self._text(element)
                score = max(min(base_score - self._ambiguity_penalty(len(matches), tag_counter[element.tag]), 100), 0)
                current = scored.get(selector)
                candidate = _RawCandidate(
                    selector=selector,
                    match_count=len(matches),
                    sample_text=text,
                    stability_score=score,
                    reasons=reasons,
                )
                if current is None or candidate.stability_score > current.stability_score:
                    scored[selector] = candidate

        # P2: Merge Scrapling auto-generated selectors as additional candidates.
        # These are produced by Scrapling's generate_css_selector property, which
        # computes the shortest unique semantic path for each element.
        if self._scrapling_page is not None:
            for extra in self._scrapling_extra_candidates():
                if extra.selector not in scored:
                    scored[extra.selector] = extra
                elif extra.stability_score > scored[extra.selector].stability_score:
                    scored[extra.selector] = extra

        return sorted(scored.values(), key=lambda item: (-item.stability_score, item.match_count, item.selector))[:limit]

    def _scrapling_extra_candidates(self) -> list[_RawCandidate]:
        """P2: Query the Scrapling page for semantically interesting elements and use
        generate_css_selector to produce stable, auto-generated selector paths."""
        candidates: list[_RawCandidate] = []
        seen: set[str] = set()

        # Probe element types that commonly appear as scraping targets
        probe_tags = ["li", "tr", "td", "a[href]", "button", "[data-testid]", "[id]", "input", "select"]
        for tag in probe_tags:
            try:
                elements = self._scrapling_page.css(tag)
                # Sample at most 6 elements per tag type to avoid noise
                sample = list(elements)[:6] if hasattr(elements, "__iter__") else []
                for el in sample:
                    try:
                        auto_sel: str = el.generate_css_selector or ""
                    except Exception:
                        continue
                    if not auto_sel or auto_sel in seen:
                        continue
                    seen.add(auto_sel)

                    # Score based on selector quality; positional check wins over id-prefix
                    if ":nth-of-type" in auto_sel or ":nth-child" in auto_sel:
                        # Positional selectors are fragile across page changes
                        score = 48
                        reason = "Scrapling 自动生成：位置路径（结构变化后可能失效）"
                    elif auto_sel.startswith("#") and " " not in auto_sel:
                        # Simple #id — most stable
                        score = 97
                        reason = "Scrapling 自动生成：id 唯一路径"
                    elif "[data-testid" in auto_sel or "[data-test" in auto_sel:
                        score = 93
                        reason = "Scrapling 自动生成：data-testid 稳定属性路径"
                    elif "#" in auto_sel:
                        # Path rooted at an id ancestor — very stable
                        score = 88
                        reason = "Scrapling 自动生成：id 锚定路径"
                    else:
                        score = 72
                        reason = "Scrapling 自动生成：语义类名路径"

                    text = ""
                    try:
                        text = str(getattr(el, "text", "") or "")[:140]
                    except Exception:
                        pass

                    candidates.append(_RawCandidate(
                        selector=auto_sel,
                        match_count=1,
                        sample_text=text,
                        stability_score=score,
                        reasons=[reason],
                    ))
            except Exception:
                continue

        return candidates

    def _candidate_selectors(self, element) -> list[tuple[str, list[str], int]]:
        candidates: list[tuple[str, list[str], int]] = []
        tag = element.tag
        element_id = self._attr(element, "id")
        if element_id and not self._looks_hashed(element_id):
            candidates.append((f"#{self._escape_identifier(element_id)}", ["id 稳定且唯一性通常较高"], 96))

        attr_scores = {
            "data-testid": 92,
            "data-test": 91,
            "data-cy": 91,
            "name": 89,
            "aria-label": 86,
            "role": 80,
        }
        for attr, score in attr_scores.items():
            value = self._attr(element, attr)
            if value and not self._looks_hashed(value):
                candidates.append((f'{tag}[{attr}="{self._escape_attr(value)}"]', [f"{attr} 属性适合自动化定位"], score))

        class_names = [name for name in self._attr(element, "class").split() if not self._looks_hashed(name)]
        if class_names:
            stable_classes = ".".join(self._escape_identifier(name) for name in class_names[:2])
            candidates.append((f"{tag}.{stable_classes}", ["使用可读 class，避开疑似哈希类名"], 76))

        if self._text(element):
            candidates.append((tag, ["标签级候选可作为兜底，需要结合上下文收窄"], 45))

        return candidates

    # ── Selector validation ───────────────────────────────────────────────────

    def _check_selector(self, document, selector: str) -> SelectorCheck:
        normalized = selector.removesuffix("::text")
        normalized = re.sub(r"::attr\([^)]+\)$", "", normalized)
        matches = self._css(document, normalized)
        sample_texts = [self._text(match) for match in matches[:3]]
        return SelectorCheck(
            selector=selector,
            match_count=len(matches),
            sample_texts=[text for text in sample_texts if text],
            stable=len(matches) > 0 and not self._selector_uses_hashed_class(selector),
        )

    # ── Warnings & risk ───────────────────────────────────────────────────────

    def _build_warnings(self, *, has_css_in_js: bool, checked_selector: SelectorCheck | None) -> list[str]:
        warnings: list[str] = []
        if has_css_in_js:
            warnings.append("检测到疑似 CSS-in-JS 或哈希类名，建议优先使用 data-testid、name、aria-label 等稳定属性。")
        if checked_selector is not None and checked_selector.match_count == 0:
            warnings.append("当前 selector 未命中元素，运行任务可能返回空结果。")
        if checked_selector is not None and not checked_selector.stable:
            warnings.append("当前 selector 依赖疑似哈希 class，目标站重新构建后可能失效。")
        return warnings

    def _risk_level(self, *, has_css_in_js: bool, checked_selector: SelectorCheck | None) -> str:
        if checked_selector is not None and checked_selector.match_count == 0:
            return "high"
        if has_css_in_js or (checked_selector is not None and not checked_selector.stable):
            return "medium"
        return "low"

    # ── CSS-in-JS detection ───────────────────────────────────────────────────

    def _detect_css_in_js(self, document) -> bool:
        classes: list[str] = []
        for element in document.iter():
            classes.extend(self._attr(element, "class").split())
        if not classes:
            return False
        hashed_count = sum(1 for class_name in classes if self._looks_hashed(class_name))
        return hashed_count / len(classes) >= 0.25

    def _selector_uses_hashed_class(self, selector: str) -> bool:
        return any(self._looks_hashed(item) for item in re.findall(r"\.([_a-zA-Z][\w-]*)", selector))

    def _looks_hashed(self, value: str) -> bool:
        if not value:
            return False
        if re.search(r"(^|[-_])[a-f0-9]{6,}($|[-_])", value, re.IGNORECASE):
            return True
        return bool(re.fullmatch(r"[a-zA-Z]{1,4}[-_][a-zA-Z0-9_-]{6,}", value))

    # ── lxml helpers ──────────────────────────────────────────────────────────

    def _css(self, document, selector: str) -> list:
        try:
            return list(document.cssselect(selector))
        except Exception:
            return []

    def _extract_title(self, document) -> str | None:
        titles = document.cssselect("title")
        if not titles:
            return None
        title = self._text(titles[0])
        return title or None

    def _text(self, element) -> str:
        text = " ".join(part.strip() for part in element.itertext() if part.strip())
        return text[:140]

    def _attr(self, element, name: str) -> str:
        value = element.get(name)
        return str(value).strip() if value is not None else ""

    def _escape_identifier(self, value: str) -> str:
        return re.sub(r"([^a-zA-Z0-9_-])", r"\\\1", value)

    def _escape_attr(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _ambiguity_penalty(self, match_count: int, tag_count: int) -> int:
        if match_count <= 1:
            return 0
        if match_count <= 3:
            return 8
        if match_count < tag_count:
            return 16
        return 28
