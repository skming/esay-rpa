from __future__ import annotations

from app.services.control_action_runner import ControlActionRunner, apply_control_result_variables
from app.services.runtime_variables import RuntimeVariableStore


async def test_control_delay_accepts_response_variable_alias() -> None:
    variables = RuntimeVariableStore.from_initial({})
    node = {"type": "control.delay", "delayMs": 1, "responseVariable": "delay_ms"}

    result = await ControlActionRunner().run(node, variables, timeout_ms=1000)
    saved = apply_control_result_variables(node, result, variables)

    assert saved == ["delay_ms"]
    assert variables.get("delay_ms") == "1"
