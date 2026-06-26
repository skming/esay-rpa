from __future__ import annotations

import pytest

from app.core.config import load_settings


def test_load_settings_supports_redis_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPA_TASK_QUEUE_BACKEND", "redis")
    monkeypatch.setenv("RPA_REDIS_URL", "redis://redis:6379/2")
    monkeypatch.setenv("RPA_TASK_QUEUE_NAME", "custom:rpa:tasks")
    monkeypatch.setenv("RPA_TASK_CONCURRENCY", "4")
    monkeypatch.setenv("RPA_FLOW_STORE_BACKEND", "sqlalchemy")
    monkeypatch.setenv("RPA_TASK_STORE_BACKEND", "sqlalchemy")
    monkeypatch.setenv("RPA_SCHEDULE_STORE_BACKEND", "sqlalchemy")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://rpa:rpa@db:5432/rpa")
    monkeypatch.setenv("RPA_ARTIFACT_STORE_BACKEND", "minio")
    monkeypatch.setenv("RPA_MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("RPA_MINIO_ACCESS_KEY", "access")
    monkeypatch.setenv("RPA_MINIO_SECRET_KEY", "secret")
    monkeypatch.setenv("RPA_MINIO_BUCKET", "rpa-results")
    monkeypatch.setenv("RPA_MINIO_SECURE", "true")

    settings = load_settings()

    assert settings.task_queue_backend == "redis"
    assert settings.redis_url == "redis://redis:6379/2"
    assert settings.task_queue_name == "custom:rpa:tasks"
    assert settings.task_concurrency == 4
    assert settings.flow_store_backend == "sqlalchemy"
    assert settings.task_store_backend == "sqlalchemy"
    assert settings.schedule_store_backend == "sqlalchemy"
    assert settings.database_url == "postgresql+asyncpg://rpa:rpa@db:5432/rpa"
    assert settings.artifact_store_backend == "minio"
    assert settings.artifact_minio_endpoint == "minio:9000"
    assert settings.artifact_minio_access_key == "access"
    assert settings.artifact_minio_secret_key == "secret"
    assert settings.artifact_minio_bucket == "rpa-results"
    assert settings.artifact_minio_secure is True


def test_load_settings_creates_default_sqlite_parent_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    app_data_dir = tmp_path / "app-data"
    monkeypatch.setenv("RPA_APP_DATA_DIR", str(app_data_dir))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = load_settings()

    assert settings.database_url == f"sqlite+aiosqlite:///{app_data_dir / 'db' / 'rpa.sqlite3'}"
    assert (app_data_dir / "db").is_dir()


def test_load_settings_rejects_invalid_queue_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPA_TASK_QUEUE_BACKEND", "kafka")

    with pytest.raises(ValueError, match="RPA_TASK_QUEUE_BACKEND"):
        load_settings()


def test_load_settings_rejects_invalid_schedule_store_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPA_SCHEDULE_STORE_BACKEND", "local-file")

    with pytest.raises(ValueError, match="RPA_SCHEDULE_STORE_BACKEND"):
        load_settings()


def test_load_settings_rejects_invalid_task_store_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPA_TASK_STORE_BACKEND", "mongo")

    with pytest.raises(ValueError, match="RPA_TASK_STORE_BACKEND"):
        load_settings()


def test_load_settings_rejects_invalid_flow_store_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPA_FLOW_STORE_BACKEND", "filesystem")

    with pytest.raises(ValueError, match="RPA_FLOW_STORE_BACKEND"):
        load_settings()


def test_load_settings_rejects_invalid_artifact_store_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPA_ARTIFACT_STORE_BACKEND", "ftp")

    with pytest.raises(ValueError, match="RPA_ARTIFACT_STORE_BACKEND"):
        load_settings()
