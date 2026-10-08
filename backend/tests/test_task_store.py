from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.models.schemas import ArtifactSnapshot, NodeExecutionEvidence, RunTaskRequest, RuntimeProgress, RuntimeVariableSnapshot, ScrapeResult, TaskLogEntry, TaskSnapshot
from app.services.schedule_store import create_schedule_engine
from app.services.log_broker import LogBroker
from app.services.task_manager import TaskManager, TaskRecord
from app.services.task_store import InMemoryTaskStore, SqlAlchemyTaskStore
from app.services.runtime_variables import RuntimeVariableStore
from tests.test_task_manager import FakeRunner


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


async def test_progress_updates_do_not_rewrite_variable_or_artifact_rows(tmp_path) -> None:
    engine = create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'partial.db'}")
    store = SqlAlchemyTaskStore(engine)
    await store.create_schema()
    request = build_task_request()
    artifact = ArtifactSnapshot(
        artifactId="artifact-1", taskId="task-1", artifactType="dataset", filename="result.json",
        storageUrl="file:///tmp/result.json", contentType="application/json", sizeBytes=2, createdAt=datetime.now(UTC),
    )
    snapshot = build_task_snapshot().model_copy(update={
        "variables": [RuntimeVariableSnapshot(name="count", type="Integer", value="1", scope="全局")], "artifacts": [artifact],
    })
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
    try:
        await store.save_task(snapshot, request, replace_variables=False, replace_artifacts=False)
        record = TaskRecord(request=request, snapshot=snapshot, persisted_snapshot=snapshot, variables=RuntimeVariableStore.from_initial({"count": 1}))
        manager = TaskManager(FakeRunner(), LogBroker(), task_store=store)
        event.listen(engine.sync_engine, "before_cursor_execute", capture)
        await manager._update_snapshot(record, progress=RuntimeProgress(currentStep=1, totalSteps=3, percent=10, elapsedMs=1))
        assert not any("rpa_task_variables" in sql or "rpa_artifacts" in sql for sql in statements)
        statements.clear()
        record.variables.set("count", 2, scope="全局")
        await manager._update_snapshot(record)
        assert any(sql.startswith("delete from rpa_task_variables") for sql in statements)
        assert not any("rpa_artifacts" in sql for sql in statements)
        statements.clear()
        await manager._update_snapshot(record, artifacts=[])
        assert any(sql.startswith("delete from rpa_artifacts") for sql in statements)
        assert not any("rpa_task_variables" in sql for sql in statements)
        restored = await store.get_task(snapshot.task_id)
        assert restored is not None
        assert restored.variables[0].value == "2"
        assert restored.artifacts == []
        assert restored.progress.percent == 10
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        await engine.dispose()


async def test_memory_history_is_bounded_without_dropping_active_tasks_or_success_rates() -> None:
    store = InMemoryTaskStore(history_limit=2)
    request = build_task_request()
    await store.save_task(build_task_snapshot("active"), request)
    updated = datetime.now(UTC)
    for index in range(4):
        await store.save_task(build_task_snapshot(str(index)).model_copy(update={"status": "success" if index == 0 else "error", "updated_at": updated}), request)
    assert await store.get_task("active") is not None
    assert await store.get_task("0") is None
    assert await store.get_task("1") is None
    assert await store.get_task("2") is not None
    assert await store.get_task("3") is not None
    assert len(await store.list_tasks()) == 3
    from datetime import timedelta
    assert await store.success_rates_since(datetime.now(UTC) - timedelta(days=30)) == {request.flow_id: 25}
    with pytest.raises(ValueError):
        InMemoryTaskStore(history_limit=0)


async def test_memory_history_eviction_preserves_complete_schedule_batches() -> None:
    store = InMemoryTaskStore(history_limit=1)
    request = build_task_request().model_copy(update={"schedule_id": "schedule-1"})
    since = datetime.now(UTC)
    for index in range(3):
        task = build_task_snapshot(f"scheduled-{index}").model_copy(update={
            "schedule_id": request.schedule_id, "status": "error" if index == 0 else "success",
        })
        await store.save_task(task, request)
    await store.save_task(build_task_snapshot("manual").model_copy(update={"status": "success"}), build_task_request())
    batch = await store.list_schedule_batch_tasks(request.schedule_id, since)
    assert len(batch) == 3
    assert sum(task.status == "error" for task in batch) == 1


async def test_snapshot_tracking_keeps_changes_made_during_persistence(tmp_path, monkeypatch) -> None:
    engine = create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'interleaved.db'}")
    store = SqlAlchemyTaskStore(engine)
    await store.create_schema()
    request = build_task_request()
    variables = RuntimeVariableStore.from_initial({"count": 1})
    snapshot = build_task_snapshot().model_copy(update={"variables": variables.snapshots()})
    record = TaskRecord(request=request, snapshot=snapshot, persisted_snapshot=snapshot, variables=variables)
    manager = TaskManager(FakeRunner(), LogBroker(), task_store=store)
    save = store.save_task
    async def interleaved_save(task, request, **options):
        record.variables.set("count", 2)
        record.snapshot = record.snapshot.model_copy(update={"variables": record.variables.snapshots()})
        return await save(task, request, **options)
    try:
        await save(snapshot, request)
        monkeypatch.setattr(store, "save_task", interleaved_save)
        await manager._update_snapshot(record)
        assert record.persisted_snapshot.variables[0].value == "1"
        monkeypatch.setattr(store, "save_task", save)
        await manager._update_snapshot(record)
        restored = await store.get_task(snapshot.task_id)
        assert restored.variables[0].value == "2"
    finally:
        await engine.dispose()


async def test_schedule_start_failure_survives_task_manager_restart(tmp_path) -> None:
    engine = create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'failed-start.db'}")
    store = SqlAlchemyTaskStore(engine)
    await store.create_schema()
    try:
        manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), task_store=store)
        request = build_task_request().model_copy(update={"schedule_id": "schedule-1"})
        failure = await manager.record_schedule_start_failure(request, "验收契约缺少 deliverable")

        reloaded = TaskManager(runner=FakeRunner(), broker=LogBroker(), task_store=store)
        batch = await reloaded.list_schedule_batch_tasks("schedule-1", failure.created_at)
        assert len(batch) == 1
        assert batch[0].task_id == failure.task_id
        assert batch[0].status == "error"
        assert batch[0].progress.current_step == 0
        assert "验收契约缺少 deliverable" in (batch[0].error or "")
    finally:
        await engine.dispose()


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

    protected = updated.model_copy(update={
        "variables": [
            RuntimeVariableSnapshot(
                name="api_token", category="credential", sensitive=True,
                type="String", value="must-not-persist", scope="全局",
            ),
        ],
        "updated_at": datetime.now(UTC),
    })
    await store.save_task(protected, request)
    protected_variables = await store.list_variables(queued.task_id)
    assert protected_variables is not None
    assert protected_variables[0].name == "api_token"
    assert protected_variables[0].sensitive is True
    assert protected_variables[0].category == "credential"
    assert protected_variables[0].value == ""

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


async def test_task_schema_has_single_sources_and_cascades_children(tmp_path) -> None:
    engine = create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}")
    store = SqlAlchemyTaskStore(engine)
    await store.create_schema()
    try:
        task = build_task_snapshot().model_copy(update={
            "variables": [RuntimeVariableSnapshot(name="count", type="Integer", value="1")],
        })
        await store.save_task(task, build_task_request())
        async with engine.begin() as connection:
            column_result = await connection.exec_driver_sql("PRAGMA table_info(rpa_tasks)")
            columns = {row[1] for row in column_result.all()}
            assert "request_payload" not in columns
            assert "variables_payload" not in columns
            assert "artifacts_payload" not in columns
            await connection.exec_driver_sql("DELETE FROM rpa_tasks WHERE id = 'task-1'")
            remaining = (await connection.exec_driver_sql(
                "SELECT count(*) FROM rpa_task_variables WHERE task_id = 'task-1'"
            )).scalar_one()
            assert remaining == 0
    finally:
        await store.close()


async def test_existing_task_schema_supports_startup_reconciliation(tmp_path) -> None:
    engine = create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'existing-tasks.db'}")
    store = SqlAlchemyTaskStore(engine)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("""
                CREATE TABLE rpa_tasks (
                    id VARCHAR(36) PRIMARY KEY,
                    flow_id VARCHAR(36), schedule_id VARCHAR(36),
                    flow_name VARCHAR(120) NOT NULL,
                    mode VARCHAR(16) NOT NULL, status VARCHAR(24) NOT NULL,
                    flow_revision INTEGER, definition_digest VARCHAR(64),
                    acceptance_contract JSON NOT NULL, run_config JSON NOT NULL,
                    progress_payload JSON NOT NULL, result_payload JSON,
                    execution_evidence_payload JSON NOT NULL,
                    error_message TEXT, confirmation_message TEXT,
                    created_at DATETIME NOT NULL, started_at DATETIME,
                    finished_at DATETIME, updated_at DATETIME NOT NULL
                )
            """)
            await connection.exec_driver_sql("""
                INSERT INTO rpa_tasks (
                    id, flow_name, mode, status, acceptance_contract, run_config,
                    progress_payload, execution_evidence_payload, created_at, updated_at
                ) VALUES (
                    'interrupted-task', '待对账任务', 'run', 'queued', '{}', '{}',
                    '{"currentStep":0,"totalSteps":1,"percent":0,"elapsedMs":0}', '[]',
                    '2026-10-08 00:00:00', '2026-10-08 00:00:00'
                )
            """)

        await store.create_schema()
        manager = TaskManager(runner=FakeRunner(), broker=LogBroker(), task_store=store)
        assert await manager.reconcile_interrupted_tasks() == 1
        stopped = await store.get_task("interrupted-task")
        assert stopped is not None
        assert stopped.status == "stopped"
        assert await store.list_unfinished_tasks() == []
        assert await manager.reconcile_interrupted_tasks() == 0
        logs = await store.list_logs("interrupted-task")
        assert logs is not None and len(logs) == 1
        await store.save_task(build_task_snapshot(), build_task_request())
        assert [task.task_id for task in await store.list_unfinished_tasks()] == ["task-1"]
        async with engine.connect() as connection:
            columns = {row[1] for row in (await connection.exec_driver_sql("PRAGMA table_info(rpa_tasks)")).all()}
        assert not columns & {"target_url", "selector", "fetcher", "extract_mode", "timeout_ms", "request_payload", "artifacts_payload", "variables_payload"}
    finally:
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


async def test_success_rates_use_all_recent_terminal_tasks_without_loading_payloads(tmp_path) -> None:
    from datetime import timedelta
    from app.services.task_store import InMemoryTaskStore

    engine = create_schedule_engine(f"sqlite+aiosqlite:///{tmp_path / 'rates.db'}")
    sql_store = SqlAlchemyTaskStore(engine)
    await sql_store.create_schema()
    now = datetime.now(UTC)
    try:
        for store in (InMemoryTaskStore(), sql_store):
            for index in range(205):
                task = build_task_snapshot(str(index)).model_copy(update={
                    "status": "success" if index < 100 else "error", "updated_at": now,
                })
                await store.save_task(task, build_task_request())
            for index, status, updated in ((206, "running", now), (207, "error", now - timedelta(days=31))):
                await store.save_task(build_task_snapshot(str(index)).model_copy(update={
                    "status": status, "updated_at": updated,
                }), build_task_request())
            if isinstance(store, SqlAlchemyTaskStore):
                def no_payload_read(row):
                    raise AssertionError("success rate must not deserialize task payloads")
                store._to_snapshot = no_payload_read
            assert await store.success_rates_since(now - timedelta(days=30)) == {
                "00000000-0000-0000-0000-000000000101": 49,
            }
    finally:
        await engine.dispose()
