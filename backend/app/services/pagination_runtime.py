from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.services.pagination_probe import build_blank_page_error


@dataclass(frozen=True)
class PaginationPage:
    rows: list[object]
    url: str = ""
    ready: bool = True

    def signature(self) -> str:
        payload = json.dumps([self.url, self.rows], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class PaginationResult:
    rows: list[object]
    per_page_counts: list[int]
    stop_reason: str


def pagination_max_pages(node: dict[str, object], resolve: Callable[[str], str]) -> int:
    if node.get("continueOnError"):
        raise ValueError("分页不能用 continueOnError 跳过失败并继续处理不完整数据。")
    value = node.get("maxIterations", 20)
    if isinstance(value, str):
        try:
            value = int(resolve(value).strip())
        except ValueError:
            raise ValueError("maxIterations 必须是正整数，页数安全上限变量必须已定义。") from None
    if type(value) is not int or value <= 0:
        raise ValueError("maxIterations 必须是正整数。")
    return value


async def wait_for_page_change(
    read_page: Callable[[float], Awaitable[PaginationPage]],
    pause: Callable[[float], Awaitable[None]],
    *,
    previous: PaginationPage | None,
    visited: set[str],
    timeout_seconds: float,
    page_number: int,
    target_selector: str,
    is_terminal: Callable[[float], Awaitable[str | None]],
    allow_navigation_return: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> PaginationPage:
    deadline = clock() + max(0.0, timeout_seconds)
    before = previous.signature() if previous else None
    latest: PaginationPage | None = None
    candidate: str | None = None
    empty_confirmations = 0
    terminal: str | None = None
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            if latest is None or not latest.rows:
                if latest and latest.ready and empty_confirmations >= 2 and terminal == "next_button_disabled":
                    return latest
                raise ValueError(build_blank_page_error(page_number=page_number, target_selector=target_selector))
            raise ValueError(
                f"分页未确认：等待 {timeout_seconds:g} 秒，未得到稳定的新页面内容。"
                "已停止，不能将部分数据当成完整抓取；请核对翻页动作和控件状态。"
            )
        latest = await read_page(remaining)
        remaining = deadline - clock()
        terminal = (await is_terminal(max(0.001, remaining))
                    if latest.ready and (not latest.rows or allow_navigation_return) else None)
        if latest.ready and not latest.rows and terminal == "next_button_disabled":
            empty_confirmations += 1
        else:
            empty_confirmations = 0
        signature = latest.signature()
        returned = allow_navigation_return and terminal == "navigation_returned_to_previous_page" and signature == before
        changed = signature != before and terminal != "navigation_returned_to_previous_page"
        if latest.ready and latest.rows and (changed or returned):
            if signature == candidate:
                if signature in visited and not returned:
                    raise ValueError("分页出现循环：回到了已采集页面，无法确认抓取完整性，已停止。")
                return latest
            candidate = signature
        else:
            candidate = None
        remaining = deadline - clock()
        if remaining > 0:
            await pause(min(0.3, remaining))


async def collect_paginated_pages(
    read_page: Callable[[float], Awaitable[PaginationPage]],
    advance: Callable[[int, int], Awaitable[None]],
    is_terminal: Callable[[float], Awaitable[str | None]],
    pause: Callable[[float], Awaitable[None]],
    *,
    max_pages: int,
    timeout_seconds: float,
    target_selector: str,
    allow_navigation_return: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> PaginationResult:
    rows: list[object] = []
    counts: list[int] = []
    visited: set[str] = set()
    previous: PaginationPage | None = None
    for page_number in range(1, max_pages + 1):
        snapshot = await wait_for_page_change(
            read_page, pause, previous=previous, visited=visited,
            timeout_seconds=timeout_seconds, page_number=page_number,
            target_selector=target_selector, is_terminal=is_terminal,
            allow_navigation_return=allow_navigation_return, clock=clock,
        )
        terminal = await is_terminal(timeout_seconds)
        if terminal == "navigation_returned_to_previous_page":
            if previous is None or snapshot.signature() != previous.signature() or len(counts) < 2:
                raise ValueError("URL 分页未确认：导航返回已采集页面，但尚无足够翻页进展证明末页；请核对地址模板。")
            return PaginationResult(rows=rows, per_page_counts=counts, stop_reason=terminal)
        if not snapshot.rows and terminal != "next_button_disabled":
            raise ValueError(build_blank_page_error(page_number=page_number, target_selector=target_selector))
        if snapshot.rows:
            rows.extend(snapshot.rows)
            counts.append(len(snapshot.rows))
        if terminal:
            return PaginationResult(rows=rows, per_page_counts=counts, stop_reason=terminal)
        if page_number == max_pages:
            raise ValueError(
                f"分页采集未完成：达到安全上限 {max_pages} 页，尚未确认末页。"
                "请核对分页控件或调整安全上限；不能报告已采集全部。"
            )
        visited.add(snapshot.signature())
        previous = snapshot
        await advance(page_number, len(snapshot.rows))
    raise ValueError("maxIterations 必须大于零。")
