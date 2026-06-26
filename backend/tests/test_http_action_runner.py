from __future__ import annotations

from app.services.http_action_runner import HttpActionRunner
from app.services.runtime_variables import RuntimeVariableStore


async def test_http_action_runner_rejects_forbidden_header() -> None:
    runner = HttpActionRunner()
    variables = RuntimeVariableStore.from_initial({})

    try:
        await runner.run(
            {
                "type": "http.request",
                "url": "https://example.com/",
                "headers": {"authorization": "Bearer token"},
            },
            variables,
            timeout_ms=1000,
        )
    except ValueError as exc:
        assert "敏感 Header" in str(exc)
    else:
        raise AssertionError("敏感 Header 必须被拒绝")


async def test_http_action_runner_rejects_get_body() -> None:
    runner = HttpActionRunner()
    variables = RuntimeVariableStore.from_initial({})

    try:
        await runner.run(
            {
                "type": "http.request",
                "method": "GET",
                "url": "https://example.com/",
                "requestBody": "{}",
            },
            variables,
            timeout_ms=1000,
        )
    except ValueError as exc:
        assert "GET" in str(exc)
    else:
        raise AssertionError("GET 请求不能携带请求体")
