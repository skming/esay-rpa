from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from app.models.schemas import FlowCreateRequest, FlowSnapshot, FlowStatus, FlowUpdateRequest, FlowVersionSnapshot, TaskSnapshot
from app.services.flow_store import FlowStore, InMemoryFlowStore

_TRAILING_NUMBER = re.compile(r"(\d+)$")


def _bump_patch_version(version: str) -> str:
    """递增版本号末尾数字（"v1.0.0" -> "v1.0.1"）；无末尾数字时追加 ".1"。"""
    match = _TRAILING_NUMBER.search(version)
    if match is None:
        return f"{version}.1"
    number = int(match.group(1)) + 1
    return version[: match.start()] + str(number)


class FlowService:
    """CRUD facade for flow snapshots; delegates persistence to a `FlowStore` implementation."""

    def __init__(self, store: FlowStore | None = None) -> None:
        self._store = store or InMemoryFlowStore()

    async def create_flow(self, request: FlowCreateRequest) -> FlowSnapshot:
        now = datetime.now(UTC)
        snapshot = FlowSnapshot(
            flowId=str(uuid4()),
            name=request.name,
            version=request.version,
            description=request.description,
            definition=request.definition,
            inputVariables=request.input_variables,
            status=request.status,
            folderPath=request.folder_path,
            defaultBrowserExecutor=request.default_browser_executor,
            createdAt=now,
            updatedAt=now,
        )
        return await self._store.save(snapshot)

    async def list_flows(self) -> list[FlowSnapshot]:
        return await self._store.list()

    async def get_flow(self, flow_id: str) -> FlowSnapshot | None:
        return await self._store.get(flow_id)

    async def update_flow(self, flow_id: str, request: FlowUpdateRequest) -> FlowSnapshot | None:
        current = await self._store.get(flow_id)
        if current is None:
            return None

        definition_changed = request.definition is not None and request.definition != current.definition
        if definition_changed:
            entry = FlowVersionSnapshot(
                version=current.version,
                description=current.description,
                definition=current.definition,
                input_variables=current.input_variables,
                saved_at=current.updated_at,
            )
            updated_snapshots = [entry, *current.snapshots[:49]]  # 版本历史最多保留 50 条（含本条），避免无限增长
        else:
            updated_snapshots = current.snapshots

        payload = current.model_dump()
        for key, value in request.model_dump(by_alias=False).items():
            if value is not None:
                payload[key] = value
        # 调用方（尤其 AI 工具）几乎不显式传 version，不自动递增会导致版本历史挤在同一版本号上。
        if definition_changed and request.version is None:
            payload["version"] = _bump_patch_version(current.version)
        payload["snapshots"] = updated_snapshots
        payload["updated_at"] = datetime.now(UTC)
        return await self._store.save(FlowSnapshot.model_validate(payload))

    async def duplicate_flow(self, flow_id: str) -> FlowSnapshot | None:
        source = await self._store.get(flow_id)
        if source is None:
            return None
        now = datetime.now(UTC)
        copy = FlowSnapshot(
            flowId=str(uuid4()),
            name=f"{source.name} 副本",
            version="v1.0.0",
            description=source.description,
            definition=source.definition,
            inputVariables=source.input_variables,
            status="draft",
            folderPath=source.folder_path,
            defaultBrowserExecutor=source.default_browser_executor,
            createdAt=now,
            updatedAt=now,
        )
        return await self._store.save(copy)

    async def move_flow(self, flow_id: str, folder_path: str) -> FlowSnapshot | None:
        current = await self._store.get(flow_id)
        if current is None:
            return None
        return await self._store.save(
            current.model_copy(update={"folder_path": folder_path, "updated_at": datetime.now(UTC)})
        )

    async def set_flow_status(self, flow_id: str, status: FlowStatus) -> FlowSnapshot | None:
        current = await self._store.get(flow_id)
        if current is None:
            return None
        return await self._store.save(
            current.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})
        )

    async def archive_flow(self, flow_id: str) -> FlowSnapshot | None:
        return await self.set_flow_status(flow_id, "archived")

    async def delete_flow(self, flow_id: str) -> bool:
        return await self._store.delete(flow_id)

    async def update_last_run(self, flow_id: str, task_status: str, run_at: datetime) -> None:
        current = await self._store.get(flow_id)
        if current is None:
            return
        await self._store.save(
            current.model_copy(update={"last_run_status": task_status, "last_run_at": run_at, "updated_at": run_at})
        )

    @staticmethod
    def compute_success_rate_30d(tasks: list[TaskSnapshot]) -> int | None:
        from datetime import timedelta
        cutoff = datetime.now(UTC) - timedelta(days=30)

        def _as_utc(value: datetime) -> datetime:
            # 历史持久化数据可能读回 naive 时间戳（SQLite 不存时区），按 UTC 补齐再比较。
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

        recent = [t for t in tasks if _as_utc(t.updated_at) >= cutoff and t.status in {"success", "error", "stopped"}]
        if not recent:
            return None
        successes = sum(1 for t in recent if t.status == "success")
        return round(successes / len(recent) * 100)
