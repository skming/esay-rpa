from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import DateTime, String, Text, delete, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.models.schemas import FlowSnapshot, FlowVersionSnapshot
from app.services.schedule_store import Base, _json_type


class FlowStore(Protocol):
    async def save(self, flow: FlowSnapshot) -> FlowSnapshot: ...

    async def list(self) -> list[FlowSnapshot]: ...

    async def get(self, flow_id: str) -> FlowSnapshot | None: ...

    async def delete(self, flow_id: str) -> bool: ...


@dataclass
class FlowRecord:
    snapshot: FlowSnapshot


class InMemoryFlowStore:
    def __init__(self) -> None:
        self._flows: dict[str, FlowRecord] = {}

    async def save(self, flow: FlowSnapshot) -> FlowSnapshot:
        self._flows[flow.flow_id] = FlowRecord(snapshot=flow)
        return flow

    async def list(self) -> list[FlowSnapshot]:
        return sorted((record.snapshot for record in self._flows.values()), key=lambda item: item.updated_at, reverse=True)

    async def get(self, flow_id: str) -> FlowSnapshot | None:
        record = self._flows.get(flow_id)
        return record.snapshot if record is not None else None

    async def delete(self, flow_id: str) -> bool:
        return self._flows.pop(flow_id, None) is not None


class FlowRow(Base):
    __tablename__ = "rpa_flows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1.0.0")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    input_variables: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    folder_path: Mapped[str] = mapped_column(String(500), nullable=False, default="默认目录")
    last_run_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshots: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)


class SqlAlchemyFlowStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, tables=[FlowRow.__table__])
            await connection.run_sync(_ensure_flow_columns)

    async def close(self) -> None:
        await self._engine.dispose()

    async def save(self, flow: FlowSnapshot) -> FlowSnapshot:
        async with self._session_factory() as session:
            row = await session.get(FlowRow, flow.flow_id)
            if row is None:
                row = FlowRow(id=flow.flow_id)
                session.add(row)
            self._apply_snapshot(row, flow)
            await session.commit()
        return flow

    async def list(self) -> list[FlowSnapshot]:
        async with self._session_factory() as session:
            result = await session.scalars(select(FlowRow).order_by(FlowRow.updated_at.desc()))
            return [self._to_snapshot(row) for row in result]

    async def get(self, flow_id: str) -> FlowSnapshot | None:
        async with self._session_factory() as session:
            row = await session.get(FlowRow, flow_id)
            return self._to_snapshot(row) if row is not None else None

    async def delete(self, flow_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(delete(FlowRow).where(FlowRow.id == flow_id))
            await session.commit()
            return (result.rowcount or 0) > 0

    @staticmethod
    def _apply_snapshot(row: FlowRow, flow: FlowSnapshot) -> None:
        row.name = flow.name
        row.version = flow.version
        row.description = flow.description
        row.definition = flow.definition
        row.input_variables = [variable.model_dump(mode="json", by_alias=True) for variable in flow.input_variables]
        row.status = flow.status
        row.folder_path = flow.folder_path
        row.last_run_status = flow.last_run_status
        row.last_run_at = flow.last_run_at
        row.created_at = flow.created_at
        row.updated_at = flow.updated_at
        row.snapshots = [s.model_dump(mode="json", by_alias=True) for s in flow.snapshots]

    @staticmethod
    def _to_snapshot(row: FlowRow) -> FlowSnapshot:
        return FlowSnapshot(
            flowId=row.id,
            name=row.name,
            version=row.version,
            description=row.description,
            definition=row.definition,
            inputVariables=row.input_variables,
            status=row.status,
            folderPath=getattr(row, "folder_path", "默认目录") or "默认目录",
            lastRunStatus=getattr(row, "last_run_status", None),
            lastRunAt=getattr(row, "last_run_at", None),
            createdAt=row.created_at,
            updatedAt=row.updated_at,
            snapshots=[FlowVersionSnapshot.model_validate(s) for s in (getattr(row, "snapshots", None) or [])],
        )


def _ensure_flow_columns(connection) -> None:
    columns = {col["name"] for col in inspect(connection).get_columns(FlowRow.__tablename__)}
    if "input_variables" not in columns:
        connection.execute(text("ALTER TABLE rpa_flows ADD COLUMN input_variables TEXT NOT NULL DEFAULT '[]'"))
    if "folder_path" not in columns:
        connection.execute(text("ALTER TABLE rpa_flows ADD COLUMN folder_path VARCHAR(500) NOT NULL DEFAULT '默认目录'"))
    if "last_run_status" not in columns:
        connection.execute(text("ALTER TABLE rpa_flows ADD COLUMN last_run_status VARCHAR(24)"))
    if "last_run_at" not in columns:
        connection.execute(text("ALTER TABLE rpa_flows ADD COLUMN last_run_at TIMESTAMP WITH TIME ZONE"))
    if "snapshots" not in columns:
        connection.execute(text("ALTER TABLE rpa_flows ADD COLUMN snapshots TEXT NOT NULL DEFAULT '[]'"))
