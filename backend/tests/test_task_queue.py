from __future__ import annotations

import asyncio
from collections import deque

import pytest

from app.services.task_queue import InMemoryTaskQueue, RedisTaskQueue


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


@pytest.fixture(params=["memory", "redis"])
def queue_factory(request):
    def create(runner, concurrency=1):
        if request.param == "memory":
            return InMemoryTaskQueue(runner, concurrency=concurrency)
        return RedisTaskQueue(runner, FakeRedis(), queue_name="test:jobs", concurrency=concurrency)
    return create


async def test_failed_job_does_not_strand_the_next_job(queue_factory):
    finished = asyncio.Event()

    async def runner(task_id):
        if task_id == "broken":
            raise RuntimeError("persistence unavailable")
        finished.set()

    queue = queue_factory(runner)
    try:
        await queue.enqueue("broken")
        await queue.enqueue("next")
        await asyncio.wait_for(finished.wait(), timeout=1)
        assert (await queue.snapshot()).queued_count == 0
    finally:
        await queue.stop()


async def test_runner_can_return_a_future_or_raise_before_returning(queue_factory):
    called = asyncio.Event()

    def runner(task_id):
        if task_id == "broken":
            raise RuntimeError("runner setup failed")
        result = asyncio.get_running_loop().create_future()
        result.set_result(None)
        called.set()
        return result

    queue = queue_factory(runner)
    try:
        await queue.enqueue("broken")
        await queue.enqueue("next")
        await asyncio.wait_for(called.wait(), timeout=1)
    finally:
        await queue.stop()


async def test_cancelling_one_job_keeps_workers_and_other_jobs_alive(queue_factory):
    entered, finished, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def runner(task_id):
        if task_id == "cancelled":
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        else:
            finished.set()

    queue = queue_factory(runner)
    try:
        await queue.enqueue("cancelled")
        await queue.enqueue("next")
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert queue.cancel("cancelled")
        await asyncio.wait_for(finished.wait(), timeout=1)
        assert cleaned.is_set()
    finally:
        await queue.stop()


async def test_worker_replacement_preserves_pending_and_active_jobs(queue_factory):
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def runner(task_id):
        calls.append(task_id)
        if task_id == "active":
            entered.set()
            await release.wait()
        if task_id == "last":
            finished.set()

    queue = queue_factory(runner, concurrency=2)
    original_workers = []
    try:
        queue.start()
        original_workers = list(queue._workers)
        await queue.enqueue("active")
        await asyncio.wait_for(entered.wait(), timeout=1)
        queue._workers[1].cancel()
        await asyncio.gather(queue._workers[1], return_exceptions=True)
        await queue.enqueue("pending")
        queue.start()
        await queue.enqueue("last")
        await asyncio.wait_for(finished.wait(), timeout=1)
        for _ in range(20):
            if (await queue.snapshot()).active_task_ids == ["active"]:
                break
            await asyncio.sleep(0)
        assert (await queue.snapshot()).active_task_ids == ["active"]
        release.set()
    finally:
        release.set()
        await queue.stop()
        for worker in original_workers:
            if not worker.done():
                worker.cancel()
        await asyncio.gather(*original_workers, return_exceptions=True)
    assert sorted(calls) == ["active", "last", "pending"]


async def test_duplicate_submission_does_not_run_an_active_job_twice(queue_factory):
    entered, release, duplicate = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def runner(task_id):
        calls.append(task_id)
        entered.set()
        if len(calls) > 1:
            duplicate.set()
        await release.wait()

    queue = queue_factory(runner, concurrency=2)
    try:
        await queue.enqueue("same")
        await asyncio.wait_for(entered.wait(), timeout=1)
        await queue.enqueue("same")
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(duplicate.wait(), timeout=0.05)
        assert calls == ["same"]
    finally:
        release.set()
        await queue.stop()


async def test_concurrent_stop_waits_for_job_cleanup_without_recancelling_it(queue_factory):
    entered, cleaning, release, closed = (asyncio.Event() for _ in range(4))

    async def runner(task_id):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await release.wait()
        closed.set()

    queue = queue_factory(runner)
    await queue.enqueue("job")
    await asyncio.wait_for(entered.wait(), timeout=1)
    first = asyncio.create_task(queue.stop())
    second = None
    try:
        await asyncio.wait_for(cleaning.wait(), timeout=1)
        second = asyncio.create_task(queue.stop())
        await asyncio.sleep(0)
        assert not first.done() and not second.done()
    finally:
        release.set()
        await asyncio.gather(*(task for task in (first, second) if task))
    assert closed.is_set()
    snapshot = await queue.snapshot()
    assert not snapshot.started and snapshot.active_count == 0


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
