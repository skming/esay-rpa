from __future__ import annotations

import pytest

from app.models.schemas import ScrapeResult
from app.services.runtime_variables import RuntimeVariableStore, apply_fetch_result_variables, build_input_variables, parse_input_variable_value


@pytest.mark.parametrize(("variable_type", "value", "expected"), [
    ("String", "123", "123"), ("String", "false", "false"),
    ("Integer", " +12 ", 12), ("Integer", -3, -3),
    ("Boolean", "FALSE", False), ("Boolean", True, True),
    ("List", '[1, "2"]', [1, "2"]), ("List", [], []),
    ("Dict", '{"count": 2}', {"count": 2}), ("Dict", {}, {}),
])
def test_input_values_follow_declared_type(variable_type, value, expected) -> None:
    result = parse_input_variable_value(variable_type, value, name="input")
    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize(("variable_type", "value"), [
    ("Integer", "12abc"), ("Integer", "1.5"), ("Integer", ""), ("Integer", True),
    ("Integer", 9_007_199_254_740_992), ("Boolean", "yes"),
    ("Integer", "9" * 5_000),
    ("List", "{}"), ("Dict", "[]"), ("List", "secret-invalid-json"), ("String", 123),
])
def test_invalid_input_types_are_rejected_without_exposing_values(variable_type, value) -> None:
    with pytest.raises(ValueError) as error:
        parse_input_variable_value(variable_type, value, name="input")
    assert str(error.value) == f"输入变量 input 必须为有效的 {variable_type}"


def test_input_overrides_are_validated_and_replace_defaults() -> None:
    declared = [{"name": "count", "type": "Integer", "value": "invalid-default"}]
    assert build_input_variables(declared, {"count": 4, "extra": {"ok": True}}) == {"count": 4, "extra": {"ok": True}}
    with pytest.raises(ValueError, match="输入变量缺少"):
        build_input_variables(["invalid"])


def test_input_defaults_and_overrides_share_the_variable_count_limit() -> None:
    declared = [{"name": f"input_{index}", "type": "String", "value": ""} for index in range(100)]
    assert len(build_input_variables(declared)) == 100
    with pytest.raises(ValueError, match="variables 最多支持 100 个变量"):
        build_input_variables(declared, {"extra": ""})


def test_runtime_variables_resolve_nested_templates() -> None:
    variables = RuntimeVariableStore.from_initial(
        {
            "page_no": 2,
            "list_url_template": "https://example.com/search?page=${var.page_no}",
        }
    )

    assert variables.resolve_text("${var.list_url_template}") == "https://example.com/search?page=2"


def test_runtime_variables_reject_template_cycles() -> None:
    variables = RuntimeVariableStore.from_initial({"a": "${var.b}", "b": "${var.a}"})

    with pytest.raises(ValueError, match="嵌套过深"):
        variables.resolve_text("${var.a}")


def test_apply_fetch_result_variables_accepts_response_variable_alias() -> None:
    variables = RuntimeVariableStore.from_initial({})
    result = ScrapeResult(url="https://example.com", selector=".item", count=2, values=["A", "B"])

    saved_names = apply_fetch_result_variables({"responseVariable": "items"}, result, variables)
    snapshots = {variable.name: variable for variable in variables.snapshots()}

    assert saved_names == ["items"]
    assert snapshots["items"].value == '["A", "B"]'
    assert snapshots["items"].type == "List"


def test_apply_fetch_result_variables_appends_values_across_iterations() -> None:
    variables = RuntimeVariableStore.from_initial({"all_items": ["A"]})
    first_result = ScrapeResult(url="https://example.com/1", selector=".item", count=1, values=["B"])
    second_result = ScrapeResult(url="https://example.com/2", selector=".item", count=2, values=["C", "D"])

    first_saved = apply_fetch_result_variables({"appendVariable": "all_items"}, first_result, variables)
    second_saved = apply_fetch_result_variables({"appendVariable": "all_items"}, second_result, variables)

    assert first_saved == ["all_items"]
    assert second_saved == ["all_items"]
    assert variables.get("all_items") == ["A", "B", "C", "D"]


def test_apply_fetch_result_variables_appends_record_payload() -> None:
    variables = RuntimeVariableStore.from_initial({})
    result = ScrapeResult(url="https://example.com", selector=".item", count=2, values=["A", "B"])

    saved_names = apply_fetch_result_variables({"appendVariable": "records", "appendMode": "record"}, result, variables)

    assert saved_names == ["records"]
    assert variables.get("records") == [{"count": 2, "first": "A", "values": ["A", "B"]}]
