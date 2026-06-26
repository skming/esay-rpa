from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.models.schemas import ScrapeResult
from app.services.runtime_variables import RuntimeVariableStore, stringify_variable_value

type FlowNode = dict[str, object]

_DATA_ACTION_TYPES = {
    "data.json.parse",
    "data.string.transform",
    "data.regex.match",
    "data.list.map",
    "data.math.compute",
    "data.step",
    "data.convert",
    "data.encrypt",
}
_MAX_INPUT_CHARS = 512_000
_MAX_REGEX_LENGTH = 500
_MAX_LIST_ITEMS = 10_000
_MATH_OPERATORS = {"add", "subtract", "multiply", "divide", "mod"}


@dataclass(frozen=True)
class DataActionResult:
    action_type: str
    value: object
    values: list[str]
    count: int

    def to_scrape_result(self) -> ScrapeResult:
        return ScrapeResult(
            url="",
            selector=self.action_type,
            count=self.count,
            values=self.values,
        )


class DataActionRunner:
    async def run(self, node: FlowNode, variables: RuntimeVariableStore, *, timeout_ms: int) -> DataActionResult:
        del timeout_ms
        action_type = _read_action_type(node)
        if action_type == "data.step":
            action_type = "data.json.parse"

        if action_type == "data.json.parse":
            return _run_json_parse(node, variables)
        if action_type == "data.string.transform":
            return _run_string_transform(node, variables)
        if action_type == "data.regex.match":
            return _run_regex_match(node, variables)
        if action_type == "data.list.map":
            return _run_list_map(node, variables)
        if action_type == "data.math.compute":
            return _run_math_compute(node, variables)
        if action_type == "data.convert":
            return _run_convert(node, variables)
        if action_type == "data.encrypt":
            return _run_encrypt(node, variables)
        raise ValueError(f"不支持的数据处理节点类型: {action_type}")


def is_data_action_node(node: FlowNode) -> bool:
    return node.get("type") in _DATA_ACTION_TYPES


def apply_data_result_variables(node: FlowNode, result: DataActionResult, variables: RuntimeVariableStore) -> list[str]:
    saved_names: list[str] = []
    output_variable = _read_optional_string(node, "outputVariable") or _read_optional_string(node, "responseVariable") or _read_optional_string(node, "resultVariable")
    if output_variable is not None:
        variables.set(output_variable, result.value, scope="局部")
        saved_names.append(output_variable)

    count_variable = _read_optional_string(node, "countVariable") or _read_optional_string(node, "statusVariable")
    if count_variable is not None:
        variables.set(count_variable, result.count, scope="局部")
        saved_names.append(count_variable)

    first_value_variable = _read_optional_string(node, "firstValueVariable")
    if first_value_variable is not None:
        variables.set(first_value_variable, result.values[0] if result.values else "", scope="局部")
        saved_names.append(first_value_variable)
    return saved_names


def _run_json_parse(node: FlowNode, variables: RuntimeVariableStore) -> DataActionResult:
    text = _read_input_text(node, variables)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("JSON 解析失败") from exc
    values = _result_values(value)
    return DataActionResult(action_type="data.json.parse", value=value, values=values, count=len(values))


def _run_string_transform(node: FlowNode, variables: RuntimeVariableStore) -> DataActionResult:
    text = _read_input_text(node, variables)
    operation = (_read_optional_string(node, "operation") or "trim").lower()
    if operation == "trim":
        value = text.strip()
    elif operation == "lower":
        value = text.lower()
    elif operation == "upper":
        value = text.upper()
    elif operation == "replace":
        search = _read_required_string(node, "search")
        replacement = _read_optional_string(node, "replacement") or ""
        value = text.replace(variables.resolve_text(search), variables.resolve_text(replacement))
    elif operation == "split":
        delimiter = variables.resolve_text(_read_optional_string(node, "delimiter") or ",")
        value = text.split(delimiter)
    else:
        raise ValueError(f"不支持的字符串处理操作: {operation}")
    values = _result_values(value)
    return DataActionResult(action_type="data.string.transform", value=value, values=values, count=len(values))


def _run_regex_match(node: FlowNode, variables: RuntimeVariableStore) -> DataActionResult:
    text = _read_input_text(node, variables)
    pattern = variables.resolve_text(_read_required_string(node, "pattern"))
    if len(pattern) > _MAX_REGEX_LENGTH:
        raise ValueError("正则表达式不能超过 500 个字符")
    matches = [match.group(1) if match.groups() else match.group(0) for match in re.finditer(pattern, text)]
    if len(matches) > _MAX_LIST_ITEMS:
        raise ValueError("正则匹配结果超过 10000 条")
    return DataActionResult(action_type="data.regex.match", value=matches, values=matches, count=len(matches))


def _run_list_map(node: FlowNode, variables: RuntimeVariableStore) -> DataActionResult:
    items = _read_input_list(node, variables)
    operation = (_read_optional_string(node, "operation") or "compact").lower()
    if operation == "compact":
        value = [item for item in items if stringify_variable_value(item).strip()]
    elif operation == "unique":
        seen: set[str] = set()
        value = []
        for item in items:
            key = stringify_variable_value(item)
            if key not in seen:
                seen.add(key)
                value.append(item)
    elif operation == "join":
        delimiter = variables.resolve_text(_read_optional_string(node, "delimiter") or ",")
        value = delimiter.join(stringify_variable_value(item) for item in items)
    else:
        raise ValueError(f"不支持的列表处理操作: {operation}")
    values = _result_values(value)
    return DataActionResult(action_type="data.list.map", value=value, values=values, count=len(values))


def _run_math_compute(node: FlowNode, variables: RuntimeVariableStore) -> DataActionResult:
    operator = (_read_optional_string(node, "operator") or "add").lower()
    if operator not in _MATH_OPERATORS:
        raise ValueError(f"不支持的数字运算操作: {operator}")
    left = _read_decimal(node, variables, "left")
    right = _read_decimal(node, variables, "right")
    if operator == "add":
        value = left + right
    elif operator == "subtract":
        value = left - right
    elif operator == "multiply":
        value = left * right
    elif operator == "divide":
        if right == 0:
            raise ValueError("数字运算不能除以 0")
        value = left / right
    else:
        if right == 0:
            raise ValueError("数字运算不能对 0 取模")
        value = left % right
    normalized = _normalize_decimal(value)
    return DataActionResult(action_type="data.math.compute", value=normalized, values=[stringify_variable_value(normalized)], count=1)


def _run_convert(node: FlowNode, variables: RuntimeVariableStore) -> DataActionResult:
    text = _read_input_text(node, variables)
    operation = (_read_optional_string(node, "operation") or "to_str").lower()
    value: object
    if operation == "to_int":
        try:
            value = int(float(text.strip()))
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"无法将输入转换为整数") from exc
    elif operation == "to_float":
        try:
            value = float(text.strip())
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"无法将输入转换为浮点数") from exc
    elif operation == "to_bool":
        lower = text.strip().lower()
        if lower in {"true", "1", "yes", "on", "是"}:
            value = True
        elif lower in {"false", "0", "no", "off", "否", ""}:
            value = False
        else:
            raise ValueError(f"无法将 '{text[:40]}' 转换为布尔值")
    elif operation == "to_str":
        value = text
    elif operation == "to_list":
        try:
            decoded = json.loads(text)
            value = decoded if isinstance(decoded, list) else [decoded]
        except json.JSONDecodeError:
            value = [item.strip() for item in text.split(",")]
    elif operation == "to_json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("JSON 解析失败") from exc
    else:
        raise ValueError(f"不支持的类型转换操作: {operation}")
    values = _result_values(value)
    return DataActionResult(action_type="data.convert", value=value, values=values, count=len(values))


def _run_encrypt(node: FlowNode, variables: RuntimeVariableStore) -> DataActionResult:
    text = _read_input_text(node, variables)
    operation = (_read_optional_string(node, "operation") or "md5").lower()
    raw = text.encode("utf-8")
    if operation == "md5":
        value: object = hashlib.md5(raw).hexdigest()
    elif operation == "sha256":
        value = hashlib.sha256(raw).hexdigest()
    elif operation == "sha1":
        value = hashlib.sha1(raw).hexdigest()
    elif operation == "base64_encode":
        value = base64.b64encode(raw).decode("ascii")
    elif operation == "base64_decode":
        try:
            value = base64.b64decode(text.strip().encode("ascii")).decode("utf-8", errors="replace")
        except Exception as exc:
            raise ValueError("Base64 解码失败") from exc
    elif operation in {"aes_encrypt", "aes_decrypt"}:
        value = _run_aes(text, operation, node, variables)
    else:
        raise ValueError(f"不支持的加密操作: {operation}")
    values = [stringify_variable_value(value)]
    return DataActionResult(action_type="data.encrypt", value=value, values=values, count=1)


def _run_aes(text: str, operation: str, node: FlowNode, variables: RuntimeVariableStore) -> str:
    import os as _os
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding as _padding
        from cryptography.hazmat.backends import default_backend
    except ImportError as exc:
        raise RuntimeError("AES 加密需要安装 cryptography 库: pip install cryptography") from exc

    pattern = _read_optional_string(node, "pattern") or ""
    key_text = variables.resolve_text(pattern) if pattern else "rpa-studio-aes-key"
    key = hashlib.sha256(key_text.encode("utf-8")).digest()

    if operation == "aes_encrypt":
        iv = _os.urandom(16)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        padder = _padding.PKCS7(128).padder()
        padded = padder.update(text.encode("utf-8")) + padder.finalize()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        return base64.b64encode(iv + ciphertext).decode("ascii")
    else:
        try:
            raw = base64.b64decode(text.strip().encode("ascii"))
        except Exception as exc:
            raise ValueError("AES 解密失败：无效的 Base64 输入") from exc
        if len(raw) < 32:
            raise ValueError("AES 解密失败：密文过短")
        iv, ciphertext = raw[:16], raw[16:]
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        padded_plain = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = _padding.PKCS7(128).unpadder()
        return (unpadder.update(padded_plain) + unpadder.finalize()).decode("utf-8")


def _read_input_text(node: FlowNode, variables: RuntimeVariableStore) -> str:
    if isinstance(node.get("inputVariable"), str) and node["inputVariable"].strip():
        value = variables.get(node["inputVariable"])
    else:
        value = node.get("inputValue", node.get("value", ""))
    text = variables.resolve_text(stringify_variable_value(value))
    if len(text) > _MAX_INPUT_CHARS:
        raise ValueError("数据处理输入不能超过 512KB")
    return text


def _read_input_list(node: FlowNode, variables: RuntimeVariableStore) -> list[object]:
    if isinstance(node.get("inputVariable"), str) and node["inputVariable"].strip():
        value = variables.get(node["inputVariable"])
    else:
        value = node.get("items", node.get("inputValue", []))
    if isinstance(value, str):
        rendered = variables.resolve_text(value)
        try:
            decoded = json.loads(rendered)
        except json.JSONDecodeError as exc:
            raise ValueError("列表处理输入必须是列表或 JSON 数组") from exc
        value = decoded
    if not isinstance(value, list):
        raise ValueError("列表处理输入必须是列表")
    if len(value) > _MAX_LIST_ITEMS:
        raise ValueError("列表处理输入不能超过 10000 项")
    return value


def _read_decimal(node: FlowNode, variables: RuntimeVariableStore, key: Literal["left", "right"]) -> Decimal:
    value = node.get(key)
    if value is None:
        variable_key = f"{key}Variable"
        raw_variable = _read_optional_string(node, variable_key)
        if raw_variable is None:
            raise ValueError(f"数字运算缺少 {key}")
        value = variables.get(raw_variable)
    rendered = variables.resolve_text(stringify_variable_value(value))
    try:
        return Decimal(rendered)
    except InvalidOperation as exc:
        raise ValueError(f"数字运算参数不是有效数字: {key}") from exc


def _result_values(value: object) -> list[str]:
    if isinstance(value, list):
        return [stringify_variable_value(item) for item in value]
    if isinstance(value, dict):
        return [stringify_variable_value(value)]
    return [stringify_variable_value(value)]


def _normalize_decimal(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value.normalize())


def _read_action_type(node: FlowNode) -> str:
    value = node.get("type")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("数据处理节点缺少 type")
    return value.strip()


def _read_required_string(node: FlowNode, key: str) -> str:
    value = _read_optional_string(node, key)
    if value is None:
        raise ValueError(f"数据处理节点缺少 {key}")
    return value


def _read_optional_string(node: FlowNode, key: str) -> str | None:
    value = node.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
