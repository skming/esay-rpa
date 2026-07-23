from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.models.schemas import RunTaskRequest, ScrapeResult

LogCallback = Callable[[str, str, str | None], Awaitable[None]]


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
            from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher

            db_path = str(Path(storage_dir) / "scrapling_storage.db")
            Path(storage_dir).mkdir(parents=True, exist_ok=True)
            args = {"db_path": db_path}
            Fetcher.configure(storage_args=args)
            DynamicFetcher.configure(storage_args=args)
            StealthyFetcher.configure(storage_args=args)
        except Exception:
            pass  # 未安装 scrapling 或配置失败时静默降级，不阻断启动

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
            return self._coerce_to_strings(
                page.css(f"{selector}::attr({attribute})").getall()
            )

        if mode == "html":
            elements = page.css(selector, adaptive=request.adaptive, auto_save=request.auto_save)
            return [str(getattr(el, "html_content", el)) for el in elements]

        if "::text" in selector:
            return self._coerce_to_strings(
                page.css(selector, adaptive=request.adaptive, auto_save=request.auto_save).getall()
            )
        elements = page.css(selector, adaptive=request.adaptive, auto_save=request.auto_save)
        return [str(getattr(el, "text", el)) for el in elements]

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

    @staticmethod
    def _coerce_to_strings(values: object) -> list[str]:
        if values is None:
            return []
        if isinstance(values, str):
            return [values]
        return [str(v) for v in values]
