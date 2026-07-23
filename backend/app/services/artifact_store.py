"""Artifact persistence backends: local filesystem and MinIO S3-compatible object storage.

File layout (local backend):
  workspace/runs/{flowKey}/{taskId}/artifacts/{filename}
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4

from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel

from app.core import storage
from app.models.schemas import ArtifactContent, ArtifactSnapshot, ArtifactType

ArtifactMetadata = dict[str, str | int | float | bool | None]
JsonPayload = BaseModel | dict[str, object]

def _safe_id(value: str) -> str:
    return (re.sub(r"[^\w\-]", "_", value) or "default")[:80]


def _safe_filename(filename: str) -> str:
    normalized = filename.strip().replace("/", "_").replace("\\", "_")
    return normalized or "artifact.bin"


class ArtifactStore(Protocol):
    async def save_json(
        self,
        *,
        task_id: str,
        artifact_type: ArtifactType,
        filename: str,
        payload: JsonPayload,
        metadata: ArtifactMetadata | None = None,
        flow_id: str | None = None,
    ) -> ArtifactSnapshot: ...

    async def save_bytes(
        self,
        *,
        task_id: str,
        artifact_type: ArtifactType,
        filename: str,
        content: bytes,
        content_type: str,
        metadata: ArtifactMetadata | None = None,
        flow_id: str | None = None,
    ) -> ArtifactSnapshot: ...

    def list_task_artifacts(self, task_id: str) -> list[ArtifactSnapshot] | None: ...

    def read_artifact_content(self, task_id: str, artifact_id: str) -> ArtifactContent | None: ...


class MinioClientProtocol(Protocol):
    def bucket_exists(self, bucket_name: str) -> bool: ...
    def make_bucket(self, bucket_name: str) -> None: ...
    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BinaryIO,
        length: int,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> object: ...
    def get_object(self, bucket_name: str, object_name: str) -> BinaryIO: ...


class LocalArtifactStore:
    """Writes artifacts as files under a local directory tree; metadata kept in memory."""

    def __init__(
        self,
        artifact_root: Path | None = None,
    ) -> None:
        self._artifact_root = (artifact_root or _resolve_env_path("RPA_ARTIFACT_ROOT", storage.resolve_run_root())).resolve()
        self._artifacts: dict[str, list[ArtifactSnapshot]] = {}
        self._artifact_paths: dict[str, Path] = {}

    def _task_dir(self, task_id: str, flow_id: str | None) -> Path:
        flow_key = _safe_id(flow_id or "standalone")
        return self._artifact_root / flow_key / _safe_id(task_id) / "artifacts"

    async def save_json(
        self,
        *,
        task_id: str,
        artifact_type: ArtifactType,
        filename: str,
        payload: JsonPayload,
        metadata: ArtifactMetadata | None = None,
        flow_id: str | None = None,
    ) -> ArtifactSnapshot:
        task_dir = self._task_dir(task_id, flow_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        safe = _safe_filename(filename)
        file_path = task_dir / safe
        content = _encode_json(payload)
        file_path.write_bytes(content)

        artifact = ArtifactSnapshot(
            artifact_id=str(uuid4()),
            task_id=task_id,
            artifact_type=artifact_type,
            filename=safe,
            storage_url=file_path.as_uri(),
            content_type="application/json",
            size_bytes=len(content),
            created_at=datetime.now(UTC),
            metadata=metadata or {},
        )
        self._artifacts.setdefault(task_id, []).append(artifact)
        self._artifact_paths[artifact.artifact_id] = file_path
        return artifact

    async def save_bytes(
        self,
        *,
        task_id: str,
        artifact_type: ArtifactType,
        filename: str,
        content: bytes,
        content_type: str,
        metadata: ArtifactMetadata | None = None,
        flow_id: str | None = None,
    ) -> ArtifactSnapshot:
        task_dir = self._task_dir(task_id, flow_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        safe = _safe_filename(filename)
        file_path = task_dir / safe
        file_path.write_bytes(content)

        artifact = ArtifactSnapshot(
            artifact_id=str(uuid4()),
            task_id=task_id,
            artifact_type=artifact_type,
            filename=safe,
            storage_url=file_path.as_uri(),
            content_type=content_type,
            size_bytes=len(content),
            created_at=datetime.now(UTC),
            metadata=metadata or {},
        )
        self._artifacts.setdefault(task_id, []).append(artifact)
        self._artifact_paths[artifact.artifact_id] = file_path
        return artifact

    def list_task_artifacts(self, task_id: str) -> list[ArtifactSnapshot] | None:
        return list(self._artifacts.get(task_id, []))

    def read_artifact_content(self, task_id: str, artifact_id: str) -> ArtifactContent | None:
        artifact = next(
            (a for a in self._artifacts.get(task_id, []) if a.artifact_id == artifact_id),
            None,
        )
        if artifact is None:
            return None

        file_path = self._artifact_paths.get(artifact_id)
        if file_path is None:
            return None

        resolved = file_path.resolve()
        # 防路径穿越：即便内部生成的路径理论上不会越界，仍在读取前显式校验，
        # 避免未来传入畸形 filename/flow_id 时读到 artifact_root 外的文件。
        if not resolved.is_relative_to(self._artifact_root):
            raise ValueError(f"artifact path escapes storage root: {resolved}")

        if artifact.content_type.startswith("text/") or artifact.content_type.endswith("/json"):
            return ArtifactContent(artifact=artifact, content=resolved.read_text("utf-8"))
        encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
        return ArtifactContent(artifact=artifact, content=f"data:{artifact.content_type};base64,{encoded}")


class MinioArtifactStore:
    """Stores artifacts as MinIO objects; metadata kept in memory (bucket is lazily created)."""

    def __init__(self, client: MinioClientProtocol, *, bucket: str) -> None:
        if not bucket.strip():
            raise ValueError("bucket name must not be empty")
        self._client = client
        self._bucket = bucket
        self._artifacts: dict[str, list[ArtifactSnapshot]] = {}
        self._artifact_objects: dict[str, str] = {}
        self._bucket_ready = False

    def _object_name(self, task_id: str, filename: str, flow_id: str | None) -> str:
        safe = _safe_filename(filename)
        uid = uuid4()
        flow_key = _safe_id(flow_id or "standalone")
        return f"runs/{flow_key}/{_safe_id(task_id)}/artifacts/{uid}-{safe}"

    async def save_json(
        self,
        *,
        task_id: str,
        artifact_type: ArtifactType,
        filename: str,
        payload: JsonPayload,
        metadata: ArtifactMetadata | None = None,
        flow_id: str | None = None,
    ) -> ArtifactSnapshot:
        await self._ensure_bucket()
        obj = self._object_name(task_id, filename, flow_id)
        content = _encode_json(payload)
        artifact = ArtifactSnapshot(
            artifact_id=str(uuid4()),
            task_id=task_id,
            artifact_type=artifact_type,
            filename=_safe_filename(filename),
            storage_url=f"s3://{self._bucket}/{obj}",
            content_type="application/json",
            size_bytes=len(content),
            created_at=datetime.now(UTC),
            metadata=metadata or {},
        )
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket, obj, BytesIO(content), len(content),
            "application/json", _stringify_metadata(artifact.metadata),
        )
        self._artifacts.setdefault(task_id, []).append(artifact)
        self._artifact_objects[artifact.artifact_id] = obj
        return artifact

    async def save_bytes(
        self,
        *,
        task_id: str,
        artifact_type: ArtifactType,
        filename: str,
        content: bytes,
        content_type: str,
        metadata: ArtifactMetadata | None = None,
        flow_id: str | None = None,
    ) -> ArtifactSnapshot:
        await self._ensure_bucket()
        obj = self._object_name(task_id, filename, flow_id)
        artifact = ArtifactSnapshot(
            artifact_id=str(uuid4()),
            task_id=task_id,
            artifact_type=artifact_type,
            filename=_safe_filename(filename),
            storage_url=f"s3://{self._bucket}/{obj}",
            content_type=content_type,
            size_bytes=len(content),
            created_at=datetime.now(UTC),
            metadata=metadata or {},
        )
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket, obj, BytesIO(content), len(content),
            content_type, _stringify_metadata(artifact.metadata),
        )
        self._artifacts.setdefault(task_id, []).append(artifact)
        self._artifact_objects[artifact.artifact_id] = obj
        return artifact

    def list_task_artifacts(self, task_id: str) -> list[ArtifactSnapshot] | None:
        return list(self._artifacts.get(task_id, []))

    def read_artifact_content(self, task_id: str, artifact_id: str) -> ArtifactContent | None:
        artifact = next(
            (a for a in self._artifacts.get(task_id, []) if a.artifact_id == artifact_id),
            None,
        )
        if artifact is None:
            return None
        obj = self._artifact_objects.get(artifact_id)
        if obj is None:
            return None
        response = self._client.get_object(self._bucket, obj)
        try:
            data = response.read()
            if artifact.content_type.startswith("text/") or artifact.content_type.endswith("/json"):
                return ArtifactContent(artifact=artifact, content=data.decode("utf-8"))
            encoded = base64.b64encode(data).decode("ascii")
            return ArtifactContent(artifact=artifact, content=f"data:{artifact.content_type};base64,{encoded}")
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    async def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        try:
            exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
            if not exists:
                await asyncio.to_thread(self._client.make_bucket, self._bucket)
        except S3Error as exc:
            raise RuntimeError(f"MinIO bucket initialisation failed: {self._bucket}") from exc
        self._bucket_ready = True


def create_minio_client(*, endpoint: str, access_key: str, secret_key: str, secure: bool) -> Minio:
    return Minio(endpoint=endpoint, access_key=access_key, secret_key=secret_key, secure=secure)


def _encode_json(payload: JsonPayload) -> bytes:
    raw = payload.model_dump(mode="json", by_alias=True) if isinstance(payload, BaseModel) else payload
    return json.dumps(raw, ensure_ascii=False, indent=2).encode("utf-8")


def _stringify_metadata(metadata: ArtifactMetadata) -> dict[str, str]:
    return {k: str(v) for k, v in metadata.items() if v is not None}


def _resolve_env_path(env_name: str, default: Path) -> Path:
    raw = os.getenv(env_name)
    return Path(raw).expanduser() if raw and raw.strip() else default
