from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.schemas import RunTaskRequest, ScheduleSnapshot
from app.services.schedule_store import SqlAlchemyScheduleStore, create_schedule_engine


def build_schedule(schedule_id: str = "schedule-1") -> ScheduleSnapshot:
    now = datetime.now(UTC)
    return ScheduleSnapshot(
        scheduleId=schedule_id,
        name="SQLAlchemy 调度",
        cronExpression="* * * * *",
        timezone="UTC",
        status="enabled",
        task=RunTaskRequest(
            flowName="持久化测试流程",
            targetUrl="https://quotes.toscrape.com/",
            selector=".quote .text::text",
            timeoutMs=1000,
        ),
        createdAt=now,
        updatedAt=now,
        nextRunAt=now + timedelta(minutes=1),
    )


async def test_sqlalchemy_schedule_store_persists_crud(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'schedules.db'}"
    engine = create_schedule_engine(database_url)
    store = SqlAlchemyScheduleStore(engine)
    await store.create_schema()

    schedule = await store.save(build_schedule())
    assert schedule.schedule_id == "schedule-1"

    listed = await store.list()
    assert [item.schedule_id for item in listed] == ["schedule-1"]
    assert listed[0].task.flow_name == "持久化测试流程"

    updated = listed[0].model_copy(update={"next_run_at": datetime.now(UTC) - timedelta(seconds=1), "last_task_id": "task-1", "last_error": "调度绑定的流程不存在"})
    await store.save(updated)

    due = await store.due(datetime.now(UTC))
    assert [item.schedule_id for item in due] == ["schedule-1"]
    assert due[0].last_task_id == "task-1"
    assert due[0].last_error == "调度绑定的流程不存在"

    deleted = await store.delete("schedule-1")
    assert deleted is True
    assert await store.get("schedule-1") is None

    await engine.dispose()


async def test_sqlite_roundtrip_returns_timezone_aware_timestamps(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'schedules.db'}"
    engine = create_schedule_engine(database_url)
    store = SqlAlchemyScheduleStore(engine)
    await store.create_schema()

    saved = await store.save(build_schedule("schedule-tz"))
    reloaded = await store.get("schedule-tz")
    assert reloaded is not None
    # SQLite 不保存时区，UTCDateTime 负责在读回时补齐 UTC。
    assert reloaded.created_at.tzinfo is not None
    assert reloaded.updated_at.tzinfo is not None
    assert reloaded.next_run_at is not None and reloaded.next_run_at.tzinfo is not None
    assert reloaded.next_run_at == saved.next_run_at
    # aware 时间可直接与 now(UTC) 混比，不再抛 TypeError。
    assert reloaded.next_run_at > datetime.now(UTC) - timedelta(hours=1)

    await engine.dispose()
