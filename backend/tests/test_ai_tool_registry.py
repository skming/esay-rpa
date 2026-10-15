from unittest.mock import AsyncMock

import pytest

from app.services.ai_tool_registry import bind_tool_schemas
from app.services.ai_tools.executor import RpaToolExecutor
from app.services.ai_tools.lint_diff import ChangeContext
from app.services.ai_tools.schemas import TOOL_SCHEMAS


@pytest.mark.parametrize("schemas", [
    TOOL_SCHEMAS[:-1],
    [*TOOL_SCHEMAS, TOOL_SCHEMAS[0]],
    [*TOOL_SCHEMAS, {"function": {"name": "audit_run"}}],
])
def test_schema_binding_rejects_incomplete_duplicate_or_internal_tools(schemas):
    with pytest.raises(ValueError, match="public registrations"):
        bind_tool_schemas(schemas)


@pytest.mark.parametrize("tool,handler,args,extra", [
    ("get_flow", "_get_flow", {"flow_id": "f"}, {}),
    ("create_flow", "_create_flow", {"name": "fixture"}, {"change_context": "context"}),
    ("update_flow", "_update_flow", {"flow_id": "f"}, {"change_context": "context"}),
    ("apply_node_fix", "_apply_node_fix", {"flow_id": "f", "node_id": "n"}, {"change_context": "context"}),
    ("set_acceptance_contract", "_set_acceptance_contract", {"flow_id": "f"}, {"change_context": "context"}),
    ("run_flow", "_run_flow", {"flow_id": "f"}, {"progress_sink": "progress"}),
    ("list_schedules", "_list_schedules", {}, {"flow_id": "f"}),
    ("toggle_schedule", "_toggle_schedule", {"schedule_id": "s", "enabled": True}, {"expected_flow_id": "f"}),
    ("audit_run", "_audit_run", {"task_id": "t"}, {}),
])
async def test_registered_dispatch_preserves_trusted_context(monkeypatch, tool, handler, args, extra):
    executor = RpaToolExecutor(None, None)
    context = ChangeContext(flow_id="f")
    progress = {}
    expected = {key: context if value == "context" else progress if value == "progress" else value
                for key, value in extra.items()}
    call = AsyncMock(return_value={"status": "fixture"})
    monkeypatch.setattr(executor, handler, call)
    original = dict(args)
    assert await executor._dispatch(tool, args, progress, context) == {"status": "fixture"}
    call.assert_awaited_once_with(**args, **expected)
    assert args == original


async def test_unknown_tool_cannot_select_an_executor_attribute():
    executor = RpaToolExecutor(None, None)
    assert "未知工具" in (await executor._dispatch("__getattribute__", {}, None, None))["error"]
