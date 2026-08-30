from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import Boolean, DateTime, String, Text, delete, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator

from app.models.schemas import RunTaskRequest, ScheduleSnapshot


class ScheduleStore(Protocol):
    async def save(self, schedule: ScheduleSnapshot) -> ScheduleSnapshot: ...

    async def list(self) -> list[ScheduleSnapshot]: ...

    async def get(self, schedule_id: str) -> ScheduleSnapshot | None: ...

    async def delete(self, schedule_id: str) -> bool: ...

    async def due(self, at: datetime) -> list[ScheduleSnapshot]: ...


@dataclass
class ScheduleRecord:
    snapshot: ScheduleSnapshot


class InMemoryScheduleStore:
    def __init__(self) -> None:
        self._schedules: dict[str, ScheduleRecord] = {}

    async def save(self, schedule: ScheduleSnapshot) -> ScheduleSnapshot:
        self._schedules[schedule.schedule_id] = ScheduleRecord(snapshot=schedule)
        return schedule

    async def list(self) -> list[ScheduleSnapshot]:
        return sorted((record.snapshot for record in self._schedules.values()), key=lambda item: item.created_at)

    async def get(self, schedule_id: str) -> ScheduleSnapshot | None:
        record = self._schedules.get(schedule_id)
        return record.snapshot if record is not None else None

    async def delete(self, schedule_id: str) -> bool:
        return self._schedules.pop(schedule_id, None) is not None

    async def due(self, at: datetime) -> list[ScheduleSnapshot]:
        return [
            record.snapshot
            for record in self._schedules.values()
            if record.snapshot.status == "enabled" and record.snapshot.next_run_at is not None and record.snapshot.next_run_at <= at
        ]


class Base(DeclarativeBase):
    pass


def _json_type() -> JSON:
    return JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator):
    """SQLite 不保存时区，`DateTime(timezone=True)` 读回的是 naive 时间戳，
    与 `datetime.now(UTC)` 混比会抛 TypeError。写入统一转成 UTC、
    读回统一补上 UTC，让所有 ORM 时间戳天然 timezone-aware。"""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            return value.astimezone(UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class ScheduleRow(Base):
    __tablename__ = "rpa_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    task_payload: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class SqlAlchemyScheduleStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, tables=[ScheduleRow.__table__])

    async def close(self) -> None:
        await self._engine.dispose()

    async def save(self, schedule: ScheduleSnapshot) -> ScheduleSnapshot:
        async with self._session_factory() as session:
            row = await session.get(ScheduleRow, schedule.schedule_id)
            if row is None:
                row = ScheduleRow(id=schedule.schedule_id)
                session.add(row)
            self._apply_snapshot(row, schedule)
            await session.commit()
        return schedule

    async def list(self) -> list[ScheduleSnapshot]:
        async with self._session_factory() as session:
            result = await session.scalars(select(ScheduleRow).order_by(ScheduleRow.created_at.asc()))
            return [self._to_snapshot(row) for row in result]

    async def get(self, schedule_id: str) -> ScheduleSnapshot | None:
        async with self._session_factory() as session:
            row = await session.get(ScheduleRow, schedule_id)
            return self._to_snapshot(row) if row is not None else None

    async def delete(self, schedule_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(delete(ScheduleRow).where(ScheduleRow.id == schedule_id))
            await session.commit()
            return (result.rowcount or 0) > 0

    async def due(self, at: datetime) -> list[ScheduleSnapshot]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(ScheduleRow)
                .where(ScheduleRow.enabled.is_(True), ScheduleRow.next_run_at.is_not(None), ScheduleRow.next_run_at <= at)
                .order_by(ScheduleRow.next_run_at.asc())
            )
            return [self._to_snapshot(row) for row in result]

    @staticmethod
    def _apply_snapshot(row: ScheduleRow, schedule: ScheduleSnapshot) -> None:
        row.name = schedule.name
        row.cron_expression = schedule.cron_expression
        row.timezone = schedule.timezone
        row.enabled = schedule.status == "enabled"
        row.task_payload = schedule.task.model_dump(mode="json", by_alias=True)
        row.last_run_at = schedule.last_run_at
        row.next_run_at = schedule.next_run_at
        row.last_task_id = schedule.last_task_id
        row.last_error = schedule.last_error
        row.created_at = schedule.created_at
        row.updated_at = schedule.updated_at

    @staticmethod
    def _to_snapshot(row: ScheduleRow) -> ScheduleSnapshot:
        return ScheduleSnapshot(
            schedule_id=row.id,
            name=row.name,
            cron_expression=row.cron_expression,
            timezone=row.timezone,
            status="enabled" if row.enabled else "disabled",
            task=RunTaskRequest.model_validate(row.task_payload),
            created_at=row.created_at,
            updated_at=row.updated_at,
            last_run_at=row.last_run_at,
            next_run_at=row.next_run_at,
            last_task_id=row.last_task_id,
            last_error=row.last_error,
        )


def create_schedule_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, future=True)
