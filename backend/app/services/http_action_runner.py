from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

from pydantic import HttpUrl, TypeAdapter, ValidationError

from app.models.schemas import ScrapeResult
from app.services.runtime_variables import RuntimeVariableStore, stringify_variable_value

type FlowNode = dict[str, object]

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]

_HTTP_NODE_TYPES = {"http.request", "script.http", "api.request"}
_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_FORBIDDEN_HEADER_PREFIXES = ("proxy-", "sec-")
_FORBIDDEN_HEADERS = {"authorization", "cookie", "host", "proxy-authorization"}
_MAX_BODY_BYTES = 256_000
_MAX_RESPONSE_BYTES = 512_000


@dataclass(frozen=True)
class HttpActionResult:
    url: str
    method: HttpMethod
    status_code: int
    body: str
    headers: dict[str, str]

    def to_scrape_result(self) -> ScrapeResult:
        return ScrapeResult(
            url=self.url,
            selector=f"{self.method} {self.status_code}",
            count=1,
            values=[self.body],
        )


class HttpActionRunner:
    async def run(self, node: FlowNode, variables: RuntimeVariableStore, *, timeout_ms: int) -> HttpActionResult:
        request = _build_http_request(node, variables)
        timeout_seconds = max(1, timeout_ms) / 1000
        return await asyncio.wait_for(asyncio.to_thread(_send_http_request, request, timeout_seconds), timeout=timeout_seconds + 1)


@dataclass(frozen=True)
class _HttpRequest:
    url: str
    method: HttpMethod
    headers: dict[str, str]
    body: bytes | None


def is_http_action_node(node: FlowNode) -> bool:
    return node.get("type") in _HTTP_NODE_TYPES


def apply_http_result_variables(node: FlowNode, result: HttpActionResult, variables: RuntimeVariableStore) -> list[str]:
    saved_names: list[str] = []

    body_variable = _read_optional_string(node, "outputVariable") or _read_optional_string(node, "responseVariable")
    if body_variable is not None:
        variables.set(body_variable, result.body, scope="局部")
        saved_names.append(body_variable)

    status_variable = _read_optional_string(node, "statusVariable") or _read_optional_string(node, "statusCodeVariable")
    if status_variable is not None:
        variables.set(status_variable, result.status_code, scope="局部")
        saved_names.append(status_variable)

    json_variable = _read_optional_string(node, "jsonVariable")
    if json_variable is not None:
        variables.set(json_variable, _parse_json_or_text(result.body), scope="局部")
        saved_names.append(json_variable)

    return saved_names


def _build_http_request(node: FlowNode, variables: RuntimeVariableStore) -> _HttpRequest:
    raw_url = _read_required_string(node, "url", fallback_keys=("targetUrl", "endpoint"))
    url = _validate_url(variables.resolve_text(raw_url))
    method = _read_method(node)
    headers = _read_headers(node, variables)
    body = _read_body(node, variables)
    if method == "GET" and body is not None:
        raise ValueError("HTTP GET 节点不能配置 requestBody/body")
    return _HttpRequest(url=url, method=method, headers=headers, body=body)


def _send_http_request(request: _HttpRequest, timeout_seconds: float) -> HttpActionResult:
    urllib_request = urllib.request.Request(
        request.url,
        data=request.body,
        headers=request.headers,
        method=request.method,
    )
    try:
        with urllib.request.urlopen(urllib_request, timeout=timeout_seconds) as response:
            body = _read_response_body(response)
            return HttpActionResult(
                url=response.geturl(),
                method=request.method,
                status_code=response.status,
                body=body,
                headers={key: value for key, value in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        body = _read_response_body(exc)
        return HttpActionResult(
            url=exc.geturl(),
            method=request.method,
            status_code=exc.code,
            body=body,
            headers={key: value for key, value in exc.headers.items()},
        )
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HTTP 请求失败: {exc.reason}") from exc


def _read_response_body(response: object) -> str:
    read = getattr(response, "read", None)
    if not callable(read):
        return ""
    payload = read(_MAX_RESPONSE_BYTES + 1)
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ValueError("HTTP 响应超过 512KB 限制")
    headers = getattr(response, "headers", {})
    charset = headers.get_content_charset() if hasattr(headers, "get_content_charset") else None
    return payload.decode(charset or "utf-8", errors="replace")


def _read_method(node: FlowNode) -> HttpMethod:
    value = _read_optional_string(node, "method") or "GET"
    method = value.upper()
    if method not in _ALLOWED_METHODS:
        raise ValueError(f"不支持的 HTTP 方法: {value}")
    return method  # type: ignore[return-value]


def _read_headers(node: FlowNode, variables: RuntimeVariableStore) -> dict[str, str]:
    raw_headers = node.get("headers")
    if raw_headers is None:
        return {}
    if isinstance(raw_headers, str):
        rendered = variables.resolve_text(raw_headers)
        try:
            decoded = json.loads(rendered)
        except json.JSONDecodeError as exc:
            raise ValueError("headers 必须是 JSON 对象") from exc
        raw_headers = decoded
    if not isinstance(raw_headers, dict):
        raise ValueError("headers 必须是对象")

    headers: dict[str, str] = {}
    for key, value in raw_headers.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("HTTP Header 名称不能为空")
        normalized_key = key.strip()
        lower_key = normalized_key.lower()
        if lower_key in _FORBIDDEN_HEADERS or any(lower_key.startswith(prefix) for prefix in _FORBIDDEN_HEADER_PREFIXES):
            raise ValueError(f"不允许由流程节点设置敏感 Header: {normalized_key}")
        headers[normalized_key] = variables.resolve_text(stringify_variable_value(value))
    return headers


def _read_body(node: FlowNode, variables: RuntimeVariableStore) -> bytes | None:
    raw_body = node.get("requestBody", node.get("body"))
    if raw_body is None:
        return None
    body_text = variables.resolve_text(stringify_variable_value(raw_body))
    body_bytes = body_text.encode("utf-8")
    if len(body_bytes) > _MAX_BODY_BYTES:
        raise ValueError("HTTP 请求体不能超过 256KB")
    return body_bytes


def _validate_url(value: str) -> str:
    try:
        url = str(_HTTP_URL_ADAPTER.validate_python(value))
    except ValidationError as exc:
        raise ValueError(f"HTTP 节点 URL 不合法: {value}") from exc
    return url


def _parse_json_or_text(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _read_required_string(node: FlowNode, key: str, *, fallback_keys: tuple[str, ...] = ()) -> str:
    for candidate_key in (key, *fallback_keys):
        value = node.get(candidate_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"HTTP 节点缺少 {key}")


def _read_optional_string(node: FlowNode, key: str) -> str | None:
    value = node.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
