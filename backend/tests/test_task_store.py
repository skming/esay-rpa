from __future__ import annotations

from datetime import UTC, datetime

from app.models.schemas import ArtifactSnapshot, NodeExecutionEvidence, RunTaskRequest, RuntimeProgress, RuntimeVariableSnapshot, ScrapeResult, TaskLogEntry, TaskSnapshot
from app.services.schedule_store import create_schedule_engine
from app.services.task_store import SqlAlchemyTaskStore


def build_task_request() -> RunTaskRequest:
    return RunTaskRequest(
        flowName="任务持久化流程",
        flowId="00000000-0000-0000-0000-000000000101",
        flowRevision=7,
        definitionDigest="a" * 64,
        acceptanceContract={
            "deliverables": [{"id": "result", "variable": "result_count", "kind": "scalar"}],
        },
        targetUrl="https://quotes.toscrape.com/",
        selector=".quote .text::text",
        scope="from-selection",
        startNodeId="n3",
        failureStrategy="continue",
        screenshot=False,
        concurrency=3,
        timeoutMs=1000,
    )


def build_task_snapshot(task_id: str = "task-1") -> TaskSnapshot:
    now = datetime.now(UTC)
    return TaskSnapshot(
        taskId=task_id,
        flowId="00000000-0000-0000-0000-000000000101",
        flowName="任务持久化流程",
        status="queued",
        mode="run",
        progress=RuntimeProgress(currentStep=0, totalSteps=3, percent=0, elapsedMs=0),
        runConfig={
            "scope": "from-selection",
            "startNodeId": "n3",
            "failureStrategy": "continue",
            "screenshot": False,
            "concurrency": 3,
        },
        createdAt=now,
        updatedAt=now,
    )


async def test_sqlalchemy_task_store_persists_task_logs_and_result(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}"
    engine = create_schedule_engine(database_url)
    store = SqlAlchemyTaskStore(engine)
    await store.create_schema()

    request = build_task_request()
    queued = await store.save_task(build_task_snapshot(), request)
    assert queued.task_id == "task-1"

    first_log = TaskLogEntry(taskId=queued.task_id, level="info", message="任务已入队", nodeId="start")
    second_log = TaskLogEntry(taskId=queued.task_id, level="success", message="任务完成", detail="命中 1 条", nodeId="end")
    await store.append_log(first_log)
    await store.append_log(second_log)

    artifact = ArtifactSnapshot(
        artifactId="artifact-1",
        taskId=queued.task_id,
        artifactType="dataset",
        filename="scrape-result.json",
        storageUrl="file:///tmp/scrape-result.json",
        contentType="application/json",
        sizeBytes=42,
        createdAt=datetime.now(UTC),
        metadata={"count": 1, "flow_name": "任务持久化流程"},
    )
    success = queued.model_copy(
        update={
            "status": "success",
            "progress": RuntimeProgress(currentStep=3, totalSteps=3, percent=100, elapsedMs=25),
            "result": ScrapeResult(url=str(request.target_url), selector=request.selector, count=1, values=["hello"]),
            "artifacts": [artifact],
            "variables": [RuntimeVariableSnapshot(name="result_count", type="Integer", value="1", scope="局部")],
            "execution_evidence": [NodeExecutionEvidence(
                nodeId="count",
                nodeType="script.python",
                unchangedPairs=[],
            )],
            "updated_at": datetime.now(UTC),
        }
    )
    await store.save_task(success, request)

    restored = await store.get_task(queued.task_id)
    assert restored is not None
    assert restored.flow_id == "00000000-0000-0000-0000-000000000101"
    assert restored.status == "success"
    assert restored.run_config.scope == "from-selection"
    assert restored.run_config.start_node_id == "n3"
    assert restored.run_config.failure_strategy == "continue"
    assert restored.run_config.screenshot is False
    assert restored.run_config.concurrency == 3
    assert restored.progress.percent == 100
    assert restored.result is not None
    assert restored.result.values == ["hello"]
    assert restored.variables[0].name == "result_count"
    assert restored.variables[0].value == "1"
    assert restored.flow_revision == 7
    assert restored.definition_digest == "a" * 64
    assert restored.acceptance_contract.deliverables[0].variable == "result_count"
    assert restored.execution_evidence[0].node_id == "count"
    assert [item.artifact_id for item in restored.artifacts] == ["artifact-1"]
    assert restored.artifacts[0].metadata["count"] == 1

    logs = await store.list_logs(queued.task_id)
    assert logs is not None
    assert [log.message for log in logs] == ["任务已入队", "任务完成"]
    assert [log.node_id for log in logs] == ["start", "end"]

    variables = await store.list_variables(queued.task_id)
    assert variables is not None
    assert [(variable.name, variable.type, variable.value) for variable in variables] == [("result_count", "Integer", "1")]

    updated = success.model_copy(
        update={
            "variables": [
                RuntimeVariableSnapshot(name="all_order_details", type="List", value='[{"order_id":"A001"}]', scope="局部"),
                RuntimeVariableSnapshot(name="result_count", type="Integer", value="2", scope="局部"),
            ],
            "updated_at": datetime.now(UTC),
        }
    )
    await store.save_task(updated, request)
    updated_variables = await store.list_variables(queued.task_id)
    assert updated_variables is not None
    assert [(variable.name, variable.value) for variable in updated_variables] == [
        ("all_order_details", '[{"order_id":"A001"}]'),
        ("result_count", "2"),
    ]

    second_request = build_task_request().model_copy(update={"flow_id": "00000000-0000-0000-0000-000000000202", "flow_name": "其他流程"})
    second = await store.save_task(build_task_snapshot("task-2").model_copy(update={"flow_id": second_request.flow_id, "flow_name": second_request.flow_name}), second_request)
    all_tasks = await store.list_tasks(limit=10)
    assert [task.task_id for task in all_tasks] == [second.task_id, queued.task_id]
    flow_tasks = await store.list_tasks(flow_id="00000000-0000-0000-0000-000000000101", limit=10)
    assert [task.task_id for task in flow_tasks] == [queued.task_id]

    assert await store.list_logs("missing-task") is None
    assert await store.list_variables("missing-task") is None

    assert await store.delete_task(queued.task_id) is True
    assert await store.get_task(queued.task_id) is None
    assert await store.list_variables(queued.task_id) is None

    await store.close()


async def test_sqlite_roundtrip_returns_timezone_aware_timestamps(tmp_path) -> None:
    """SQLite 不保存时区；读回的时间戳必须补齐 UTC，否则下游与 aware cutoff
    比较（如 30 天成功率统计）会抛 TypeError。"""
    engine = create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}")
    store = SqlAlchemyTaskStore(engine)
    await store.create_schema()
    try:
        await store.save_task(build_task_snapshot("task-tz"), build_task_request())
        loaded = await store.get_task("task-tz")
        assert loaded is not None
        assert loaded.created_at.tzinfo is not None
        assert loaded.updated_at.tzinfo is not None
        listed = await store.list_tasks(flow_id="00000000-0000-0000-0000-000000000101")
        assert all(t.updated_at.tzinfo is not None for t in listed)
    finally:
        await store.close()


def test_compute_success_rate_30d_tolerates_naive_timestamps() -> None:
    from app.services.flow_service import FlowService

    def snap(task_id: str, status: str, updated: datetime) -> TaskSnapshot:
        return TaskSnapshot(
            taskId=task_id,
            flowId="00000000-0000-0000-0000-000000000101",
            flowName="任务持久化流程",
            status=status,
            mode="run",
            progress=RuntimeProgress(currentStep=1, totalSteps=1, percent=100, elapsedMs=1),
            createdAt=updated,
            updatedAt=updated,
        )

    naive_recent = datetime.now(UTC).replace(tzinfo=None)
    tasks = [
        snap("t1", "success", naive_recent),          # naive（历史 SQLite 读回）
        snap("t2", "error", datetime.now(UTC)),        # aware（内存中）
        snap("t3", "success", datetime.now(UTC)),
        snap("t4", "running", datetime.now(UTC)),      # 未完成，不计入
    ]
    assert FlowService.compute_success_rate_30d(tasks) == 67
    assert FlowService.compute_success_rate_30d([]) is None
