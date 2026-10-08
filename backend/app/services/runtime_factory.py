from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.core import storage
from app.core.config import Settings
from app.services.artifact_store import ArtifactStore, LocalArtifactStore, MinioArtifactStore, create_minio_client
from app.services.flow_runner import FlowRunService
from app.services.flow_service import FlowService
from app.services.flow_store import SqlAlchemyFlowStore
from app.services.log_broker import LogBroker
from app.services.schedule_store import SqlAlchemyScheduleStore, create_schedule_engine
from app.services.scrapling_runner import ScraplingRunner
from app.services.scheduler_service import ScheduleService
from app.services.task_manager import TaskManager
from app.services.task_queue import RedisTaskQueue, TaskRunner
from app.services.task_store import SqlAlchemyTaskStore


@dataclass(frozen=True)
class RuntimeServices:
    task_manager: TaskManager
    schedule_service: ScheduleService
    flow_service: FlowService
    flow_run_service: FlowRunService
    schedule_store: SqlAlchemyScheduleStore
    task_store: SqlAlchemyTaskStore
    flow_store: SqlAlchemyFlowStore
    redis: Redis | None = None

    async def start(self) -> None:
        await self.flow_store.create_schema()
        await self.task_store.create_schema()
        await self.schedule_store.create_schema()

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()
        await self.flow_store.close()
        await self.task_store.close()
        await self.schedule_store.close()


def create_runtime_services(settings: Settings, broker: LogBroker) -> RuntimeServices:
    # 组合根：仅应在启动时调用一次，会创建 DB engine / Redis 连接等有状态资源，
    # 对应资源需在关闭时通过 RuntimeServices.close() 释放。

    flow_store = SqlAlchemyFlowStore(create_schedule_engine(settings.database_url))
    flow_service = FlowService(store=flow_store)
    task_store = SqlAlchemyTaskStore(create_schedule_engine(settings.database_url))
    redis = Redis.from_url(settings.redis_url, decode_responses=True) if settings.task_queue_backend == "redis" else None

    def create_redis_queue(runner: TaskRunner) -> RedisTaskQueue:
        assert redis is not None
        return RedisTaskQueue(
            runner=runner,
            redis=redis,
            queue_name=settings.task_queue_name,
            concurrency=settings.task_concurrency,
        )

    manager = TaskManager(
        runner=ScraplingRunner(storage_dir=str(storage.resolve_scrapling_storage_dir())),
        broker=broker,
        artifact_store=_create_artifact_store(settings),
        task_store=task_store,
        flow_service=flow_service,
        concurrency=settings.task_concurrency,
        queue_factory=create_redis_queue if redis is not None else None,
    )
    flow_run_service = FlowRunService(task_manager=manager)
    schedule_store = SqlAlchemyScheduleStore(create_schedule_engine(settings.database_url))
    schedule_service = ScheduleService(
        task_manager=manager, store=schedule_store, flow_service=flow_service, flow_run_service=flow_run_service,
    )
    return RuntimeServices(
        task_manager=manager,
        schedule_service=schedule_service,
        flow_service=flow_service,
        flow_run_service=flow_run_service,
        schedule_store=schedule_store,
        task_store=task_store,
        flow_store=flow_store,
        redis=redis,
    )


def _create_artifact_store(settings: Settings) -> ArtifactStore:
    if settings.artifact_store_backend == "minio":
        client = create_minio_client(
            endpoint=settings.artifact_minio_endpoint,
            access_key=settings.artifact_minio_access_key,
            secret_key=settings.artifact_minio_secret_key,
            secure=settings.artifact_minio_secure,
        )
        return MinioArtifactStore(client=client, bucket=settings.artifact_minio_bucket)
    return LocalArtifactStore()
