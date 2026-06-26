from __future__ import annotations

import os
import tempfile
from pathlib import Path


_TEST_APP_DATA_DIR = Path(tempfile.mkdtemp(prefix="easy-rpa-tests-"))

# 测试显式声明隔离数据目录，避免 API 测试导入 app.main 时写入真实用户目录。
os.environ["RPA_APP_DATA_DIR"] = str(_TEST_APP_DATA_DIR)
os.environ["RPA_WORKSPACE_ROOT"] = str(_TEST_APP_DATA_DIR / "workspace")
os.environ["RPA_LOG_DIR"] = str(_TEST_APP_DATA_DIR / "logs")
os.environ["RPA_CACHE_DIR"] = str(_TEST_APP_DATA_DIR / "cache")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_APP_DATA_DIR / 'db' / 'rpa.sqlite3'}"

for directory in (
    _TEST_APP_DATA_DIR / "db",
    _TEST_APP_DATA_DIR / "workspace",
    _TEST_APP_DATA_DIR / "workspace" / "runs",
    _TEST_APP_DATA_DIR / "logs",
    _TEST_APP_DATA_DIR / "cache",
    _TEST_APP_DATA_DIR / "runtime" / "browser",
    _TEST_APP_DATA_DIR / "runtime" / "scrapling",
    _TEST_APP_DATA_DIR / "ai" / "chats",
):
    directory.mkdir(parents=True, exist_ok=True)


def pytest_configure() -> None:
    """API 测试使用 ASGITransport 时不会自动跑 lifespan，因此测试套件显式建表。"""
    import asyncio
    import app.main as main_module

    asyncio.run(main_module.runtime_services.start())
