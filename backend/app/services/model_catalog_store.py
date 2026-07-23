from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import Integer, String, delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.services.schedule_store import Base, _json_type


class ModelCatalogStore(Protocol):
    async def create_schema(self) -> None: ...

    async def is_empty(self) -> bool: ...

    async def list(self) -> list[dict[str, Any]]: ...

    async def replace_all(self, catalog: list[dict[str, Any]]) -> None: ...

    async def close(self) -> None: ...


class ModelCatalogRow(Base):
    __tablename__ = "rpa_model_catalog"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data: Mapped[dict] = mapped_column(_json_type(), nullable=False)


class SqlAlchemyModelCatalogStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, tables=[ModelCatalogRow.__table__])

    async def close(self) -> None:
        await self._engine.dispose()

    async def is_empty(self) -> bool:
        async with self._session_factory() as session:
            result = await session.scalars(select(ModelCatalogRow.id).limit(1))
            return result.first() is None

    async def list(self) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            result = await session.scalars(select(ModelCatalogRow).order_by(ModelCatalogRow.sort_order))
            return [dict(row.data) for row in result]

    async def replace_all(self, catalog: list[dict[str, Any]]) -> None:
        # 全量覆盖而非增量更新：先清空整表再按传入顺序重建 sort_order，调用方需传完整目录。
        async with self._session_factory() as session:
            await session.execute(delete(ModelCatalogRow))
            for index, item in enumerate(catalog):
                session.add(ModelCatalogRow(id=item["id"], sort_order=index, data=item))
            await session.commit()
