from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.core import storage
from app.core.config import Settings
from app.services.artifact_store import ArtifactStore, LocalArtifactStore, MinioArtifactStore, create_minio_client
from app.services.flow_runner import FlowRunService
from app.services.flow_service import FlowService
from app.services.flow_store import FlowStore, SqlAlchemyFlowStore
from app.services.log_broker import LogBroker
from app.services.schedule_store import SqlAlchemyScheduleStore, create_schedule_engine
from app.services.scrapling_runner import ScraplingRunner
from app.services.scheduler_service import ScheduleService
from app.services.task_manager import TaskManager
from app.services.task_queue import RedisTaskQueue
from app.services.task_store import SqlAlchemyTaskStore, TaskStore


@dataclass(frozen=True)
class RuntimeServices:
    task_manager: TaskManager
    schedule_service: ScheduleService
    flow_service: FlowService
    flow_run_service: FlowRunService
    schedule_store: SqlAlchemyScheduleStore | None = None
    task_store: SqlAlchemyTaskStore | None = None
    flow_store: SqlAlchemyFlowStore | None = None
    redis: Redis | None = None

    async def start(self) -> None:
        if self.flow_store is not None:
            await self.flow_store.create_schema()
        if self.task_store is not None:
            await self.task_store.create_schema()
        if self.schedule_store is not None:
            await self.schedule_store.create_schema()

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()
        if self.flow_store is not None:
            await self.flow_store.close()
        if self.task_store is not None:
            await self.task_store.close()
        if self.schedule_store is not None:
            await self.schedule_store.close()


def create_runtime_services(settings: Settings, broker: LogBroker) -> RuntimeServices:
    # 组合根：仅应在启动时调用一次，会创建 DB engine / Redis 连接等有状态资源，
    # 对应资源需在关闭时通过 RuntimeServices.close() 释放。

    flow_store = _create_flow_store(settings)
    flow_service = FlowService(store=flow_store)
    task_store = _create_task_store(settings)
    if settings.task_queue_backend == "redis":
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        manager = TaskManager(
            runner=ScraplingRunner(storage_dir=str(storage.resolve_scrapling_storage_dir())),
            broker=broker,
            artifact_store=_create_artifact_store(settings),
            task_store=task_store,
            flow_service=flow_service,
            concurrency=settings.task_concurrency,
            queue_factory=lambda runner: RedisTaskQueue(
                runner=runner,
                redis=redis,
                queue_name=settings.task_queue_name,
                concurrency=settings.task_concurrency,
            ),
        )
        flow_run_service = FlowRunService(task_manager=manager)
        schedule_service, schedule_store = _create_schedule_service(settings, manager, flow_service, flow_run_service)
        return RuntimeServices(
            task_manager=manager,
            schedule_service=schedule_service,
            flow_service=flow_service,
            flow_run_service=flow_run_service,
            schedule_store=schedule_store,
            task_store=task_store if isinstance(task_store, SqlAlchemyTaskStore) else None,
            flow_store=flow_store if isinstance(flow_store, SqlAlchemyFlowStore) else None,
            redis=redis,
        )

    manager = TaskManager(
        runner=ScraplingRunner(storage_dir=str(storage.resolve_scrapling_storage_dir())),
        broker=broker,
        artifact_store=_create_artifact_store(settings),
        task_store=task_store,
        flow_service=flow_service,
        concurrency=settings.task_concurrency,
    )
    flow_run_service = FlowRunService(task_manager=manager)
    schedule_service, schedule_store = _create_schedule_service(settings, manager, flow_service, flow_run_service)
    return RuntimeServices(
        task_manager=manager,
        schedule_service=schedule_service,
        flow_service=flow_service,
        flow_run_service=flow_run_service,
        schedule_store=schedule_store,
        task_store=task_store if isinstance(task_store, SqlAlchemyTaskStore) else None,
        flow_store=flow_store if isinstance(flow_store, SqlAlchemyFlowStore) else None,
    )


def _create_flow_store(settings: Settings) -> FlowStore | None:
    if settings.flow_store_backend == "sqlalchemy":
        return SqlAlchemyFlowStore(create_schedule_engine(settings.database_url))
    return None


def _create_task_store(settings: Settings) -> TaskStore | None:
    if settings.task_store_backend == "sqlalchemy":
        return SqlAlchemyTaskStore(create_schedule_engine(settings.database_url))
    return None


def _create_schedule_service(
    settings: Settings,
    task_manager: TaskManager,
    flow_service: FlowService,
    flow_run_service: FlowRunService,
) -> tuple[ScheduleService, SqlAlchemyScheduleStore | None]:
    if settings.schedule_store_backend == "sqlalchemy":
        store = SqlAlchemyScheduleStore(create_schedule_engine(settings.database_url))
        return ScheduleService(task_manager=task_manager, store=store, flow_service=flow_service, flow_run_service=flow_run_service), store
    return ScheduleService(task_manager=task_manager, flow_service=flow_service, flow_run_service=flow_run_service), None


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
