from __future__ import annotations

from types import SimpleNamespace

import app.services.ai_tools.static_page_probe as probe_module
from app.services.ai_tools.static_page_probe import inspect_static_page


async def test_static_page_probe_returns_bounded_evidence(monkeypatch) -> None:
    page = SimpleNamespace(
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
    page = SimpleNamespace(
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
    page = SimpleNamespace(
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
    page = SimpleNamespace(
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
    page = SimpleNamespace(
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
