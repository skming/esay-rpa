from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.models.schemas import RunTaskRequest, ScrapeResult

logger = logging.getLogger(__name__)

LogCallback = Callable[[str, str, str | None], Awaitable[None]]

# 结尾的 ::text / ::attr(x) 是伪元素，css() 返回字符串而非元素。
_PSEUDO_SUFFIX_RE = re.compile(r"::(?:text|attr\(\s*[^)\s]+\s*\))\s*$")


class RunnerProtocol(Protocol):
    async def run(self, task_id: str, request: RunTaskRequest, on_log: LogCallback) -> ScrapeResult:
        ...


@dataclass(frozen=True)
class _RawScrapeResult:
    values: list[str]


class ScraplingRunner:
    def __init__(self, storage_dir: str | None = None) -> None:
        # 配置存储路径后 auto_save/adaptive 才能真正持久化并匹配元素指纹。
        if storage_dir:
            self._configure_storage(storage_dir)

    @staticmethod
    def _configure_storage(storage_dir: str) -> None:
        try:
            from scrapling.fetchers import AsyncFetcher, DynamicFetcher, Fetcher, StealthyFetcher

            Path(storage_dir).mkdir(parents=True, exist_ok=True)
            # 键名必须是 storage_file（SQLiteStorageSystem 的形参名）。传错键在这里不报错，
            # 要等第一次真去开库时才抛 TypeError，而那时已经在抓取途中了。
            args = {"storage_file": str(Path(storage_dir) / "scrapling_storage.db")}
            # adaptive 必须在 Selector 初始化时打开：css(adaptive=True) 只是单次调用的开关，
            # 初始化时没开就整段跳过，节点上的开关随之全程失效，而报错只有 scrapling
            # 自己 logger 里的一行 warning。
            # AsyncFetcher 是独立的类，configure 写的是各自的类属性，漏了它
            # static 档（build_request_for_fetch_node 的默认值）就永远拿不到指纹。
            for fetcher in (Fetcher, AsyncFetcher, DynamicFetcher, StealthyFetcher):
                fetcher.configure(adaptive=True, storage_args=args)
        except Exception:
            # 配置失败只降级为「没有自动重定位」，不阻断启动；但必须留痕，
            # 静默 pass 的话这个功能死掉也没有任何迹象。
            logger.warning("Scrapling 元素指纹存储配置失败，页面改版后的自动重定位将不可用", exc_info=True)

    async def run(self, task_id: str, request: RunTaskRequest, on_log: LogCallback) -> ScrapeResult:
        await on_log("running", "开始调用 Scrapling 采集引擎", f"{request.fetcher} · {request.target_url}")
        try:
            raw_result = await asyncio.wait_for(
                self._run_async(request),
                timeout=request.timeout_ms / 1000,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"Scrapling 采集超时：{request.timeout_ms}ms · {request.fetcher} · {request.target_url}"
            ) from exc
        except ModuleNotFoundError as exc:
            raise RuntimeError('未安装 Scrapling，请执行 uv pip install "scrapling[all]"') from exc
        except Exception as exc:
            raise RuntimeError(self._format_runtime_error(exc, request)) from exc

        await on_log("success", f"采集完成 · 命中 {len(raw_result.values)} 条数据", None)
        return ScrapeResult(
            url=str(request.target_url),
            selector=request.selector,
            count=len(raw_result.values),
            values=raw_result.values,
        )

    @staticmethod
    def _format_runtime_error(exc: Exception, request: RunTaskRequest) -> str:
        message = str(exc).strip()
        if not message:
            message = exc.__class__.__name__
        return f"Scrapling 采集失败：{message} · {request.fetcher} · {request.target_url}"

    async def _run_async(self, request: RunTaskRequest) -> _RawScrapeResult:
        # static 走原生异步 AsyncFetcher；dynamic/stealthy 内部封装 Playwright，必须放线程池。
        if request.fetcher == "static":
            page = await self._fetch_static_async(str(request.target_url))
        else:
            page = await asyncio.to_thread(self._fetch_page_sync, request)

        values = self._extract_values(page, request)
        return _RawScrapeResult(values=values)

    @staticmethod
    async def _fetch_static_async(url: str) -> Any:
        from scrapling import AsyncFetcher

        return await AsyncFetcher.get(url)

    @staticmethod
    def _fetch_page_sync(request: RunTaskRequest) -> Any:
        if request.fetcher == "dynamic":
            from scrapling.fetchers import DynamicFetcher

            return DynamicFetcher.fetch(str(request.target_url), headless=True, network_idle=True)

        from scrapling.fetchers import StealthyFetcher

        return StealthyFetcher.fetch(str(request.target_url), headless=True, network_idle=True)

    def _extract_values(self, page: Any, request: RunTaskRequest) -> list[str]:
        selector = request.selector or ""
        mode = request.extract_mode

        if mode == "by_text":
            return self._extract_by_text(page, request)

        if mode == "similar":
            return self._extract_similar(page, selector)

        if mode == "attribute":
            attribute = request.attribute or "href"
            values = (el.attrib.get(attribute) for el in self._select(page, selector, request))
            return [str(v) for v in values if v is not None]

        if mode == "html":
            return [str(getattr(el, "html_content", el)) for el in self._select(page, selector, request)]

        elements = self._select(page, selector, request)
        if _PSEUDO_SUFFIX_RE.search(selector):
            # 页面级 "sel::text" 取的是直接文本子节点、不含后代，./text() 与之逐字等价；
            # 元素级 el.css("::text") 会把后代文本也捞进来，换掉会改变既有流程的输出。
            return [str(t) for el in elements for t in el.xpath("./text()").getall()]
        return [str(getattr(el, "text", el)) for el in elements]

    @staticmethod
    def _select(page: Any, selector: str, request: RunTaskRequest) -> Any:
        """按裸选择器选出元素，伪元素的取值由调用方自己完成。

        伪元素不能交给 css()：平时它返回字符串没问题，可一旦命中重定位，relocate 交还的是
        元素本身，getall() 于是吐出整段 HTML 而不是文本——错得非常安静，抓下来的表格会突然
        变成一列标签。指纹也只有落在真实元素上才谈得上「改版后还能找回同一个元素」。

        identifier 固定用原始 selector：同一批元素在不同 extractMode 下写法不同（裸写、
        ::text、配 attribute），共用一份指纹正是想要的，否则每种写法各存一份互不相认。
        """
        base = _PSEUDO_SUFFIX_RE.sub("", selector).strip() or selector
        return page.css(base, identifier=selector, adaptive=request.adaptive, auto_save=request.auto_save)

    def _extract_by_text(self, page: Any, request: RunTaskRequest) -> list[str]:
        text_query = getattr(request, "text_query", None) or ""
        if not text_query:
            return []
        results = page.find_by_text(text_query, first_match=False, partial=True)
        if results is None:
            return []
        if not isinstance(results, (list, tuple)):
            results = [results]
        return [str(getattr(el, "text", el)) for el in results if el is not None]

    @staticmethod
    def _extract_similar(page: Any, selector: str) -> list[str]:
        if not selector:
            return []
        elements = page.css(selector)
        if not elements:
            return []
        seed = elements[0] if hasattr(elements, "__getitem__") else elements
        # 阈值 0.4 为经验值：过高会漏掉结构相似但文案不同的兄弟节点，过低会引入噪声
        similar = seed.find_similar(similarity_threshold=0.4, match_text=False)
        all_els = [seed, *(similar or [])]
        seen: set[str] = set()
        results: list[str] = []
        for el in all_els:
            text = str(getattr(el, "text", el) or "").strip()
            if text and text not in seen:
                seen.add(text)
                results.append(text)
        return results

