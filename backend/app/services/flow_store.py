from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import ForeignKey, Index, Integer, String, Text, delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.models.schemas import FlowSnapshot, FlowVersionSnapshot
from app.services.schedule_store import Base, UTCDateTime, _json_type


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
    __table_args__ = (Index("ix_rpa_flows_updated_at", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1.0.0")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    input_variables: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)
    acceptance_contract: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    folder_path: Mapped[str] = mapped_column(String(500), nullable=False, default="默认目录")
    default_browser_executor: Mapped[str] = mapped_column(String(24), nullable=False, default="playwright")
    last_run_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class FlowVersionRow(Base):
    __tablename__ = "rpa_flow_versions"
    __table_args__ = (Index("ix_rpa_flow_versions_flow_saved", "flow_id", "saved_at"),)

    flow_id: Mapped[str] = mapped_column(ForeignKey("rpa_flows.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    input_variables: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)
    acceptance_contract: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    saved_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class SqlAlchemyFlowStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, tables=[FlowRow.__table__, FlowVersionRow.__table__])

    async def close(self) -> None:
        await self._engine.dispose()

    async def save(self, flow: FlowSnapshot) -> FlowSnapshot:
        async with self._session_factory() as session:
            row = await session.get(FlowRow, flow.flow_id)
            if row is None:
                row = FlowRow(id=flow.flow_id)
                session.add(row)
            self._apply_snapshot(row, flow)
            await session.execute(delete(FlowVersionRow).where(FlowVersionRow.flow_id == flow.flow_id))
            session.add_all([self._to_version_row(flow.flow_id, snapshot) for snapshot in flow.snapshots])
            await session.commit()
        return flow

    async def list(self) -> list[FlowSnapshot]:
        async with self._session_factory() as session:
            result = await session.scalars(select(FlowRow).order_by(FlowRow.updated_at.desc()))
            return await self._load_snapshots(session, list(result))

    async def get(self, flow_id: str) -> FlowSnapshot | None:
        async with self._session_factory() as session:
            row = await session.get(FlowRow, flow_id)
            if row is None:
                return None
            return (await self._load_snapshots(session, [row]))[0]

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
        row.acceptance_contract = flow.acceptance_contract.model_dump(mode="json", by_alias=True)
        row.revision = flow.revision
        row.status = flow.status
        row.folder_path = flow.folder_path
        row.default_browser_executor = flow.default_browser_executor
        row.last_run_status = flow.last_run_status
        row.last_run_at = flow.last_run_at
        row.created_at = flow.created_at
        row.updated_at = flow.updated_at

    @staticmethod
    def _to_snapshot(row: FlowRow, snapshots: list[FlowVersionSnapshot]) -> FlowSnapshot:
        return FlowSnapshot(
            flowId=row.id,
            name=row.name,
            version=row.version,
            description=row.description,
            definition=row.definition,
            inputVariables=row.input_variables,
            acceptanceContract=getattr(row, "acceptance_contract", None) or {},
            revision=getattr(row, "revision", 1) or 1,
            status=row.status,
            folderPath=getattr(row, "folder_path", "默认目录") or "默认目录",
            defaultBrowserExecutor=getattr(row, "default_browser_executor", "playwright") or "playwright",
            lastRunStatus=getattr(row, "last_run_status", None),
            lastRunAt=getattr(row, "last_run_at", None),
            createdAt=row.created_at,
            updatedAt=row.updated_at,
            snapshots=snapshots,
        )

    async def _load_snapshots(self, session, rows: list[FlowRow]) -> list[FlowSnapshot]:
        if not rows:
            return []
        flow_ids = [row.id for row in rows]
        version_rows = await session.scalars(
            select(FlowVersionRow)
            .where(FlowVersionRow.flow_id.in_(flow_ids))
            .order_by(FlowVersionRow.flow_id, FlowVersionRow.revision.desc())
        )
        versions: dict[str, list[FlowVersionSnapshot]] = {flow_id: [] for flow_id in flow_ids}
        for version in version_rows:
            versions[version.flow_id].append(self._from_version_row(version))
        return [self._to_snapshot(row, versions[row.id]) for row in rows]

    @staticmethod
    def _to_version_row(flow_id: str, snapshot: FlowVersionSnapshot) -> FlowVersionRow:
        return FlowVersionRow(
            flow_id=flow_id,
            revision=snapshot.revision,
            version=snapshot.version,
            description=snapshot.description,
            definition=snapshot.definition,
            input_variables=[
                variable.model_copy(update={"value": ""}).model_dump(mode="json", by_alias=True)
                if variable.sensitive or variable.category == "credential"
                else variable.model_dump(mode="json", by_alias=True)
                for variable in snapshot.input_variables
            ],
            acceptance_contract=snapshot.acceptance_contract.model_dump(mode="json", by_alias=True),
            saved_at=snapshot.saved_at,
        )

    @staticmethod
    def _from_version_row(row: FlowVersionRow) -> FlowVersionSnapshot:
        return FlowVersionSnapshot(
            version=row.version,
            description=row.description,
            definition=row.definition,
            inputVariables=row.input_variables,
            acceptanceContract=row.acceptance_contract,
            revision=row.revision,
            savedAt=row.saved_at,
        )
