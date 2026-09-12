from __future__ import annotations

from scrapling.engines.toolbelt.custom import Response

import app.services.ai_tools.static_page_probe as probe_module
from app.services.ai_tools.static_page_probe import inspect_static_page


def _response(*, status, url, html_content):
    return Response(url=url, content=html_content, status=status, reason="OK",
                    cookies={}, headers={}, request_headers={})


async def test_static_page_probe_returns_bounded_evidence(monkeypatch) -> None:
    page = _response(
        status=200,
        url="https://forum.example/post-1",
        html_content="""
        <html><head><title>主题标题</title></head><body>
          <article id="topic"><h1>主题标题</h1><p>主题正文</p></article>
          <ul id="replies"><li class="reply">回复一</li><li class="reply">回复二</li></ul>
        </body></html>
        """,
    )

    async def fake_fetch(_url: str) -> object:
        return page

    monkeypatch.setattr(probe_module, "_fetch_static_page", fake_fetch)
    result = await inspect_static_page("https://forum.example/post-1")

    assert result["status"] == "success"
    assert result["inspection_source"] == "scrapling_static"
    assert result["recommended_node_type"] == "browser.fetch"
    assert "回复一" in result["page_text_sample"]
    assert len(result["page_text_sample"]) <= 6_000
    assert result["selector_candidates"]


async def test_static_page_probe_rejects_challenge_html(monkeypatch) -> None:
    page = _response(
        status=200,
        url="https://forum.example/post-1",
        html_content="<html><head><title>Just a moment...</title></head><body>Cloudflare</body></html>",
    )

    async def fake_fetch(_url: str) -> object:
        return page

    monkeypatch.setattr(probe_module, "_fetch_static_page", fake_fetch)
    result = await inspect_static_page("https://forum.example/post-1")

    assert result["status"] == "blocked"
    assert "真实业务内容" in result["error"]


async def test_static_page_probe_rejects_thin_page_without_known_markers(monkeypatch) -> None:
    """未知验证页变体不带已知关键词；只靠关键词表会把拦截页当成真实内容交给模型。"""
    page = _response(
        status=200,
        url="https://forum.example/post-1",
        html_content=(
            "<html><head><title>Verify</title></head>"
            "<body><div>Please wait while we verify your request.</div></body></html>"
        ),
    )

    async def fake_fetch(_url: str) -> object:
        return page

    monkeypatch.setattr(probe_module, "_fetch_static_page", fake_fetch)
    result = await inspect_static_page("https://forum.example/post-1")

    assert result["status"] == "blocked"


async def test_static_page_probe_rejects_challenge_by_html_only_marker(monkeypatch) -> None:
    """cf-chl- 只在 script src 里，text_content() 取不到；只匹配正文会把挑战页当真实内容放过。"""
    page = _response(
        status=200,
        url="https://forum.example/post-1",
        html_content=(
            "<html><head><title>Forum</title></head><body>"
            "<script src=\"/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1?ray=1\"></script>"
            "<div>Loading</div></body></html>"
        ),
    )

    async def fake_fetch(_url: str) -> object:
        return page

    monkeypatch.setattr(probe_module, "_fetch_static_page", fake_fetch)
    result = await inspect_static_page("https://forum.example/post-1")

    assert result["status"] == "blocked"


async def test_static_page_probe_keeps_short_but_structured_page(monkeypatch) -> None:
    """正文短不等于拦截页：有列表/表格等业务结构就不能按薄页拒绝。"""
    page = _response(
        status=200,
        url="https://forum.example/list",
        html_content=(
            "<html><head><title>公告</title></head><body>"
            "<ul><li>公告一</li><li>公告二</li><li>公告三</li></ul>"
            "</body></html>"
        ),
    )

    async def fake_fetch(_url: str) -> object:
        return page

    monkeypatch.setattr(probe_module, "_fetch_static_page", fake_fetch)
    result = await inspect_static_page("https://forum.example/list")

    assert result["status"] == "success"
    assert "公告一" in result["page_text_sample"]


async def test_static_summary_cleans_noise_without_losing_business_structure(monkeypatch):
    markup = """<html><head><title>账单</title><script>HEAD_NOISE</script></head><body>
      <script>BODY_NOISE</script><style>STYLE_NOISE</style>
      <div hidden>HIDDEN_NOISE</div><div style="display:none">CSS_NOISE</div>
      <main><h1>账单明细</h1><p>当前月份</p>
      <ul><li><a href="/bill/1">订单详情</a></li></ul>
      <table id="bills"><tr><th>单号</th><th>金额</th></tr>
      <tr><td>A001</td><td>128.50</td></tr></table></main>
      <ul id="replies"><li>补充说明</li></ul></body></html>"""
    page = _response(status=200, url="https://example.com/bills", html_content=markup)
    original = page.html_content

    async def fetch(_url):
        return page

    monkeypatch.setattr(probe_module, "_fetch_static_page", fetch)
    result = await inspect_static_page(page.url)
    assert result["status"] == "success"
    sample = result["page_text_sample"]
    assert "NOISE" not in sample
    for value in ("账单明细", "[订单详情](/bill/1)", "单号", "金额", "A001", "128.50", "补充说明"):
        assert value in sample
    assert page.html_content == original
    assert result["selector_candidates"]
    assert result["static_only"] is True


async def test_static_summary_removes_script_before_truncation(monkeypatch):
    page = _response(status=200, url="https://example.com/article", html_content=
        '<html><body><script>' + 'x' * 10000 + '</script><article><h1>有效标题</h1><p>'
        + '正文' * 5000 + '</p></article></body></html>')

    async def fetch(_url):
        return page

    monkeypatch.setattr(probe_module, "_fetch_static_page", fetch)
    result = await inspect_static_page(page.url)
    assert result["status"] == "success"
    assert "有效标题" in result["page_text_sample"]
    assert "xxxx" not in result["page_text_sample"]
    assert len(result["page_text_sample"]) <= 6000
    assert result["truncated"] is True
    assert result["next_cursor"]


async def test_static_summary_rejects_page_with_only_hidden_content(monkeypatch):
    page = _response(status=200, url="https://example.com/empty", html_content=
        '<html><body><main hidden>不可见正文</main><script>noise</script></body></html>')

    async def fetch(_url):
        return page

    monkeypatch.setattr(probe_module, "_fetch_static_page", fetch)
    result = await inspect_static_page(page.url)
    assert result["status"] == "error"
    assert "未取得可用正文" in result["error"]
    assert "page_text_sample" not in result
