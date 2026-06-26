from __future__ import annotations

import asyncio
from collections import defaultdict

from app.models.schemas import TaskLogEntry


class LogBroker:
    """In-process pub/sub for streaming task log entries to WebSocket clients.

    Each WebSocket handler subscribes a bounded queue; the task runner publishes
    entries via `publish`. Slow consumers are protected by dropping old messages
    rather than blocking the runner.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[TaskLogEntry]]] = defaultdict(set)

    def subscribe(self, task_id: str) -> asyncio.Queue[TaskLogEntry]:
        """Return a new queue that will receive log entries for `task_id`."""
        queue: asyncio.Queue[TaskLogEntry] = asyncio.Queue(maxsize=200)
        self._subscribers[task_id].add(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue[TaskLogEntry]) -> None:
        """Remove a subscriber queue and clean up the per-task set when it becomes empty."""
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
