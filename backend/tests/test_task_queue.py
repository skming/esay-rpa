from __future__ import annotations

import asyncio
from collections import deque

from app.services.task_queue import RedisTaskQueue


class FakeRedis:
    def __init__(self) -> None:
        self.queues: dict[str, deque[str]] = {}
        self.sets: dict[str, set[str]] = {}

    async def sadd(self, key: str, *values: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(values)
        return len(bucket) - before

    async def srem(self, key: str, *values: str) -> int:
        bucket = self.sets.setdefault(key, set())
        removed = 0
        for value in values:
            if value in bucket:
                bucket.remove(value)
                removed += 1
        return removed

    async def smembers(self, key: str) -> set[str | bytes]:
        return set(self.sets.setdefault(key, set()))

    async def rpush(self, key: str, value: str) -> int:
        queue = self.queues.setdefault(key, deque())
        queue.append(value)
        return len(queue)

    async def blpop(self, key: str, timeout: int = 1) -> tuple[str, str] | None:
        queue = self.queues.setdefault(key, deque())
        if queue:
            return key, queue.popleft()
        await asyncio.sleep(min(timeout, 1) * 0.01)
        return None


async def test_redis_task_queue_runs_task_and_updates_snapshot() -> None:
    redis = FakeRedis()
    completed: list[str] = []

    async def runner(task_id: str) -> None:
        completed.append(task_id)

    queue = RedisTaskQueue(runner=runner, redis=redis, queue_name="test:rpa:tasks", concurrency=1, poll_timeout_seconds=1)
    queue.start()
    await queue.enqueue("task-1")

    for _ in range(20):
        if completed == ["task-1"]:
            break
        await asyncio.sleep(0.01)

    snapshot = await queue.snapshot()
    assert completed == ["task-1"]
    assert snapshot.backend == "redis"
    assert snapshot.started is True
    assert snapshot.concurrency == 1
    assert snapshot.queued_count == 0
    assert snapshot.active_count == 0
    assert redis.sets["test:rpa:tasks:pending"] == set()
    assert redis.sets["test:rpa:tasks:active"] == set()

    await queue.stop()
    assert (await queue.snapshot()).started is False
