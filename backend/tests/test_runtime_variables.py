from __future__ import annotations

import pytest

from app.models.schemas import ScrapeResult
from app.services.runtime_variables import RuntimeVariableStore, apply_fetch_result_variables


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
