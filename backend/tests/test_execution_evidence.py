from __future__ import annotations

from app.services.execution_evidence import build_node_execution_evidence
from app.services.runtime_variables import RuntimeVariableStore


def test_evidence_only_records_declared_inputs_and_outputs() -> None:
    variables = RuntimeVariableStore.from_initial({
        "raw_text": "正文",
        "unrelated_token": "不得进入该节点证据",
    })
    before = variables.raw_values()
    variables.set("cleaned_text", "正文", scope="局部")

    evidence = build_node_execution_evidence(
        {
            "id": "clean",
            "type": "script.python",
            "inputVariables": ["raw_text"],
            "outputVariable": "cleaned_text",
        },
        before,
        variables,
        duration_ms=125,
        browser_url="https://example.com/orders",
        match_count=1,
    )

    assert [item.name for item in evidence.inputs] == ["raw_text"]
    assert [item.name for item in evidence.outputs] == ["cleaned_text"]
    assert evidence.unchanged_pairs == ["raw_text->cleaned_text"]
    assert variables.producer_of("cleaned_text") == "clean"
    assert evidence.duration_ms == 125
    assert evidence.browser_url == "https://example.com/orders"
    assert evidence.match_count == 1


def test_sensitive_values_never_receive_a_content_digest() -> None:
    variables = RuntimeVariableStore.from_initial(
        {"api_token": "secret", "result": "secret"},
        sensitive_names={"api_token"},
    )
    before = variables.raw_values()

    evidence = build_node_execution_evidence(
        {
            "id": "request",
            "type": "script.python",
            "inputVariables": ["api_token"],
            "outputVariable": "result",
        },
        before,
        variables,
    )

    assert evidence.inputs[0].digest is None
    assert evidence.inputs[0].comparable is False
    assert evidence.unchanged_pairs == []
