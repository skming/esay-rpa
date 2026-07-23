"""Playwright 驱动与扩展驱动两种浏览器执行器共用的接口（Protocol 结构化匹配，无需改动实现类）。
create_context 返回的 context 对每个执行器的形状不同（Playwright 是 page/browser 句柄，扩展执行器只需知道已连接），
故意用 object 而非统一 dataclass。"""
from __future__ import annotations

from typing import Protocol

from app.services.browser_action_runner import BrowserActionResult, FlowNode
from app.services.runtime_variables import RuntimeVariableStore


class BrowserExecutor(Protocol):
    async def create_context(self, *, headless: bool = True) -> object: ...

    async def close_context(self, context: object | None) -> None: ...

    async def screenshot(self, context: object) -> bytes: ...

    async def run(
        self, node: FlowNode, variables: RuntimeVariableStore, context: object, *, timeout_ms: int
    ) -> BrowserActionResult: ...
