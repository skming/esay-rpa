from __future__ import annotations

from app.models.schemas import AnalyzeSiteRequest
from app.services.site_analyzer import SiteAnalyzer


class FakeHtmlFetcher:
    def __init__(self, html_text: str) -> None:
        self.html_text = html_text

    def fetch_html(self, request: AnalyzeSiteRequest) -> str:
        return self.html_text


def build_request(selector: str | None = ".css-a1b2c3") -> AnalyzeSiteRequest:
    return AnalyzeSiteRequest(
        targetUrl="https://example.com/",
        selector=selector,
        maxCandidates=6,
    )


async def test_site_analyzer_detects_css_in_js_and_recommends_stable_selectors() -> None:
    analyzer = SiteAnalyzer(
        fetcher=FakeHtmlFetcher(
            """
            <html>
              <head><title>订单页面</title></head>
              <body>
                <button class="css-a1b2c3 btn" data-testid="submit-order">提交订单</button>
                <input class="x-9f8e7d6" name="username" aria-label="账号" />
                <span class="css-123abc">状态</span>
                <span class="css-987def">成功</span>
              </body>
            </html>
            """
        )
    )

    result = await analyzer.analyze(build_request())

    assert result.title == "订单页面"
    assert result.has_css_in_js is True
    assert result.risk_level == "medium"
    assert result.checked_selector is not None
    assert result.checked_selector.match_count == 1
    assert result.checked_selector.stable is False
    assert any("CSS-in-JS" in warning for warning in result.warnings)
    assert [candidate.selector for candidate in result.candidates[:2]] == [
        'button[data-testid="submit-order"]',
        'input[name="username"]',
    ]


async def test_site_analyzer_marks_missing_selector_as_high_risk() -> None:
    analyzer = SiteAnalyzer(fetcher=FakeHtmlFetcher("<html><body><main id='app'>空页面</main></body></html>"))

    result = await analyzer.analyze(build_request("#missing"))

    assert result.risk_level == "high"
    assert result.checked_selector is not None
    assert result.checked_selector.match_count == 0
    assert any("未命中" in warning for warning in result.warnings)
