from __future__ import annotations

import pytest

from app.services.data_action_runner import DataActionRunner, apply_data_result_variables
from app.services.runtime_variables import RuntimeVariableStore


async def test_data_runner_parses_json_and_writes_variables() -> None:
    variables = RuntimeVariableStore.from_initial({"payload": '{"items":["A","B"]}'})
    node = {
        "type": "data.json.parse",
        "inputVariable": "payload",
        "outputVariable": "parsed",
        "countVariable": "parsed_count",
    }

    result = await DataActionRunner().run(node, variables, timeout_ms=1_000)
    saved = apply_data_result_variables(node, result, variables)

    assert result.value == {"items": ["A", "B"]}
    assert result.count == 1
    assert saved == ["parsed", "parsed_count"]
    assert variables.get("parsed") == {"items": ["A", "B"]}


async def test_data_runner_accepts_response_and_status_variable_aliases() -> None:
    variables = RuntimeVariableStore.from_initial({"payload": '{"items":["A","B"]}'})
    node = {
        "type": "data.json.parse",
        "inputVariable": "payload",
        "responseVariable": "parsed",
        "statusVariable": "parsed_count",
    }

    result = await DataActionRunner().run(node, variables, timeout_ms=1_000)
    saved = apply_data_result_variables(node, result, variables)

    assert saved == ["parsed", "parsed_count"]
    assert variables.get("parsed") == {"items": ["A", "B"]}
    assert variables.get("parsed_count") == 1


async def test_data_runner_matches_regex_and_first_value() -> None:
    variables = RuntimeVariableStore()
    node = {
        "type": "data.regex.match",
        "inputValue": "订单 A001 和 A002",
        "pattern": "A(\\d+)",
        "outputVariable": "matches",
        "firstValueVariable": "first_match",
        "countVariable": "match_count",
    }

    result = await DataActionRunner().run(node, variables, timeout_ms=1_000)
    apply_data_result_variables(node, result, variables)

    assert result.values == ["001", "002"]
    assert variables.get("first_match") == "001"
    assert variables.get("match_count") == 2


async def test_data_runner_computes_math_safely() -> None:
    variables = RuntimeVariableStore.from_initial({"left": 8, "right": 4})
    node = {
        "type": "data.math.compute",
        "leftVariable": "left",
        "rightVariable": "right",
        "operator": "divide",
        "outputVariable": "quotient",
    }

    result = await DataActionRunner().run(node, variables, timeout_ms=1_000)
    apply_data_result_variables(node, result, variables)

    assert result.value == 2
    assert variables.get("quotient") == 2


async def test_data_runner_rejects_divide_by_zero() -> None:
    with pytest.raises(ValueError, match="除以 0"):
        await DataActionRunner().run({"type": "data.math.compute", "left": "8", "right": "0", "operator": "divide"}, RuntimeVariableStore(), timeout_ms=1_000)
