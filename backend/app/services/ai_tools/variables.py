"""节点里变量引用与定义的提取原语。

lint 规则、执行器的运行前校验都要判断"这个变量在此处是否已定义"，
统一放这里避免各处各写一套 ${var.x} 解析。
"""
from __future__ import annotations

import re
from typing import Any

# Variable reference helpers

_VAR_REF_RE = re.compile(r'\$\{var\.([^}]+)\}')

# Fields that DEFINE (output) a new variable
# indexVariable 由循环节点自己写入（foreach 的 loop_index、repeat_until 的 repeat_index），
# 不列进来的话下游引用它会被 validate_flow 误报「变量未定义」。
_OUTPUT_FIELDS = ("outputVariable", "countVariable", "firstValueVariable", "itemVariable", "indexVariable", "errorVariable")
_VARIABLE_NAME_FIELDS = frozenset({
    "variableName",
    "name",
    "outputVariable",
    "responseVariable",
    "resultVariable",
    "saveAs",
    "countVariable",
    "firstValueVariable",
    "appendVariable",
    "appendOutputVariable",
    "itemsVariable",
    "itemVariable",
    "indexVariable",
    "inputVariable",
    "jsonVariable",
    "statusVariable",
    "errorVariable",
})
_CONDITION_EXPRESSION_FIELDS = ("condition", "expression", "inputValue")
# 无歧义的"整串"标记：本身带标点/括号，子串匹配不会误伤普通变量名。
_SCRIPT_HTTP_FETCH_MARKERS = (
    # Python
    "urllib.request",
    "requests.",
    "httpx.",
    "aiohttp.",
    "urlopen(",
    "pycurl.",
    "http.client",
    "socket.create_connection",
    "socket.socket(",
    # JavaScript/Node
    "fetch(",
    "axios.",
    "xmlhttprequest",
    "node-fetch",
    "http.request(",
    "https.request(",
    "http.get(",
    "https.get(",
)
# 裸词标记：如 curl/wget 出现在参数列表里（如 subprocess.run(['curl', url])）时不带
# 尾随空格/括号，必须用词边界正则匹配才能命中，纯子串匹配会漏掉这类调用。
_SCRIPT_HTTP_FETCH_WORD_MARKERS = (
    "curl",
    "wget",
    "invoke-webrequest",
    "invoke-restmethod",
)
_SCRIPT_HTTP_FETCH_WORD_PATTERN = re.compile(
    r"\b(" + "|".join(_SCRIPT_HTTP_FETCH_WORD_MARKERS) + r")\b"
)
_SCRIPT_CHANNEL_NODE_TYPES = frozenset({"script.python", "script.javascript", "script.shell"})


def _find_script_http_fetch_marker(code: str) -> str | None:
    if not code:
        return None
    lowered = code.lower()
    for marker in _SCRIPT_HTTP_FETCH_MARKERS:
        if marker in lowered:
            return marker
    word_match = _SCRIPT_HTTP_FETCH_WORD_PATTERN.search(lowered)
    if word_match is not None:
        return word_match.group(1)
    return None

# Runtime-injected builtins — always considered defined (injected by the executor before each run)
_RUNTIME_BUILTINS = frozenset(["run_timestamp", "flow_slug", "output_dir", "output_prefix"])


def _collect_defined_vars(nodes: list[Any], input_variable_names: list[str]) -> set[str]:
    """Return the set of all variable names that are defined by this flow."""
    defined: set[str] = set(_RUNTIME_BUILTINS)
    defined.update(input_variable_names)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for field in _OUTPUT_FIELDS:
            val = node.get(field)
            if isinstance(val, str) and val.strip():
                defined.add(val.strip())
        # variable.set and variable.input both define variableName
        if node.get("type") in ("variable.set", "variable.input"):
            vname = node.get("variableName")
            if isinstance(vname, str) and vname.strip():
                defined.add(vname.strip())
        # script.python / script.javascript may output via outputVariables list
        for item in node.get("outputVariables") or []:
            if isinstance(item, str) and item.strip():
                defined.add(item.strip())
    return defined


def _collect_refs_in_node(node: dict[str, Any]) -> set[str]:
    """Return all ${var.xxx} names referenced anywhere inside a node, recursively."""
    refs: set[str] = set()

    def _walk(obj: Any) -> None:
        if isinstance(obj, str):
            for m in _VAR_REF_RE.finditer(obj):
                refs.add(m.group(1))
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    for val in node.values():
        _walk(val)
    return refs


def _template_refs(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [match.group(1) for match in _VAR_REF_RE.finditer(value)]


def _collect_output_vars_in_node(node: dict[str, Any]) -> set[str]:
    """返回节点可能写入的变量名，用于判断容错节点失败后是否会留下未定义变量。"""
    vars_: set[str] = set()
    for field in _OUTPUT_FIELDS:
        val = node.get(field)
        if isinstance(val, str) and val.strip():
            vars_.add(val.strip())
    for item in node.get("outputVariables") or []:
        if isinstance(item, str) and item.strip():
            vars_.add(item.strip())
    return vars_




def _validate_variable_refs(
    nodes: list[Any],
    input_variable_names: list[str],
) -> list[dict[str, Any]]:
    """Return a list of issues for nodes that reference undefined variables."""
    defined = _collect_defined_vars(nodes, input_variable_names)
    issues = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("type", "")
        if node_type in ("start", "end"):
            continue
        refs = _collect_refs_in_node(node)
        undefined = refs - defined
        if undefined:
            issues.append({
                "node_id": node.get("id", "?"),
                "node_title": node.get("title", node.get("id", "?")),
                "node_type": node_type,
                "undefined_variables": sorted(undefined),
            })
    return issues
