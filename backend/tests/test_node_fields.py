"""守住 KNOWN_NODE_FIELDS 不落后于执行层。

unread_node_field 的判据是「平台没人读这个键」，名单少收一个字段就会把合法流程判成错。
所以名单不能靠手写维护：这里重新扫描后端执行层与前端 flowDefinition.ts，扫出来的字段
必须都在名单里。加了新节点字段而没同步名单，这条测试会先红，而不是等生产里冒误判。
"""
from __future__ import annotations

import json
import pathlib
import re

from app.services.ai_tools.catalog import NODE_TYPE_CATALOG
from app.services.ai_tools.node_fields import FIELD_ALIAS_HINTS, KNOWN_NODE_FIELDS

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
_FRONTEND_FLOW_DEFINITION = pathlib.Path(__file__).resolve().parents[2] / "src" / "lib" / "flowDefinition.ts"

_DIRECT_READ_PATTERNS = (
    re.compile(r'node\.get\(\s*["\']([A-Za-z_][A-Za-z0-9_]*)["\']'),
    re.compile(r'node\[\s*["\']([A-Za-z_][A-Za-z0-9_]*)["\']\s*\]'),
    re.compile(
        r'_read_(?:int|bool|non_negative_int|required_string|optional_string'
        r'|selector_config|action_type)\(\s*node\s*,\s*["\']([A-Za-z_][A-Za-z0-9_]*)["\']'
    ),
)
_FALLBACK_KEYS_PATTERN = re.compile(r"fallback_keys\s*=\s*\(([^)]*)\)")
# for key in ("a", "b"): ... node.get(key) —— 动态键循环，直接正则读不到键名
_DYNAMIC_KEY_LOOP_PATTERN = re.compile(r"for\s+([A-Za-z_]\w*)\s+in\s+\(([^)]*?)\)\s*:", re.S)
_QUOTED_NAME_PATTERN = re.compile(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']')


def _scan_backend_node_fields() -> set[str]:
    found: set[str] = set()
    for path in sorted(_BACKEND_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in _DIRECT_READ_PATTERNS:
            found.update(match.group(1) for match in pattern.finditer(text))
        for match in _FALLBACK_KEYS_PATTERN.finditer(text):
            found.update(name.group(1) for name in _QUOTED_NAME_PATTERN.finditer(match.group(1)))
        for match in _DYNAMIC_KEY_LOOP_PATTERN.finditer(text):
            loop_var, tuple_body = match.group(1), match.group(2)
            tail = text[match.end():match.end() + 400]
            if f"node.get({loop_var}" in tail or f"node[{loop_var}]" in tail:
                found.update(name.group(1) for name in _QUOTED_NAME_PATTERN.finditer(tuple_body))
    return found


def _scan_frontend_node_fields() -> set[str]:
    text = _FRONTEND_FLOW_DEFINITION.read_text(encoding="utf-8")
    return set(re.findall(r"node\.([A-Za-z_][A-Za-z0-9_]*)", text))


_CATALOG_PAREN_PATTERN = re.compile(r"[（(][^）)]*[）)]")
# key_fields 是给模型看的散文：逗号/顿号/斜杠/「或」都当分隔符，括号里是说明不是字段名
_CATALOG_SPLIT_PATTERN = re.compile(r"[,，、/／+或]")
_CATALOG_FIELD_PATTERN = re.compile(r"[a-z][A-Za-z0-9]*")


def _catalog_key_fields(key_fields: str) -> set[str]:
    found: set[str] = set()
    for token in _CATALOG_SPLIT_PATTERN.split(_CATALOG_PAREN_PATTERN.sub("", key_fields)):
        token = token.strip()
        if _CATALOG_FIELD_PATTERN.fullmatch(token):
            found.add(token)
    return found


def test_catalog_key_fields_are_fields_the_platform_actually_reads() -> None:
    """catalog 是模型唯一的字段说明书，写进 key_fields 的字段模型就会写进节点。

    平台不读的字段不报错，只静默忽略：excel.filter 少了 column 会原样返回全部行并报成功，
    用户拿到的是错数据而不是错误。
    """
    unread: dict[str, list[str]] = {}
    for entry in NODE_TYPE_CATALOG:
        missing = sorted(_catalog_key_fields(str(entry["key_fields"])) - KNOWN_NODE_FIELDS)
        if missing:
            unread[str(entry["type"])] = missing
    assert not unread, f"catalog 让模型写了这些平台不读的字段：{unread}"


def test_known_node_fields_covers_every_field_the_backend_reads() -> None:
    missing = sorted(_scan_backend_node_fields() - KNOWN_NODE_FIELDS)
    assert not missing, (
        "后端执行层读了这些字段但 KNOWN_NODE_FIELDS 没收，会把合法流程判成 unread_node_field："
        f"{missing}"
    )


def test_known_node_fields_covers_every_field_the_canvas_reads() -> None:
    missing = sorted(_scan_frontend_node_fields() - KNOWN_NODE_FIELDS)
    assert not missing, (
        "前端 flowDefinition.ts 读了这些字段但 KNOWN_NODE_FIELDS 没收："
        f"{missing}"
    )


def test_alias_hints_point_at_fields_that_are_actually_read() -> None:
    """别名建议必须指向真实字段，否则「改成 X」会把模型引到第二个读不到的键上。"""
    broken = sorted(
        f"{wrong}->{suggested}"
        for wrong, suggested in FIELD_ALIAS_HINTS.items()
        if suggested not in KNOWN_NODE_FIELDS
    )
    assert not broken, f"这些别名建议指向了平台不读的字段：{broken}"


def test_alias_hints_are_not_themselves_valid_fields() -> None:
    """别名键本身必须是无人读取的写法，否则会对合法字段给出「改成别的」的建议。"""
    conflicting = sorted(key for key in FIELD_ALIAS_HINTS if key in KNOWN_NODE_FIELDS)
    assert not conflicting, f"这些键既在别名表又是合法字段：{conflicting}"
