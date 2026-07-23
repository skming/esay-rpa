from __future__ import annotations

import asyncio
from collections import defaultdict

from app.models.schemas import TaskLogEntry


class LogBroker:
    """进程内 pub/sub，向 WebSocket 客户端推送任务日志；队列满时丢旧消息而非阻塞任务执行。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[TaskLogEntry]]] = defaultdict(set)

    def subscribe(self, task_id: str) -> asyncio.Queue[TaskLogEntry]:
        queue: asyncio.Queue[TaskLogEntry] = asyncio.Queue(maxsize=200)  # 上限经验值：足够覆盖突发日志，超出即视为慢客户端
        self._subscribers[task_id].add(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue[TaskLogEntry]) -> None:
        subscribers = self._subscribers.get(task_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(task_id, None)

    async def publish(self, entry: TaskLogEntry) -> None:
        # 队列满时丢弃最旧消息，避免慢客户端拖垮执行任务。
        for queue in tuple(self._subscribers.get(entry.task_id, set())):
            if queue.full():
                _ = queue.get_nowait()
            await queue.put(entry)
