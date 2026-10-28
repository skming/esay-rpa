from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, TypedDict

from langgraph.graph.state import CompiledStateGraph


class OrchestrationState(TypedDict):
    round_num: int
    route: str


class GraphDelivery:
    """消费者请求下一条事件后才继续图，防止断流时抢跑下一次工具调用。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[dict[str, Any], asyncio.Future[None]]] = asyncio.Queue(maxsize=1)

    async def emit(self, event: dict[str, Any]) -> None:
        acknowledged = asyncio.get_running_loop().create_future()
        await self._queue.put((event, acknowledged))
        await acknowledged

    async def stream(self, graph: CompiledStateGraph, max_rounds: int) -> AsyncIterator[dict[str, Any]]:
        # 节点边界只在本轮有效；跨请求恢复仅使用语义检查点，并重新核对授权与任务服务。
        running = asyncio.create_task(graph.ainvoke(
            {"round_num": 0, "route": "model"},
            {"recursion_limit": max_rounds * 3 + 3},
        ))
        receiving: asyncio.Task | None = None
        try:
            while True:
                receiving = asyncio.create_task(self._queue.get())
                done, _ = await asyncio.wait({running, receiving}, return_when=asyncio.FIRST_COMPLETED)
                if receiving not in done:
                    running.result()
                    return
                event, acknowledged = receiving.result()
                yield event
                if not acknowledged.done():
                    acknowledged.set_result(None)
        finally:
            tasks = [task for task in (receiving, running) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
