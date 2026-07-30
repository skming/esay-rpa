from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.models.schemas import NodeExecutionEvidence, VariableEvidence
from app.services.runtime_variables import RuntimeVariableStore, infer_variable_type

_VAR_REF_RE = re.compile(r"\$\{var\.([^}]+)\}")
_MAX_COMPARABLE_BYTES = 2 * 1024 * 1024
_INPUT_NAME_FIELDS = (
    "inputVariable",
    "jsonVariable",
    "itemsVariable",
    "leftVariable",
    "rightVariable",
)
_OUTPUT_NAME_FIELDS = (
    "outputVariable",
    "responseVariable",
    "resultVariable",
    "saveAs",
    "countVariable",
    "firstValueVariable",
    "statusVariable",
    "errorVariable",
    "stderrVariable",
    "appendVariable",
    "appendOutputVariable",
    "variableName",
)


def definition_digest(definition: dict[str, object] | None) -> str | None:
    if definition is None:
        return None
    payload = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def collect_node_input_names(node: dict[str, object]) -> list[str]:
    names: set[str] = set()
    declared = node.get("inputVariables")
    if isinstance(declared, list):
        names.update(str(item).strip() for item in declared if isinstance(item, str) and item.strip())
    for field in _INPUT_NAME_FIELDS:
        value = node.get(field)
        if isinstance(value, str) and value.strip() and "${var." not in value:
            names.add(value.strip())

    def walk(value: object) -> None:
        if isinstance(value, str):
            names.update(match.group(1) for match in _VAR_REF_RE.finditer(value))
        elif isinstance(value, dict):
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(node)
    return sorted(names)


def collect_node_output_names(node: dict[str, object]) -> list[str]:
    names: set[str] = set()
    for field in _OUTPUT_NAME_FIELDS:
        value = node.get(field)
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
    declared = node.get("outputVariables")
    if isinstance(declared, list):
        names.update(str(item).strip() for item in declared if isinstance(item, str) and item.strip())
    return sorted(names)


def build_node_execution_evidence(
    node: dict[str, object],
    before: dict[str, object],
    variables: RuntimeVariableStore,
    *,
    duration_ms: int = 0,
    browser_url: str | None = None,
    match_count: int | None = None,
) -> NodeExecutionEvidence:
    node_id = str(node.get("id") or "node")
    input_names = collect_node_input_names(node)
    output_names = [name for name in collect_node_output_names(node) if name in variables.raw_values()]
    inputs = [
        _build_variable_evidence(name, before[name], variables)
        for name in input_names
        if name in before
    ]
    current = variables.raw_values()
    outputs = [
        _build_variable_evidence(name, current[name], variables).model_copy(
            update={"producer_node_id": node_id}
        )
        for name in output_names
    ]
    unchanged_pairs = [
        f"{source.name}->{target.name}"
        for source in inputs
        for target in outputs
        if source.comparable and target.comparable and source.digest == target.digest
    ]
    variables.mark_producer(output_names, node_id)
    return NodeExecutionEvidence(
        nodeId=node_id,
        nodeType=str(node.get("type") or "unknown"),
        inputs=inputs,
        outputs=outputs,
        unchangedPairs=unchanged_pairs,
        durationMs=duration_ms,
        browserUrl=browser_url,
        selector=str(node.get("selector")) if node.get("selector") else None,
        matchCount=match_count,
    )


def _build_variable_evidence(
    name: str,
    value: object,
    variables: RuntimeVariableStore,
) -> VariableEvidence:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    encoded = rendered.encode("utf-8")
    comparable = len(encoded) <= _MAX_COMPARABLE_BYTES and not variables.is_sensitive(name)
    item_count = len(value) if isinstance(value, list | dict) else None
    return VariableEvidence(
        name=name,
        type=infer_variable_type(value),
        charCount=len(rendered),
        itemCount=item_count,
        digest=hashlib.sha256(encoded).hexdigest() if comparable else None,
        comparable=comparable,
        producerNodeId=variables.producer_of(name),
    )
