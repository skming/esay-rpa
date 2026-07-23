from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeVar
from app.core import storage

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# 约束 _read_backend 的返回类型与传入的 allowed 字面量集合一致，而非退化为 str
BackendName = TypeVar("BackendName", bound=str)


@dataclass(frozen=True)
class Settings:
    """所有路径默认在 ~/.easy-rpa 下，可用 RPA_* 环境变量覆盖。"""

    task_concurrency: int = 2
    task_queue_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://127.0.0.1:6379/0"
    task_queue_name: str = "rpa:tasks"
    flow_store_backend: Literal["memory", "sqlalchemy"] = "memory"
    task_store_backend: Literal["memory", "sqlalchemy"] = "memory"
    schedule_store_backend: Literal["memory", "sqlalchemy"] = "memory"
    database_url: str = "postgresql+asyncpg://rpa:rpa@127.0.0.1:5432/rpa"
    artifact_store_backend: Literal["local", "minio"] = "local"
    artifact_minio_endpoint: str = "127.0.0.1:9000"
    artifact_minio_access_key: str = "minioadmin"
    artifact_minio_secret_key: str = "minioadmin"
    artifact_minio_bucket: str = "rpa-artifacts"
    artifact_minio_secure: bool = False
    # 应用本地数据目录（由 Electron 注入，回退到 ~/.easy-rpa）
    app_data_dir: str = ""
    log_dir: str = ""
    cache_dir: str = ""
    workspace_root: str = ""
    log_level: str = "INFO"
    log_backup_count: int = 30  # 单位：天
    log_module_levels: dict[str, str] = field(default_factory=dict)


def load_settings() -> Settings:
    app_data_dir = os.getenv("RPA_APP_DATA_DIR", "").strip() or str(storage.resolve_app_data_dir())
    log_dir = os.getenv("RPA_LOG_DIR", "").strip() or str(storage.resolve_logs_dir())
    cache_dir = os.getenv("RPA_CACHE_DIR", "").strip() or str(storage.resolve_cache_dir())
    workspace_root = os.getenv("RPA_WORKSPACE_ROOT", "").strip() or str(storage.resolve_workspace_root())
    database_url = os.getenv("DATABASE_URL") or _default_sqlite_database_url()
    return Settings(
        task_concurrency=_read_positive_int("RPA_TASK_CONCURRENCY", default=2),
        task_queue_backend=_read_queue_backend("RPA_TASK_QUEUE_BACKEND", default="memory"),
        redis_url=os.getenv("RPA_REDIS_URL", "redis://127.0.0.1:6379/0"),
        task_queue_name=os.getenv("RPA_TASK_QUEUE_NAME", "rpa:tasks").strip() or "rpa:tasks",
        flow_store_backend=_read_backend("RPA_FLOW_STORE_BACKEND", default="sqlalchemy", allowed={"memory", "sqlalchemy"}),
        task_store_backend=_read_backend("RPA_TASK_STORE_BACKEND", default="sqlalchemy", allowed={"memory", "sqlalchemy"}),
        schedule_store_backend=_read_backend("RPA_SCHEDULE_STORE_BACKEND", default="sqlalchemy", allowed={"memory", "sqlalchemy"}),
        database_url=database_url,
        artifact_store_backend=_read_backend("RPA_ARTIFACT_STORE_BACKEND", default="local", allowed={"local", "minio"}),
        artifact_minio_endpoint=os.getenv("RPA_MINIO_ENDPOINT", "127.0.0.1:9000"),
        artifact_minio_access_key=os.getenv("RPA_MINIO_ACCESS_KEY", "minioadmin"),
        artifact_minio_secret_key=os.getenv("RPA_MINIO_SECRET_KEY", "minioadmin"),
        artifact_minio_bucket=os.getenv("RPA_MINIO_BUCKET", "rpa-artifacts").strip() or "rpa-artifacts",
        artifact_minio_secure=_read_bool("RPA_MINIO_SECURE", default=False),
        app_data_dir=app_data_dir,
        log_dir=log_dir,
        cache_dir=cache_dir,
        workspace_root=workspace_root,
        log_level=_read_log_level("RPA_LOG_LEVEL", default="INFO"),
        log_backup_count=_read_positive_int("RPA_LOG_BACKUP_COUNT", default=30),
        log_module_levels=_read_module_levels("RPA_LOG_LEVELS"),
    )


def _default_sqlite_database_url() -> str:
    database_path = storage.resolve_database_path()
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{database_path}"


def _read_module_levels(name: str) -> dict[str, str]:
    """解析 RPA_LOG_LEVELS=module:LEVEL,module:LEVEL，如 app.services.scheduler_service:WARNING,litellm:ERROR。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    result: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(f"{name} 格式错误：每项须为 module:LEVEL，实际得到 '{entry}'")
        module, level = entry.split(":", 1)
        level = level.strip().upper()
        if level not in _VALID_LOG_LEVELS:
            raise ValueError(f"{name} 中模块 '{module}' 的级别 '{level}' 无效，只能是 {', '.join(sorted(_VALID_LOG_LEVELS))}")
        result[module.strip()] = level
    return result


def _read_log_level(name: str, default: str) -> str:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    value = raw_value.strip().upper()
    if value not in _VALID_LOG_LEVELS:
        raise ValueError(f"{name} 只能是 {', '.join(sorted(_VALID_LOG_LEVELS))}")
    return value


def _read_positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是正整数") from exc
    if value < 1:
        raise ValueError(f"{name} 必须大于等于 1")
    return value


def _read_queue_backend(name: str, default: Literal["memory", "redis"]) -> Literal["memory", "redis"]:
    value = _read_backend(name, default=default, allowed={"memory", "redis"})
    if value not in {"memory", "redis"}:
        raise ValueError(f"{name} 只能是 memory 或 redis")
    return value


def _read_backend(name: str, *, default: BackendName, allowed: set[BackendName]) -> BackendName:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = raw_value.strip().lower()
    if value not in allowed:
        allowed_text = " 或 ".join(sorted(allowed))
        raise ValueError(f"{name} 只能是 {allowed_text}")
    return value


def _read_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是布尔值")
