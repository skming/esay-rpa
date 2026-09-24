from __future__ import annotations

from io import BytesIO

from app.models.schemas import ScrapeResult
from app.services.artifact_store import LocalArtifactStore, MinioArtifactStore


class FakeMinioClient:
    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], bytes] = {}
        self.metadata: dict[tuple[str, str], dict[str, str]] = {}

    def bucket_exists(self, bucket_name: str) -> bool:
        return bucket_name in self.buckets

    def make_bucket(self, bucket_name: str) -> None:
        self.buckets.add(bucket_name)

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data,
        length: int,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> object:
        content = data.read(length)
        self.objects[(bucket_name, object_name)] = content
        self.metadata[(bucket_name, object_name)] = metadata or {}
        return object()

    def get_object(self, bucket_name: str, object_name: str) -> BytesIO:
        return BytesIO(self.objects[(bucket_name, object_name)])


async def test_minio_artifact_store_saves_and_reads_json() -> None:
    client = FakeMinioClient()
    store = MinioArtifactStore(client=client, bucket="rpa-artifacts")

    artifact = await store.save_json(
        task_id="task-1",
        artifact_type="dataset",
        filename="../result.json",
        payload=ScrapeResult(url="https://example.com/", selector="h1::text", count=1, values=["hello"]),
        metadata={"count": 1, "secret": None},
    )

    assert artifact.filename == ".._result.json"
    assert artifact.storage_url.startswith("s3://rpa-artifacts/runs/standalone/task-1/artifacts/")
    assert artifact.size_bytes > 0
    assert client.buckets == {"rpa-artifacts"}

    listed = store.list_task_artifacts("task-1")
    assert listed == [artifact]
    content = store.read_artifact_content("task-1", artifact.artifact_id)
    assert content is not None
    assert '"values"' in content.content
    assert '"hello"' in content.content

    object_name = artifact.storage_url.removeprefix("s3://rpa-artifacts/")
    assert client.metadata[("rpa-artifacts", object_name)] == {"count": "1"}


async def test_artifact_store_reads_binary_artifact_as_data_url(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    artifact = await store.save_bytes(
        task_id="task-1",
        artifact_type="screenshot",
        filename="node.png",
        content=b"\x89PNG\r\n\x1a\nfake",
        content_type="image/png",
        metadata={"node_id": "n1"},
    )

    content = store.read_artifact_content("task-1", artifact.artifact_id)

    assert artifact.content_type == "image/png"
    assert content is not None
    assert content.content.startswith("data:image/png;base64,")


async def test_local_store_reads_content_from_persisted_snapshot(tmp_path) -> None:
    # 模拟进程重启：产物已落盘，新建 store 的内存路径映射为空，只能按持久化 storage_url 回读。
    store = LocalArtifactStore(tmp_path)
    artifact = await store.save_bytes(
        task_id="task-1",
        artifact_type="screenshot",
        filename="node.png",
        content=b"\x89PNG\r\n\x1a\nfake",
        content_type="image/png",
    )

    reopened = LocalArtifactStore(tmp_path)
    assert reopened.read_artifact_content("task-1", artifact.artifact_id) is None
    content = reopened.read_snapshot_content(artifact)
    assert content is not None
    assert content.content.startswith("data:image/png;base64,")


async def test_minio_store_reads_snapshot_when_filename_has_url_special_chars() -> None:
    # 文件名里的 # ? 会被 urlparse 当作 fragment/query 截断，重启后按 bucket 前缀切出完整对象名才能回读。
    client = FakeMinioClient()
    store = MinioArtifactStore(client=client, bucket="rpa-artifacts")

    for filename in ("summary#2.txt", "report?v=1.txt"):
        artifact = await store.save_bytes(
            task_id="task-1",
            artifact_type="dataset",
            filename=filename,
            content=b"payload-body",
            content_type="text/plain",
        )
        assert filename in artifact.storage_url

        reopened = MinioArtifactStore(client=client, bucket="rpa-artifacts")
        content = reopened.read_snapshot_content(artifact)
        assert content is not None
        assert content.content == "payload-body"


async def test_minio_store_reads_content_from_persisted_snapshot() -> None:
    client = FakeMinioClient()
    store = MinioArtifactStore(client=client, bucket="rpa-artifacts")
    artifact = await store.save_json(
        task_id="task-1",
        artifact_type="dataset",
        filename="result.json",
        payload=ScrapeResult(url="https://example.com/", selector="h1::text", count=1, values=["hello"]),
    )

    reopened = MinioArtifactStore(client=client, bucket="rpa-artifacts")
    assert reopened.read_artifact_content("task-1", artifact.artifact_id) is None
    content = reopened.read_snapshot_content(artifact)
    assert content is not None
    assert '"hello"' in content.content
