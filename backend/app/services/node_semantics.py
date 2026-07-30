from __future__ import annotations

from typing import Literal

NodeSemanticRole = Literal["extract", "wait", "transform", "validate", "export", "other"]

EXTRACT_NODE_TYPES = frozenset({"browser.extract", "ui.extract", "browser.fetch"})
WAIT_NODE_TYPES = frozenset({"browser.wait", "browser.waitFor", "ui.wait"})
TRANSFORM_NODE_TYPES = frozenset({
    "script.python",
    "script.javascript",
    "script.shell",
    "data.string.transform",
    "data.regex.match",
    "data.list.map",
    "data.convert",
})


def node_semantic_role(node_type: str) -> NodeSemanticRole:
    if node_type in EXTRACT_NODE_TYPES:
        return "extract"
    if node_type in WAIT_NODE_TYPES:
        return "wait"
    if node_type in TRANSFORM_NODE_TYPES:
        return "transform"
    if node_type.startswith(("file.", "excel.")):
        return "export"
    return "other"
