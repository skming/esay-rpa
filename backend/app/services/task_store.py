from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.models.schemas import ArtifactSnapshot, FlowAcceptanceContract, NodeExecutionEvidence, RunConfigSnapshot, RunTaskRequest, RuntimeProgress, RuntimeVariableSnapshot, ScrapeResult, TaskLogEntry, TaskSnapshot
from app.services.schedule_store import Base, UTCDateTime, _json_type


# 进程仍应「持有」这些任务的状态：重启后内存 record 丢失，它们既不能自动继续也不能 /resume，需启动时对账
_UNFINISHED_TASK_STATUSES = ("queued", "running", "awaiting_confirmation")


class TaskStore(Protocol):
    async def save_task(self, task: TaskSnapshot, request: RunTaskRequest) -> TaskSnapshot: ...

    async def get_task(self, task_id: str) -> TaskSnapshot | None: ...

    async def list_tasks(self, *, flow_id: str | None = None, schedule_id: str | None = None, limit: int = 50) -> list[TaskSnapshot]: ...

    async def success_rates_since(self, since: datetime) -> dict[str, int]: ...

    async def append_log(self, log: TaskLogEntry) -> TaskLogEntry: ...

    async def list_logs(self, task_id: str) -> list[TaskLogEntry] | None: ...

    async def list_variables(self, task_id: str) -> list[RuntimeVariableSnapshot] | None: ...

    async def delete_artifacts(self, task_ids: list[str]) -> None: ...

    async def list_unfinished_tasks(self) -> list[TaskSnapshot]: ...

    async def mark_interrupted_stopped(self, task_id: str, *, error: str, log: TaskLogEntry) -> TaskSnapshot | None: ...

    async def has_unfinished_schedule_tasks(self, schedule_id: str) -> bool: ...

    async def list_schedule_batch_tasks(self, schedule_id: str, since: datetime) -> list[TaskSnapshot]: ...


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

    async def success_rates_since(self, since: datetime) -> dict[str, int]:
        counts: dict[str, list[int]] = {}
        for record in self._tasks.values():
            task = record.snapshot
            updated = task.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            if task.flow_id and updated >= since and task.status in {"success", "error", "stopped"}:
                totals = counts.setdefault(task.flow_id, [0, 0])
                totals[0] += task.status == "success"
                totals[1] += 1
        return {flow_id: round(successes / total * 100) for flow_id, (successes, total) in counts.items()}

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

    async def delete_artifacts(self, task_ids: list[str]) -> None:
        for task_id in task_ids:
            record = self._tasks.get(task_id)
            if record is not None:
                record.snapshot = record.snapshot.model_copy(update={"artifacts": []})

    async def list_unfinished_tasks(self) -> list[TaskSnapshot]:
        return [record.snapshot for record in self._tasks.values() if record.snapshot.status in _UNFINISHED_TASK_STATUSES]

    async def has_unfinished_schedule_tasks(self, schedule_id: str) -> bool:
        return any(
            record.snapshot.schedule_id == schedule_id and record.snapshot.status in _UNFINISHED_TASK_STATUSES
            for record in self._tasks.values()
        )

    async def list_schedule_batch_tasks(self, schedule_id: str, since: datetime) -> list[TaskSnapshot]:
        return [
            record.snapshot
            for record in self._tasks.values()
            if record.snapshot.schedule_id == schedule_id and record.snapshot.created_at >= since
        ]

    async def mark_interrupted_stopped(self, task_id: str, *, error: str, log: TaskLogEntry) -> TaskSnapshot | None:
        record = self._tasks.get(task_id)
        if record is None or record.snapshot.status not in _UNFINISHED_TASK_STATUSES:
            return None
        record.snapshot = record.snapshot.model_copy(
            update={"status": "stopped", "error": error, "confirmation_message": None, "updated_at": datetime.now(UTC)}
        )
        record.logs.append(log)
        return record.snapshot


class TaskRow(Base):
    __tablename__ = "rpa_tasks"
    __table_args__ = (
        Index("ix_rpa_tasks_updated_at", "updated_at"),
        Index("ix_rpa_tasks_flow_updated", "flow_id", "updated_at"),
        Index("ix_rpa_tasks_schedule_created", "schedule_id", "created_at"),
        Index("ix_rpa_tasks_rate", "status", "updated_at", "flow_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    flow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schedule_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    flow_name: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="run")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    flow_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    definition_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acceptance_contract: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    run_config: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    progress_payload: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    result_payload: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    execution_evidence_payload: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TaskLogRow(Base):
    __tablename__ = "rpa_task_logs"
    __table_args__ = (Index("ix_rpa_task_logs_task_created", "task_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("rpa_tasks.id", ondelete="CASCADE"), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TaskVariableRow(Base):
    __tablename__ = "rpa_task_variables"
    __table_args__ = (Index("ix_rpa_task_variables_task_name", "task_id", "name"),)

    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("rpa_tasks.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False, default="flow")
    sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ArtifactRow(Base):
    __tablename__ = "rpa_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("rpa_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
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
            for index in TaskRow.__table__.indexes:
                await connection.run_sync(lambda conn, index=index: index.create(conn, checkfirst=True))

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
            if row is None:
                return None
            return (await self._load_snapshots(session, [row]))[0]

    async def list_tasks(self, *, flow_id: str | None = None, schedule_id: str | None = None, limit: int = 50) -> list[TaskSnapshot]:
        normalized_limit = _normalize_limit(limit)
        statement = select(TaskRow).order_by(TaskRow.updated_at.desc()).limit(normalized_limit)
        if flow_id is not None:
            statement = statement.where(TaskRow.flow_id == flow_id)
        if schedule_id is not None:
            statement = statement.where(TaskRow.schedule_id == schedule_id)
        async with self._session_factory() as session:
            result = await session.scalars(statement)
            return await self._load_snapshots(session, list(result))

    async def success_rates_since(self, since: datetime) -> dict[str, int]:
        statement = select(
            TaskRow.flow_id,
            func.sum(case((TaskRow.status == "success", 1), else_=0)),
            func.count(),
        ).where(
            TaskRow.flow_id.is_not(None),
            TaskRow.updated_at >= since,
            TaskRow.status.in_(["success", "error", "stopped"]),
        ).group_by(TaskRow.flow_id)
        async with self._session_factory() as session:
            rows = await session.execute(statement)
            return {flow_id: round(successes / total * 100) for flow_id, successes, total in rows}

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
        async with self._session_factory() as session:
            if await session.get(TaskRow, task_id) is None:
                return None
            result = await session.scalars(select(TaskLogRow).where(TaskLogRow.task_id == task_id).order_by(TaskLogRow.created_at.asc()))
            return [self._to_log(row) for row in result]

    async def list_variables(self, task_id: str) -> list[RuntimeVariableSnapshot] | None:
        async with self._session_factory() as session:
            if await session.get(TaskRow, task_id) is None:
                return None
            result = await session.scalars(select(TaskVariableRow).where(TaskVariableRow.task_id == task_id).order_by(TaskVariableRow.name.asc()))
            return [self._to_variable(row) for row in result]

    async def delete_task(self, task_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(delete(TaskRow).where(TaskRow.id == task_id))
            await session.commit()
            return (result.rowcount or 0) > 0

    async def delete_artifacts(self, task_ids: list[str]) -> None:
        if not task_ids:
            return
        async with self._session_factory() as session:
            await session.execute(delete(ArtifactRow).where(ArtifactRow.task_id.in_(task_ids)))
            await session.commit()

    async def list_unfinished_tasks(self) -> list[TaskSnapshot]:
        statement = select(TaskRow).where(TaskRow.status.in_(_UNFINISHED_TASK_STATUSES))
        async with self._session_factory() as session:
            result = await session.scalars(statement)
            return await self._load_snapshots(session, list(result))

    async def has_unfinished_schedule_tasks(self, schedule_id: str) -> bool:
        statement = select(TaskRow.id).where(
            TaskRow.schedule_id == schedule_id,
            TaskRow.status.in_(_UNFINISHED_TASK_STATUSES),
        ).limit(1)
        async with self._session_factory() as session:
            return await session.scalar(statement) is not None

    async def list_schedule_batch_tasks(self, schedule_id: str, since: datetime) -> list[TaskSnapshot]:
        statement = select(TaskRow).where(
            TaskRow.schedule_id == schedule_id,
            TaskRow.created_at >= since,
        ).order_by(TaskRow.created_at.asc())
        async with self._session_factory() as session:
            result = await session.scalars(statement)
            return await self._load_snapshots(session, list(result))

    async def mark_interrupted_stopped(self, task_id: str, *, error: str, log: TaskLogEntry) -> TaskSnapshot | None:
        # 状态翻转与终止日志同一事务提交，避免对账中途崩溃留下「已停但无留痕」的半截状态
        async with self._session_factory() as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.status not in _UNFINISHED_TASK_STATUSES:
                return None
            now = datetime.now(UTC)
            row.status = "stopped"
            row.error_message = error
            row.confirmation_message = None
            row.finished_at = now
            row.updated_at = now
            session.add(
                TaskLogRow(
                    id=log.id,
                    task_id=log.task_id,
                    level=log.level,
                    message=log.message,
                    detail=log.detail,
                    node_id=log.node_id,
                    created_at=log.time,
                )
            )
            await session.commit()
            return (await self._load_snapshots(session, [row]))[0]

    @staticmethod
    def _apply_task(row: TaskRow, task: TaskSnapshot, request: RunTaskRequest) -> None:
        row.flow_name = task.flow_name
        row.flow_id = task.flow_id
        row.schedule_id = task.schedule_id
        row.mode = task.mode
        row.status = task.status
        row.flow_revision = task.flow_revision or request.flow_revision
        row.definition_digest = task.definition_digest or request.definition_digest
        contract = task.acceptance_contract
        if not contract.requirements and not contract.deliverables:
            contract = request.acceptance_contract
        row.acceptance_contract = contract.model_dump(mode="json", by_alias=True)
        row.run_config = task.run_config.model_dump(mode="json", by_alias=True)
        row.progress_payload = task.progress.model_dump(mode="json", by_alias=True)
        row.result_payload = task.result.model_dump(mode="json", by_alias=True) if task.result is not None else None
        row.execution_evidence_payload = [
            evidence.model_dump(mode="json", by_alias=True) for evidence in task.execution_evidence
        ]
        row.error_message = task.error
        row.confirmation_message = task.confirmation_message
        row.created_at = task.created_at
        if task.status == "running" and row.started_at is None:
            row.started_at = task.updated_at
        # 非终态时强制清空 finished_at，保证任务被重新排队/执行时耗时统计不会沿用上一轮的结束时间
        row.finished_at = task.updated_at if task.status in {"success", "stopped", "error"} else None
        row.updated_at = task.updated_at

    @staticmethod
    def _to_snapshot(
        row: TaskRow,
        *,
        variables: list[RuntimeVariableSnapshot],
        artifacts: list[ArtifactSnapshot],
    ) -> TaskSnapshot:
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
            artifacts=artifacts,
            variables=variables,
            flow_revision=row.flow_revision,
            definition_digest=row.definition_digest,
            acceptance_contract=FlowAcceptanceContract.model_validate(row.acceptance_contract or {}),
            execution_evidence=[
                NodeExecutionEvidence.model_validate(evidence)
                for evidence in (getattr(row, "execution_evidence_payload", None) or [])
            ],
            run_config=RunConfigSnapshot.model_validate(row.run_config or {}),
            error=row.error_message,
            confirmation_message=row.confirmation_message,
        )

    @staticmethod
    def _to_log(row: TaskLogRow) -> TaskLogEntry:
        return TaskLogEntry(id=row.id, task_id=row.task_id, time=row.created_at, level=row.level, message=row.message, detail=row.detail, node_id=row.node_id)

    @staticmethod
    def _to_variable_row(task: TaskSnapshot, variable: RuntimeVariableSnapshot) -> TaskVariableRow:
        protected = variable.sensitive or variable.category == "credential"
        return TaskVariableRow(
            id=f"{task.task_id}:{variable.name}",
            task_id=task.task_id,
            name=variable.name,
            category=variable.category,
            sensitive=protected,
            scope=variable.scope,
            type=variable.type,
            value="" if protected else variable.value,
            updated_at=task.updated_at,
        )

    @staticmethod
    def _to_variable(row: TaskVariableRow) -> RuntimeVariableSnapshot:
        return RuntimeVariableSnapshot(
            name=row.name,
            category=row.category,
            sensitive=row.sensitive,
            scope=row.scope,
            type=row.type,
            value=row.value,
        )

    @staticmethod
    def _to_artifact_row(task: TaskSnapshot, artifact: ArtifactSnapshot) -> ArtifactRow:
        return ArtifactRow(
            id=artifact.artifact_id,
            task_id=artifact.task_id,
            artifact_type=artifact.artifact_type,
            filename=artifact.filename,
            storage_url=artifact.storage_url,
            content_type=artifact.content_type,
            size_bytes=artifact.size_bytes,
            metadata_payload=artifact.metadata,
            created_at=artifact.created_at,
        )

    async def _load_snapshots(self, session, rows: list[TaskRow]) -> list[TaskSnapshot]:
        if not rows:
            return []
        task_ids = [row.id for row in rows]
        variable_rows = await session.scalars(
            select(TaskVariableRow)
            .where(TaskVariableRow.task_id.in_(task_ids))
            .order_by(TaskVariableRow.task_id, TaskVariableRow.name)
        )
        artifact_rows = await session.scalars(
            select(ArtifactRow)
            .where(ArtifactRow.task_id.in_(task_ids))
            .order_by(ArtifactRow.task_id, ArtifactRow.created_at)
        )
        variables: dict[str, list[RuntimeVariableSnapshot]] = {task_id: [] for task_id in task_ids}
        artifacts: dict[str, list[ArtifactSnapshot]] = {task_id: [] for task_id in task_ids}
        for variable in variable_rows:
            variables[variable.task_id].append(self._to_variable(variable))
        for artifact in artifact_rows:
            artifacts[artifact.task_id].append(self._to_artifact(artifact))
        return [
            self._to_snapshot(row, variables=variables[row.id], artifacts=artifacts[row.id])
            for row in rows
        ]

    @staticmethod
    def _to_artifact(row: ArtifactRow) -> ArtifactSnapshot:
        return ArtifactSnapshot(
            artifactId=row.id,
            taskId=row.task_id,
            artifactType=row.artifact_type,
            filename=row.filename,
            storageUrl=row.storage_url,
            contentType=row.content_type,
            sizeBytes=row.size_bytes,
            metadata=row.metadata_payload,
            createdAt=row.created_at,
        )



def _normalize_limit(limit: int) -> int:
    if limit < 1:
        return 1
    return min(limit, 200)
