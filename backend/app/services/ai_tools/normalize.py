"""AI 生成的节点/边归一化，以及画布布局原语。

AI 返回的结构常缺 kind/title/position，直接入库会让 Studio 渲染异常。
"""
from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

_KIND_BY_TYPE_PREFIX = {
    "browser": "browser",
    "ui": "browser",
    "excel": "excel",
    "file": "file",
    "http": "http",
    "variable": "variable",
    "control": "control",
    "data": "data",
    "script": "python",
}


def _normalize_generated_node(node: Any, index: int) -> Any:
    """把不同模型的节点输出规整为执行器使用的平铺结构。"""
    if not isinstance(node, dict):
        return node

    normalized: dict[str, Any] = {}
    action_payload = node.get("action") if isinstance(node.get("action"), dict) else None
    config_payload = node.get("config") if isinstance(node.get("config"), dict) else None
    for payload in (action_payload, config_payload, node):
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            if key in {"action", "config", "data"}:
                continue
            normalized[key] = value

    node_id = str(normalized.get("id") or f"n_{uuid4()}")
    node_type = str(normalized.get("type") or "")
    if not node_type and action_payload:
        node_type = str(action_payload.get("type") or "")
    normalized["id"] = node_id
    if node_type:
        normalized["type"] = node_type
    normalized.setdefault("title", _default_node_title(node_type, node_id))
    normalized.setdefault("status", "pending")
    normalized.setdefault("description", str(normalized.get("title", node_id)))

    kind = normalized.get("kind")
    if not isinstance(kind, str) or not kind:
        normalized["kind"] = _KIND_BY_TYPE_PREFIX.get(node_type.split(".")[0], "control")

    position = normalized.get("position")
    if not isinstance(position, dict):
        normalized["position"] = {"x": 560, "y": 20 + index * 120}
    else:
        normalized["position"] = {
            "x": _read_layout_number(position.get("x"), 560),
            "y": _read_layout_number(position.get("y"), 20 + index * 120),
        }

    if isinstance(normalized.get("delayMs"), str) and str(normalized["delayMs"]).isdigit():
        normalized["delayMs"] = int(normalized["delayMs"])
    if isinstance(normalized.get("timeoutMs"), str) and str(normalized["timeoutMs"]).isdigit():
        normalized["timeoutMs"] = int(normalized["timeoutMs"])
    if node_type in {"browser.open", "browser.tab.open"} and not normalized.get("targetUrl") and normalized.get("url"):
        normalized["targetUrl"] = normalized["url"]
    if node_type == "browser.fetch" and not normalized.get("targetUrl") and normalized.get("url"):
        normalized["targetUrl"] = normalized["url"]
    if node_type == "browser.fill" and not normalized.get("inputValue") and normalized.get("value") is not None:
        normalized["inputValue"] = normalized["value"]
    if node_type == "browser.press" and not normalized.get("inputValue") and normalized.get("key") is not None:
        normalized["inputValue"] = normalized["key"]
    if (
        node_type.startswith(("file.", "excel.", "script."))
        and not normalized.get("path")
        and normalized.get("filePath")
    ):
        normalized["path"] = normalized["filePath"]
    if node_type == "excel.addrow" and not normalized.get("rowData"):
        for alias in ("row", "rows", "content", "value"):
            if normalized.get(alias) is not None:
                normalized["rowData"] = normalized[alias]
                break
    if node_type == "file.write" and not normalized.get("content") and normalized.get("value") is not None:
        normalized["content"] = normalized["value"]

    _strip_templates_from_name_fields(normalized, node_type)

    # countVariable 与 outputVariable 互不影响，缺了只会挂 lint，直接派生。
    # 必须放在模板剥离之后，否则 `${var.rows}` 会派生出 `${var.rows}_count`。
    if node_type in _COUNTABLE_EXTRACT_TYPES and not normalized.get("countVariable"):
        output_variable = normalized.get("outputVariable")
        if isinstance(output_variable, str) and output_variable.strip():
            normalized["countVariable"] = f"{output_variable.strip()}_count"

    return normalized


# 变量名字段和条件表达式里的 `${var.x}` 只能指变量 x，在入口还原成裸变量名。
_TEMPLATE_REF_RE = re.compile(r"\$\{\s*var\.([A-Za-z_][A-Za-z0-9_]*)\s*\}")
_NAME_ONLY_FIELDS = (
    "variableName", "outputVariable", "countVariable", "firstValueVariable",
    "itemsVariable", "itemVariable", "indexVariable", "inputVariable",
    "listVariable", "errorVariable", "responseVariable", "resultVariable",
)
# 只有条件类节点的 inputValue 是表达式；browser.fill 的 inputValue 是要填的值，模板必须保留。
_CONDITION_NODE_TYPES = frozenset({"control.condition", "control.repeat_until", "control.while"})
# 与 lint 的 _ROW_SELECTOR_FIELD_BY_TYPE 同一批节点：会产出行集合、因而「行数」有意义。
_COUNTABLE_EXTRACT_TYPES = frozenset({
    "browser.extract", "ui.extract", "browser.paginateNext", "browser.clickLoadMore",
})


def _strip_templates_from_name_fields(node: dict[str, Any], node_type: str) -> None:
    fields = list(_NAME_ONLY_FIELDS)
    if node_type in _CONDITION_NODE_TYPES:
        fields += ["condition", "expression", "inputValue", "untilCondition"]
    for field in fields:
        value = node.get(field)
        if isinstance(value, str) and "${" in value:
            node[field] = _TEMPLATE_REF_RE.sub(r"\1", value)


def _normalize_generated_nodes(nodes: list[Any]) -> list[Any]:
    return [_normalize_generated_node(node, index) for index, node in enumerate(nodes)]


def _normalize_generated_edge(edge: Any, index: int) -> Any:
    """规整连线标签和 id，减少不同模型命名差异。"""
    if not isinstance(edge, dict):
        return edge
    normalized = dict(edge)
    label = normalized.get("label")
    if isinstance(label, str):
        label_map = {
            "true": "true", "false": "false",
            "body": "body", "exit": "exit",
            "yes": "true", "no": "false",
            "loop": "body", "each": "body", "iterate": "body",
            "done": "exit", "complete": "exit",
            "是": "true", "否": "false",
            "循环体": "body", "退出": "exit",
            "循环": "body", "每项": "body", "迭代": "body",
            "完成": "exit", "结束": "exit", "跳出": "exit",
        }
        normalized["label"] = label_map.get(label.strip().lower(), label.strip())
    if not normalized.get("id") and normalized.get("source") and normalized.get("target"):
        normalized["id"] = f"e_{uuid4()}"
    return normalized


def _normalize_generated_edges(edges: list[Any]) -> list[Any]:
    return [_normalize_generated_edge(edge, index) for index, edge in enumerate(edges)]


def _node_ref(node: Any) -> dict[str, str] | None:
    """返回面向用户的节点引用，避免助手只输出 n12 这类机器 ID。"""
    if not isinstance(node, dict):
        return None
    node_id = node.get("id")
    if not isinstance(node_id, str) or not node_id:
        return None
    title = str(node.get("title") or node.get("name") or node_id)
    node_type = str(node.get("type") or node.get("actionType") or node.get("kind") or "")
    label = f"{title}（{node_id} · {node_type}）" if node_type else f"{title}（{node_id}）"
    return {"id": node_id, "title": title, "type": node_type, "label": label}


def _default_node_title(node_type: str, node_id: str) -> str:
    if node_type == "start":
        return "开始"
    if node_type == "end":
        return "结束"
    title_map = {
        "browser.open": "打开网页",
        "browser.click": "点击元素",
        "browser.fill": "填写输入",
        "browser.wait": "等待元素",
        "browser.extract": "提取数据",
        "control.condition": "条件判断",
        "control.foreach": "循环遍历",
        "control.repeat_until": "重复直到",
        "variable.set": "设置变量",
        "file.write": "写入文件",
        "excel.addrow": "追加 Excel 行",
        "excel.save": "保存 Excel",
    }
    return title_map.get(node_type, node_id)


def _nodes_visually_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    NODE_W, NODE_H, GAP = 240, 84, 16
    left_pos = left.get("position") or {}
    right_pos = right.get("position") or {}
    left_x = _read_layout_number(left_pos.get("x"), 560)
    left_y = _read_layout_number(left_pos.get("y"), 0)
    right_x = _read_layout_number(right_pos.get("x"), 560)
    right_y = _read_layout_number(right_pos.get("y"), 0)
    return (
        left_x < right_x + NODE_W + GAP
        and left_x + NODE_W + GAP > right_x
        and left_y < right_y + NODE_H + GAP
        and left_y + NODE_H + GAP > right_y
    )

def _read_layout_number(value: Any, default: int) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return default

def _read_node_x(node: dict[str, Any]) -> int:
    position = node.get("position") or {}
    return _read_layout_number(position.get("x"), 560) if isinstance(position, dict) else 560

def _read_node_y(node: dict[str, Any]) -> int:
    position = node.get("position") or {}
    return _read_layout_number(position.get("y"), 0) if isinstance(position, dict) else 0

def _next_layout_lane(current_lane: int, label: Any) -> int:
    normalized = str(label or "").strip().lower()
    if normalized in {"true", "body", "是"}:
        return current_lane - 1
    if normalized in {"false", "exit", "否"}:
        return current_lane + 1
    return current_lane

def _choose_layout_lane(current_lane: int, incoming_lane: int) -> int:
    if current_lane == incoming_lane:
        return current_lane
    # When branches merge, return toward the main lane instead of keeping the
    # first branch lane forever. This keeps downstream work centered.
    if current_lane < 0 < incoming_lane or incoming_lane < 0 < current_lane:
        return 0
    return current_lane if abs(current_lane) <= abs(incoming_lane) else incoming_lane
