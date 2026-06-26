"""Async task queue implementations (in-memory and Redis-backed)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

# Callable signature expected by both queue implementations for executing a task by ID.
TaskRunner = Callable[[str], Awaitable[None]]


class RedisQueueClient(Protocol):
    """Minimal Redis client interface required by RedisTaskQueue (subset of aioredis API)."""

    async def sadd(self, key: str, *values: str) -> int: ...

    async def srem(self, key: str, *values: str) -> int: ...

    async def smembers(self, key: str) -> set[str | bytes]: ...

    async def rpush(self, key: str, value: str) -> int: ...

    async def blpop(self, key: str, timeout: int = 1) -> tuple[str | bytes, str | bytes] | None: ...


@dataclass(frozen=True)
class QueueSnapshot:
    """Point-in-time view of the queue returned by the /api/queue endpoint."""

    backend: str
    concurrency: int
    queued_count: int
    active_count: int
    active_task_ids: list[str] = field(default_factory=list)
    started: bool = False


class TaskQueue(Protocol):
    """Common interface for pluggable queue backends (memory / Redis)."""

    def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def enqueue(self, task_id: str) -> None: ...

    def cancel(self, task_id: str) -> bool: ...

    async def snapshot(self) -> QueueSnapshot: ...


class InMemoryTaskQueue:
    """asyncio-based bounded worker pool; does not survive process restarts."""
    def __init__(self, runner: TaskRunner, concurrency: int = 2) -> None:
        if concurrency < 1:
            raise ValueError("concurrency 必须大于等于 1")
        self._runner = runner
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._active: dict[str, asyncio.Task[None]] = {}
        self._pending: set[str] = set()
        self._concurrency = concurrency
        self._started = False

    def start(self) -> None:
        if self._has_running_workers_in_current_loop():
            return
        self._workers = []
        self._active = {}
        self._pending = set()
        self._queue = asyncio.Queue()
        self._started = True
        self._workers = [asyncio.create_task(self._worker_loop(index)) for index in range(self._concurrency)]

    async def stop(self) -> None:
        if not self._started:
            return
        current_loop = asyncio.get_running_loop()
        if any(worker.get_loop() is not current_loop for worker in self._workers):
            self._workers = []
            self._active = {}
            self._pending = set()
            self._started = False
            return
        for worker_task in tuple(self._active.values()):
            worker_task.cancel()
        while not self._queue.empty():
            try:
                _ = self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        self._pending = set()
        for _ in self._workers:
            await self._queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        self._started = False

    async def enqueue(self, task_id: str) -> None:
        if not self._has_running_workers_in_current_loop():
            self.start()
        self._pending.add(task_id)
        await self._queue.put(task_id)

    def cancel(self, task_id: str) -> bool:
        worker_task = self._active.get(task_id)
        if worker_task is None or worker_task.done():
            return False
        worker_task.cancel()
        return True

    async def snapshot(self) -> QueueSnapshot:
        return QueueSnapshot(
            backend="memory",
            concurrency=self._concurrency,
            queued_count=len(self._pending),
            active_count=len(self._active),
            active_task_ids=sorted(self._active),
            started=self._started,
        )

    def _has_running_workers_in_current_loop(self) -> bool:
        if not self._started or not self._workers:
            return False
        current_loop = asyncio.get_running_loop()
        return all(not worker.done() for worker in self._workers) and all(worker.get_loop() is current_loop for worker in self._workers)

    async def _worker_loop(self, _index: int) -> None:
        while True:
            task_id = await self._queue.get()
            if task_id is None:
                self._queue.task_done()
                return

            current_task = asyncio.current_task()
            if current_task is not None:
                self._active[task_id] = current_task
            self._pending.discard(task_id)
            try:
                await self._runner(task_id)
            finally:
                self._active.pop(task_id, None)
                self._queue.task_done()


class RedisTaskQueue:
    def __init__(
        self,
        runner: TaskRunner,
        redis: RedisQueueClient,
        *,
        queue_name: str = "rpa:tasks",
        concurrency: int = 2,
        poll_timeout_seconds: int = 1,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency 必须大于等于 1")
        if not queue_name.strip():
            raise ValueError("queue_name 不能为空")
        self._runner = runner
        self._redis = redis
        self._queue_name = queue_name
        self._pending_key = f"{queue_name}:pending"
        self._active_key = f"{queue_name}:active"
        self._workers: list[asyncio.Task[None]] = []
        self._active: dict[str, asyncio.Task[None]] = {}
        self._pending: set[str] = set()
        self._concurrency = concurrency
        self._poll_timeout_seconds = poll_timeout_seconds
        self._started = False

    def start(self) -> None:
        current_loop = asyncio.get_running_loop()
        if (
            self._started
            and self._workers
            and all(not worker.done() for worker in self._workers)
            and all(worker.get_loop() is current_loop for worker in self._workers)
        ):
            return
        self._workers = []
        self._active = {}
        self._pending = set()
        self._started = True
        self._workers = [asyncio.create_task(self._worker_loop(index)) for index in range(self._concurrency)]

    async def stop(self) -> None:
        if not self._started:
            return
        current_loop = asyncio.get_running_loop()
        if any(worker.get_loop() is not current_loop for worker in self._workers):
            self._workers = []
            self._active = {}
            self._pending = set()
            self._started = False
            return
        for worker_task in tuple(self._active.values()):
            worker_task.cancel()
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        if self._active:
            await self._redis.srem(self._active_key, *self._active)
        self._workers = []
        self._active = {}
        self._pending = set()
        self._started = False

    async def enqueue(self, task_id: str) -> None:
        if not self._started:
            self.start()
        self._pending.add(task_id)
        await self._redis.sadd(self._pending_key, task_id)
        await self._redis.rpush(self._queue_name, task_id)

    def cancel(self, task_id: str) -> bool:
        worker_task = self._active.get(task_id)
        if worker_task is None or worker_task.done():
            return False
        worker_task.cancel()
        return True

    async def snapshot(self) -> QueueSnapshot:
        pending_ids = await self._redis.smembers(self._pending_key)
        active_ids = await self._redis.smembers(self._active_key)
        return QueueSnapshot(
            backend="redis",
            concurrency=self._concurrency,
            queued_count=len(pending_ids),
            active_count=len(active_ids),
            active_task_ids=sorted(self._decode_task_id(task_id) for task_id in active_ids),
            started=self._started,
        )

    async def _worker_loop(self, _index: int) -> None:
        while True:
            item = await self._redis.blpop(self._queue_name, timeout=self._poll_timeout_seconds)
            if item is None:
                await asyncio.sleep(0)
                continue
            _, raw_task_id = item
            task_id = self._decode_task_id(raw_task_id)
            current_task = asyncio.current_task()
            if current_task is not None:
                self._active[task_id] = current_task
            self._pending.discard(task_id)
            await self._redis.srem(self._pending_key, task_id)
            await self._redis.sadd(self._active_key, task_id)
            try:
                await self._runner(task_id)
            finally:
                self._active.pop(task_id, None)
                await self._redis.srem(self._active_key, task_id)

    @staticmethod
    def _decode_task_id(value: str | bytes) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value
