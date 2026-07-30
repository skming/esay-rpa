from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy import BigInteger, Integer, String, Text, delete, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.models.schemas import ArtifactSnapshot, FlowAcceptanceContract, NodeExecutionEvidence, RunConfigSnapshot, RunTaskRequest, RuntimeProgress, RuntimeVariableSnapshot, ScrapeResult, TaskLogEntry, TaskSnapshot
from app.services.schedule_store import Base, UTCDateTime, _json_type


class TaskStore(Protocol):
    async def save_task(self, task: TaskSnapshot, request: RunTaskRequest) -> TaskSnapshot: ...

    async def get_task(self, task_id: str) -> TaskSnapshot | None: ...

    async def list_tasks(self, *, flow_id: str | None = None, schedule_id: str | None = None, limit: int = 50) -> list[TaskSnapshot]: ...

    async def append_log(self, log: TaskLogEntry) -> TaskLogEntry: ...

    async def list_logs(self, task_id: str) -> list[TaskLogEntry] | None: ...

    async def list_variables(self, task_id: str) -> list[RuntimeVariableSnapshot] | None: ...


@dataclass
class TaskStoreRecord:
    request: RunTaskRequest
    snapshot: TaskSnapshot
    logs: list[TaskLogEntry] = field(default_factory=list)


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskStoreRecord] = {}

    async def save_task(self, task: TaskSnapshot, request: RunTaskRequest) -> TaskSnapshot:
        record = self._tasks.get(task.task_id)
        logs = record.logs if record is not None else []
        self._tasks[task.task_id] = TaskStoreRecord(request=request, snapshot=task, logs=logs)
        return task

    async def get_task(self, task_id: str) -> TaskSnapshot | None:
        record = self._tasks.get(task_id)
        return record.snapshot if record is not None else None

    async def list_tasks(self, *, flow_id: str | None = None, schedule_id: str | None = None, limit: int = 50) -> list[TaskSnapshot]:
        snapshots = [record.snapshot for record in self._tasks.values()]
        if flow_id is not None:
            snapshots = [s for s in snapshots if s.flow_id == flow_id]
        if schedule_id is not None:
            snapshots = [s for s in snapshots if s.schedule_id == schedule_id]
        return sorted(snapshots, key=lambda snapshot: snapshot.updated_at, reverse=True)[: _normalize_limit(limit)]

    async def append_log(self, log: TaskLogEntry) -> TaskLogEntry:
        record = self._tasks.get(log.task_id)
        if record is not None:
            record.logs.append(log)
        return log

    async def list_logs(self, task_id: str) -> list[TaskLogEntry] | None:
        record = self._tasks.get(task_id)
        if record is None:
            return None
        return list(record.logs)

    async def list_variables(self, task_id: str) -> list[RuntimeVariableSnapshot] | None:
        record = self._tasks.get(task_id)
        if record is None:
            return None
        return list(record.snapshot.variables)


class TaskRow(Base):
    __tablename__ = "rpa_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    flow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schedule_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    flow_name: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="run")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    target_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    selector: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fetcher: Mapped[str] = mapped_column(String(24), nullable=False, default="static")
    extract_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="text")
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=30000)
    request_payload: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    progress_payload: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    result_payload: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    artifacts_payload: Mapped[list] = mapped_column(_json_type(), nullable=False)
    variables_payload: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)
    execution_evidence_payload: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_takeover_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_takeover_resume_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TaskLogRow(Base):
    __tablename__ = "rpa_task_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TaskVariableRow(Base):
    __tablename__ = "rpa_task_variables"

    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    flow_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ArtifactRow(Base):
    __tablename__ = "rpa_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    flow_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    metadata_payload: Mapped[dict] = mapped_column("metadata", _json_type(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class SqlAlchemyTaskStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, tables=[TaskRow.__table__, TaskLogRow.__table__, TaskVariableRow.__table__, ArtifactRow.__table__])
            await connection.run_sync(_ensure_task_columns)

    async def close(self) -> None:
        await self._engine.dispose()

    async def save_task(self, task: TaskSnapshot, request: RunTaskRequest) -> TaskSnapshot:
        async with self._session_factory() as session:
            row = await session.get(TaskRow, task.task_id)
            if row is None:
                row = TaskRow(id=task.task_id)
                session.add(row)
            self._apply_task(row, task, request)
            # variables/artifacts 按整份快照全量替换而非增量 diff，避免节点重跑后残留旧值
            await session.execute(delete(TaskVariableRow).where(TaskVariableRow.task_id == task.task_id))
            for variable in task.variables:
                session.add(self._to_variable_row(task, variable))
            await session.execute(delete(ArtifactRow).where(ArtifactRow.task_id == task.task_id))
            for artifact in task.artifacts:
                session.add(self._to_artifact_row(task, artifact))
            await session.commit()
        return task

    async def get_task(self, task_id: str) -> TaskSnapshot | None:
        async with self._session_factory() as session:
            row = await session.get(TaskRow, task_id)
            return self._to_snapshot(row) if row is not None else None

    async def list_tasks(self, *, flow_id: str | None = None, schedule_id: str | None = None, limit: int = 50) -> list[TaskSnapshot]:
        normalized_limit = _normalize_limit(limit)
        statement = select(TaskRow).order_by(TaskRow.updated_at.desc()).limit(normalized_limit)
        if flow_id is not None:
            statement = statement.where(TaskRow.flow_id == flow_id)
        if schedule_id is not None:
            statement = statement.where(TaskRow.schedule_id == schedule_id)
        async with self._session_factory() as session:
            result = await session.scalars(statement)
            return [self._to_snapshot(row) for row in result]

    async def append_log(self, log: TaskLogEntry) -> TaskLogEntry:
        async with self._session_factory() as session:
            row = TaskLogRow(
                id=log.id,
                task_id=log.task_id,
                level=log.level,
                message=log.message,
                detail=log.detail,
                node_id=log.node_id,
                created_at=log.time,
            )
            session.add(row)
            await session.commit()
        return log

    async def list_logs(self, task_id: str) -> list[TaskLogEntry] | None:
        if await self.get_task(task_id) is None:
            return None
        async with self._session_factory() as session:
            result = await session.scalars(select(TaskLogRow).where(TaskLogRow.task_id == task_id).order_by(TaskLogRow.created_at.asc()))
            return [self._to_log(row) for row in result]

    async def list_variables(self, task_id: str) -> list[RuntimeVariableSnapshot] | None:
        if await self.get_task(task_id) is None:
            return None
        async with self._session_factory() as session:
            result = await session.scalars(select(TaskVariableRow).where(TaskVariableRow.task_id == task_id).order_by(TaskVariableRow.name.asc()))
            return [self._to_variable(row) for row in result]

    async def delete_task(self, task_id: str) -> bool:
        async with self._session_factory() as session:
            await session.execute(delete(TaskLogRow).where(TaskLogRow.task_id == task_id))
            await session.execute(delete(TaskVariableRow).where(TaskVariableRow.task_id == task_id))
            await session.execute(delete(ArtifactRow).where(ArtifactRow.task_id == task_id))
            result = await session.execute(delete(TaskRow).where(TaskRow.id == task_id))
            await session.commit()
            return (result.rowcount or 0) > 0

    @staticmethod
    def _apply_task(row: TaskRow, task: TaskSnapshot, request: RunTaskRequest) -> None:
        row.flow_name = task.flow_name
        row.flow_id = task.flow_id
        row.schedule_id = task.schedule_id
        row.mode = task.mode
        row.status = task.status
        row.target_url = str(request.target_url) if request.target_url is not None else ""
        row.selector = request.selector or ""
        row.fetcher = request.fetcher
        row.extract_mode = request.extract_mode
        row.timeout_ms = request.timeout_ms
        row.request_payload = request.model_dump(mode="json", by_alias=True)
        row.progress_payload = task.progress.model_dump(mode="json", by_alias=True)
        row.result_payload = task.result.model_dump(mode="json", by_alias=True) if task.result is not None else None
        row.artifacts_payload = [artifact.model_dump(mode="json", by_alias=True) for artifact in task.artifacts]
        row.variables_payload = [variable.model_dump(mode="json", by_alias=True) for variable in task.variables]
        row.execution_evidence_payload = [
            evidence.model_dump(mode="json", by_alias=True) for evidence in task.execution_evidence
        ]
        row.error_message = task.error
        row.input_prompt = task.input_prompt
        row.human_takeover_message = task.human_takeover_message
        row.human_takeover_resume_mode = task.human_takeover_resume_mode
        row.created_at = task.created_at
        if task.status == "running" and row.started_at is None:
            row.started_at = task.updated_at
        # 非终态时强制清空 finished_at，保证任务被重新排队/执行时耗时统计不会沿用上一轮的结束时间
        row.finished_at = task.updated_at if task.status in {"success", "stopped", "error"} else None
        row.updated_at = task.updated_at

    @staticmethod
    def _to_snapshot(row: TaskRow) -> TaskSnapshot:
        return TaskSnapshot(
            task_id=row.id,
            flow_id=row.flow_id,
            schedule_id=getattr(row, "schedule_id", None),
            flow_name=row.flow_name,
            status=row.status,
            mode=row.mode,
            progress=RuntimeProgress.model_validate(row.progress_payload),
            created_at=row.created_at,
            updated_at=row.updated_at,
            result=ScrapeResult.model_validate(row.result_payload) if row.result_payload is not None else None,
            artifacts=[ArtifactSnapshot.model_validate(artifact) for artifact in row.artifacts_payload],
            variables=[RuntimeVariableSnapshot.model_validate(variable) for variable in row.variables_payload],
            flow_revision=_payload_value(row.request_payload, "flowRevision", "flow_revision"),
            definition_digest=_payload_value(row.request_payload, "definitionDigest", "definition_digest"),
            acceptance_contract=FlowAcceptanceContract.model_validate(
                _payload_value(row.request_payload, "acceptanceContract", "acceptance_contract") or {}
            ),
            execution_evidence=[
                NodeExecutionEvidence.model_validate(evidence)
                for evidence in (getattr(row, "execution_evidence_payload", None) or [])
            ],
            run_config=_run_config_from_payload(row.request_payload),
            error=row.error_message,
            input_prompt=getattr(row, "input_prompt", None),
            human_takeover_message=getattr(row, "human_takeover_message", None),
            human_takeover_resume_mode=getattr(row, "human_takeover_resume_mode", None),
        )

    @staticmethod
    def _to_log(row: TaskLogRow) -> TaskLogEntry:
        return TaskLogEntry(id=row.id, task_id=row.task_id, time=row.created_at, level=row.level, message=row.message, detail=row.detail, node_id=row.node_id)

    @staticmethod
    def _to_variable_row(task: TaskSnapshot, variable: RuntimeVariableSnapshot) -> TaskVariableRow:
        return TaskVariableRow(
            id=f"{task.task_id}:{variable.name}",
            task_id=task.task_id,
            flow_id=task.flow_id,
            name=variable.name,
            scope=variable.scope,
            type=variable.type,
            value=variable.value,
            updated_at=task.updated_at,
        )

    @staticmethod
    def _to_variable(row: TaskVariableRow) -> RuntimeVariableSnapshot:
        return RuntimeVariableSnapshot(name=row.name, scope=row.scope, type=row.type, value=row.value)

    @staticmethod
    def _to_artifact_row(task: TaskSnapshot, artifact: ArtifactSnapshot) -> ArtifactRow:
        return ArtifactRow(
            id=artifact.artifact_id,
            task_id=artifact.task_id,
            flow_id=task.flow_id,
            artifact_type=artifact.artifact_type,
            filename=artifact.filename,
            storage_url=artifact.storage_url,
            content_type=artifact.content_type,
            size_bytes=artifact.size_bytes,
            metadata_payload=artifact.metadata,
            created_at=artifact.created_at,
        )


def _ensure_task_columns(connection) -> None:
    """补充后续迁移新增的列，不丢已有数据（简易 ALTER TABLE，非正式 migration 框架）。"""
    log_columns = {column["name"] for column in inspect(connection).get_columns(TaskLogRow.__tablename__)}
    if "node_id" not in log_columns:
        connection.execute(text("ALTER TABLE rpa_task_logs ADD COLUMN node_id VARCHAR(120)"))
    task_columns = {column["name"] for column in inspect(connection).get_columns(TaskRow.__tablename__)}
    if "variables_payload" not in task_columns:
        connection.execute(text("ALTER TABLE rpa_tasks ADD COLUMN variables_payload JSON DEFAULT '[]' NOT NULL"))
    if "execution_evidence_payload" not in task_columns:
        connection.execute(text("ALTER TABLE rpa_tasks ADD COLUMN execution_evidence_payload JSON DEFAULT '[]' NOT NULL"))
    if "schedule_id" not in task_columns:
        connection.execute(text("ALTER TABLE rpa_tasks ADD COLUMN schedule_id VARCHAR(36)"))
    if "input_prompt" not in task_columns:
        connection.execute(text("ALTER TABLE rpa_tasks ADD COLUMN input_prompt TEXT"))
    if "human_takeover_message" not in task_columns:
        connection.execute(text("ALTER TABLE rpa_tasks ADD COLUMN human_takeover_message TEXT"))
    if "human_takeover_resume_mode" not in task_columns:
        connection.execute(text("ALTER TABLE rpa_tasks ADD COLUMN human_takeover_resume_mode VARCHAR(32)"))


def _normalize_limit(limit: int) -> int:
    if limit < 1:
        return 1
    return min(limit, 200)


def _payload_value(payload: dict | None, camel: str, snake: str):
    if not isinstance(payload, dict):
        return None
    return payload.get(camel, payload.get(snake))


def _run_config_from_payload(payload: dict | None) -> RunConfigSnapshot:
    if not isinstance(payload, dict):
        return RunConfigSnapshot()
    # 同时兼容 camelCase（当前）与 snake_case（历史存量 request_payload）两种字段名
    return RunConfigSnapshot.model_validate(
        {
            "scope": payload.get("scope", "full"),
            "startNodeId": payload.get("startNodeId") or payload.get("start_node_id"),
            "failureStrategy": payload.get("failureStrategy", payload.get("failure_strategy", "stop")),
            "screenshot": payload.get("screenshot", True),
            "concurrency": payload.get("concurrency", 1),
        }
    )
