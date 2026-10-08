from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.models.schemas import FlowCreateRequest
from app.services.flow_store import SqlAlchemyFlowStore
from app.services.log_broker import LogBroker
from app.services.runtime_factory import create_runtime_services
from app.services.schedule_store import SqlAlchemyScheduleStore
from app.services.task_queue import InMemoryTaskQueue, RedisTaskQueue
from app.services.task_store import SqlAlchemyTaskStore
from tests.test_schedule_store import build_schedule
from tests.test_task_store import build_task_request, build_task_snapshot


@pytest.mark.parametrize("queue_backend", ["memory", "redis"])
async def test_runtime_composes_database_services_and_closes_owned_resources(tmp_path, monkeypatch, queue_backend):
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
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.sqlite3'}",
    )
    runtime = create_runtime_services(settings, LogBroker())
    stores = (runtime.flow_store, runtime.task_store, runtime.schedule_store)
    close_calls = [AsyncMock(wraps=store.close) for store in stores]
    for store, close in zip(stores, close_calls):
        monkeypatch.setattr(store, "close", close)
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
        assert isinstance(runtime.flow_store, SqlAlchemyFlowStore)
        assert isinstance(runtime.task_store, SqlAlchemyTaskStore)
        assert isinstance(runtime.schedule_store, SqlAlchemyScheduleStore)
        assert runtime.flow_service._store is runtime.flow_store
        assert runtime.task_manager._task_store is runtime.task_store
        assert runtime.schedule_service._store is runtime.schedule_store
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
    for close in close_calls:
        close.assert_awaited_once()


async def test_runtime_preserves_flows_tasks_and_schedules_after_recreation(tmp_path):
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'persistent.sqlite3'}")
    runtime = create_runtime_services(settings, LogBroker())
    try:
        await runtime.start()
        flow = await runtime.flow_service.create_flow(FlowCreateRequest(name="persistent-flow"))
        task = build_task_snapshot().model_copy(update={"status": "success", "flow_id": flow.flow_id})
        request = build_task_request().model_copy(update={"flow_id": flow.flow_id})
        await runtime.task_store.save_task(task, request)
        schedule = build_schedule()
        await runtime.schedule_store.save(schedule)
    finally:
        await runtime.close()

    restored = create_runtime_services(settings, LogBroker())
    try:
        await restored.start()
        assert (await restored.flow_service.get_flow(flow.flow_id)).name == "persistent-flow"
        assert (await restored.task_manager.get_task(task.task_id)).status == "success"
        assert (await restored.schedule_store.get(schedule.schedule_id)).name == schedule.name
    finally:
        await restored.close()
