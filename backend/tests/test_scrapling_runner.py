from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from app.models.schemas import RunTaskRequest
from app.services.scrapling_runner import ScraplingRunner


async def _noop_log(level: str, message: str, detail: str | None = None) -> None:
    return None


async def test_scrapling_runner_reports_timeout_with_context(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = ScraplingRunner()

    async def timeout_run(request: RunTaskRequest) -> object:
        raise asyncio.TimeoutError()

    monkeypatch.setattr(runner, "_run_async", timeout_run)

    with pytest.raises(RuntimeError) as exc_info:
        await runner.run(
            "task-timeout",
            RunTaskRequest(
                flowName="超时流程",
                targetUrl="https://quotes.toscrape.com/",
                selector=".quote .text::text",
                timeoutMs=1000,
            ),
            _noop_log,
        )

    assert str(exc_info.value) == "Scrapling 采集超时：1000ms · static · https://quotes.toscrape.com/"


async def test_scrapling_runner_reports_empty_exception_with_type(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = ScraplingRunner()

    async def empty_error_run(request: RunTaskRequest) -> object:
        raise ValueError()

    monkeypatch.setattr(runner, "_run_async", empty_error_run)

    with pytest.raises(RuntimeError) as exc_info:
        await runner.run(
            "task-empty-error",
            RunTaskRequest(
                flowName="空错误流程",
                targetUrl="https://quotes.toscrape.com/",
                selector=".quote .text::text",
            ),
            _noop_log,
        )

    assert str(exc_info.value) == "Scrapling 采集失败：ValueError · static · https://quotes.toscrape.com/"


# 改版前后：class 名换了、外面多包了一层，结构还是同一个元素。
_BEFORE = '<html><body><div class="wrap"><span class="price">99</span>元</div></body></html>'
_AFTER = '<html><body><section><div class="box"><span class="cost">99</span>元</div></section></body></html>'


def _page(html: str, storage_dir: str, *, url: str = "https://shop.example/item") -> object:
    from scrapling.parser import Selector

    return Selector(
        html,
        url=url,
        adaptive=True,
        storage_args={"storage_file": str(Path(storage_dir) / "scrapling_storage.db")},
    )


def _request(selector: str, **overrides: object) -> RunTaskRequest:
    payload: dict[str, object] = {
        "flowName": "改版重定位",
        "targetUrl": "https://shop.example/item",
        "selector": selector,
        "adaptive": True,
        "autoSave": True,
    }
    payload.update(overrides)
    return RunTaskRequest.model_validate(payload)


def test_configure_storage_enables_adaptive_on_every_fetcher(tmp_path: Path) -> None:
    """adaptive 只在 css() 上传是不够的，必须在初始化时打开，否则整段逻辑被跳过。"""
    from scrapling.fetchers import AsyncFetcher, DynamicFetcher, Fetcher, StealthyFetcher

    ScraplingRunner(storage_dir=str(tmp_path))

    # AsyncFetcher 是独立的类而不是 Fetcher 的别名，static 档走的正是它。
    for fetcher in (Fetcher, AsyncFetcher, DynamicFetcher, StealthyFetcher):
        assert fetcher.adaptive is True, fetcher.__name__
        assert fetcher.storage_args == {"storage_file": str(tmp_path / "scrapling_storage.db")}


def test_relocates_the_same_element_after_the_page_is_redesigned(tmp_path: Path) -> None:
    runner = ScraplingRunner(storage_dir=str(tmp_path))
    request = _request("span.price")

    assert runner._extract_values(_page(_BEFORE, str(tmp_path)), request) == ["99"]
    # 原选择器在新页面上一个都选不中，只能靠上一轮存下的指纹找回来。
    assert runner._extract_values(_page(_AFTER, str(tmp_path)), request) == ["99"]


def test_text_pseudo_element_survives_relocation_as_text(tmp_path: Path) -> None:
    """重定位交还的是元素，若把 ::text 一起丢给 css()，getall() 会吐出整段 HTML。"""
    runner = ScraplingRunner(storage_dir=str(tmp_path))
    request = _request("span.price::text")

    assert runner._extract_values(_page(_BEFORE, str(tmp_path)), request) == ["99"]
    assert runner._extract_values(_page(_AFTER, str(tmp_path)), request) == ["99"]


def test_text_pseudo_element_keeps_direct_child_semantics(tmp_path: Path) -> None:
    """"sel::text" 一直只取直接文本子节点，不含后代；换成 el.text 会少取、换成
    元素级 el.css('::text') 会多取，两种都会改变既有流程的输出。"""
    runner = ScraplingRunner(storage_dir=str(tmp_path))
    html = '<html><body><div class="c">A<b>B</b>C</div><div class="c">D</div></body></html>'

    values = runner._extract_values(_page(html, str(tmp_path), url="https://x.example/t"), _request("div.c::text"))

    assert values == ["A", "C", "D"]


def test_attribute_mode_relocates_and_still_returns_the_attribute(tmp_path: Path) -> None:
    runner = ScraplingRunner(storage_dir=str(tmp_path))
    request = _request("a.link", extractMode="attribute", attribute="href")
    before = '<html><body><a class="link" href="/one">x</a></body></html>'
    after = '<html><body><nav><a class="l2" href="/one">x</a></nav></body></html>'

    assert runner._extract_values(_page(before, str(tmp_path)), request) == ["/one"]
    assert runner._extract_values(_page(after, str(tmp_path)), request) == ["/one"]


def test_attr_pseudo_element_returns_the_attribute_in_the_default_mode(tmp_path: Path) -> None:
    """`a::attr(href)` 单写、不配 extractMode=attribute 是仓库里认可的写法（lint 反过来
    禁止两者同用）。默认档要是把它当 ::text 处理，链接列表会安静地变成链接文字列表。"""
    runner = ScraplingRunner(storage_dir=str(tmp_path))
    request = _request("a.link::attr(href)")
    before = '<html><body><a class="link" href="/one">TXT</a></body></html>'
    after = '<html><body><nav><a class="l2" href="/one">TXT</a></nav></body></html>'

    assert runner._extract_values(_page(before, str(tmp_path)), request) == ["/one"]
    assert runner._extract_values(_page(after, str(tmp_path)), request) == ["/one"]


def test_configure_storage_warns_instead_of_failing_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """静默 pass 是这个功能死掉多久都没人发现的原因。"""
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("configure exploded")

    monkeypatch.setattr("scrapling.fetchers.Fetcher.configure", classmethod(boom))

    with caplog.at_level(logging.WARNING):
        ScraplingRunner(storage_dir=str(tmp_path))

    assert "自动重定位" in caplog.text
