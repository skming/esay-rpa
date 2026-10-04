from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.services.log_broker import LogBroker
from app.services.runtime_factory import create_runtime_services
from app.services.task_queue import InMemoryTaskQueue, RedisTaskQueue


@pytest.mark.parametrize("queue_backend", ["memory", "redis"])
@pytest.mark.parametrize("store_backend", ["memory", "sqlalchemy"])
async def test_runtime_composes_services_and_closes_owned_resources(tmp_path, monkeypatch, queue_backend, store_backend):
    redis = AsyncMock()
    from_url_calls = []

    def from_url(url, **options):
        from_url_calls.append((url, options))
        return redis

    monkeypatch.setattr("app.services.runtime_factory.Redis.from_url", from_url)
    settings = Settings(
        task_queue_backend=queue_backend,
        task_concurrency=3,
        task_queue_name="test:queue",
        flow_store_backend=store_backend,
        task_store_backend=store_backend,
        schedule_store_backend=store_backend,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.sqlite3'}",
    )
    runtime = create_runtime_services(settings, LogBroker())
    try:
        await runtime.start()
        stats = await runtime.task_manager.queue_stats()
        assert stats.backend == queue_backend
        assert stats.concurrency == 3
        assert runtime.flow_run_service._task_manager is runtime.task_manager
        assert runtime.schedule_service._task_manager is runtime.task_manager
        assert runtime.schedule_service._flow_service is runtime.flow_service
        assert runtime.schedule_service._flow_run_service is runtime.flow_run_service
        assert isinstance(runtime.task_manager._queue, RedisTaskQueue if queue_backend == "redis" else InMemoryTaskQueue)
        assert (runtime.flow_store is not None) == (store_backend == "sqlalchemy")
        assert (runtime.task_store is not None) == (store_backend == "sqlalchemy")
        assert (runtime.schedule_store is not None) == (store_backend == "sqlalchemy")
        if queue_backend == "redis":
            assert from_url_calls == [(settings.redis_url, {"decode_responses": True})]
            assert runtime.redis is redis
            assert runtime.task_manager._queue._queue_name == "test:queue"
        else:
            assert from_url_calls == []
            assert runtime.redis is None
    finally:
        await runtime.close()
    assert redis.aclose.await_count == (1 if queue_backend == "redis" else 0)
