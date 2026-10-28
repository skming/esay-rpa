"""AI 编排用的 RPA 工具集。

本模块只做对外再导出，实现按职责分散在同包各文件。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.ai_tools.catalog import NODE_TYPE_CATALOG
from app.services.ai_tools.schemas import TOOL_SCHEMAS

if TYPE_CHECKING:
    from app.services.ai_tools.executor import RpaToolExecutor


def __getattr__(name: str):
    # diagnostics 等底层模块也会加载本包，不能在此反向加载依赖审计的执行器。
    if name == "RpaToolExecutor":
        from app.services.ai_tools.executor import RpaToolExecutor
        return RpaToolExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["NODE_TYPE_CATALOG", "TOOL_SCHEMAS", "RpaToolExecutor"]
