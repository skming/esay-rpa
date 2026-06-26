from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.models.schemas import FlowCreateRequest, FlowSnapshot, FlowStatus, FlowUpdateRequest, FlowVersionSnapshot, TaskSnapshot
from app.services.flow_store import FlowStore, InMemoryFlowStore


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
            createdAt=now,
            updatedAt=now,
        )
        return await self._store.save(snapshot)

    async def list_flows(self) -> list[FlowSnapshot]:
        flows = await self._store.list()
        # Deduplicate: merge same-name flows into the newest one's snapshots
        await self._merge_duplicates(flows)
        return await self._store.list()

    async def _merge_duplicates(self, flows: list[FlowSnapshot]) -> None:
        """For each group of flows sharing the same name, keep only the newest.

        Older flows' definitions are prepended as FlowVersionSnapshots on the
        canonical (newest) flow, then the older flow records are deleted.
        Only runs when actual duplicates exist to avoid unnecessary writes.
        """
        from collections import defaultdict
        groups: dict[str, list[FlowSnapshot]] = defaultdict(list)
        for flow in flows:
            groups[flow.name].append(flow)

        for name, group in groups.items():
            if len(group) <= 1:
                continue
            # Sort newest first
            sorted_group = sorted(group, key=lambda f: f.updated_at, reverse=True)
            canonical = sorted_group[0]
            duplicates = sorted_group[1:]

            # Collect extra snapshots from duplicates (newest dup first, then its own snapshots)
            extra_snapshots: list[FlowVersionSnapshot] = []
            for dup in duplicates:
                # Add the duplicate's current definition as a snapshot
                extra_snapshots.append(FlowVersionSnapshot(
                    version=dup.version,
                    description=dup.description or f"历史版本（合并自重复流程）",
                    definition=dup.definition,
                    input_variables=dup.input_variables,
                    saved_at=dup.updated_at,
                ))
                # Also bring in its own historical snapshots
                extra_snapshots.extend(dup.snapshots)
                # Delete the duplicate record
                await self._store.delete(dup.flow_id)

            # Merge into canonical's snapshots (deduplicate by version+savedAt, keep at most 50)
            seen: set[str] = {f"{s.version}|{s.saved_at}" for s in canonical.snapshots}
            merged: list[FlowVersionSnapshot] = list(canonical.snapshots)
            for snap in extra_snapshots:
                key = f"{snap.version}|{snap.saved_at}"
                if key not in seen:
                    seen.add(key)
                    merged.append(snap)
            # Sort merged snapshots newest first, cap at 50
            merged.sort(key=lambda s: s.saved_at, reverse=True)
            merged = merged[:50]

            updated = canonical.model_copy(update={"snapshots": merged})
            await self._store.save(updated)

    async def get_flow(self, flow_id: str) -> FlowSnapshot | None:
        return await self._store.get(flow_id)

    async def update_flow(self, flow_id: str, request: FlowUpdateRequest) -> FlowSnapshot | None:
        current = await self._store.get(flow_id)
        if current is None:
            return None

        # When definition changes, push current version into snapshots before overwriting.
        definition_changed = request.definition is not None and request.definition != current.definition
        if definition_changed:
            entry = FlowVersionSnapshot(
                version=current.version,
                description=current.description,
                definition=current.definition,
                input_variables=current.input_variables,
                saved_at=current.updated_at,
            )
            updated_snapshots = [entry, *current.snapshots[:49]]  # keep at most 50
        else:
            updated_snapshots = current.snapshots

        payload = current.model_dump()
        for key, value in request.model_dump(by_alias=False).items():
            if value is not None:
                payload[key] = value
        payload["snapshots"] = updated_snapshots
        payload["updated_at"] = datetime.now(UTC)
        return await self._store.save(FlowSnapshot.model_validate(payload))

    async def duplicate_flow(self, flow_id: str) -> FlowSnapshot | None:
        """Create a copy of a flow with a new ID, a '副本' name suffix, and status reset to 'draft'."""
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
        """Return the percentage of successful tasks in the last 30 days, or None if there are no completed tasks."""
        from datetime import timedelta
        cutoff = datetime.now(UTC) - timedelta(days=30)
        recent = [t for t in tasks if t.updated_at >= cutoff and t.status in {"success", "error", "stopped"}]
        if not recent:
            return None
        successes = sum(1 for t in recent if t.status == "success")
        return round(successes / len(recent) * 100)
