from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import ai_model_transport as transport
from test_ai_orchestrator import _FakeStream, _chunk, _tool_call_chunk


@pytest.mark.parametrize("tools", [[], [{"type": "function", "function": {"name": "fixture"}}]])
async def test_transport_builds_request_and_normalizes_stream(monkeypatch, tools):
    litellm = transport.ModelTransport.ensure_available()
    response = _FakeStream([
        _chunk(content="<thi"), _chunk(content="nk>reason</think>reply"),
        _chunk(tool_calls=[_tool_call_chunk(0, name="fixture", arguments='{"value":1}')]),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7)),
    ])
    response.aclose = AsyncMock()
    completion = AsyncMock(return_value=response)
    monkeypatch.setattr(litellm, "acompletion", completion)
    output = transport.RoundOutput()
    messages = [{"role": "user", "content": "fixture"}]
    events = [event async for event in transport.ModelTransport().stream_round(
        "fixture-model", messages, tools, output, round_num=4,
    )]
    request = completion.call_args.kwargs
    assert request["model"] == "fixture-model"
    assert request["messages"] == messages
    assert request["stream_options"] == {"include_usage": True}
    assert request["timeout"] == transport.LLM_REQUEST_TIMEOUT
    if tools:
        assert request["tools"] == tools and request["tool_choice"] == "auto"
    else:
        assert "tools" not in request and "tool_choice" not in request
    assert output.text == ["reply"] and output.thinking == ["reason"]
    assert output.tool_calls[0]["id"] == "call_r4_0"
    assert output.tool_calls[0]["arguments"] == '{"value":1}'
    assert output.usage.prompt_tokens == 12 and output.usage.completion_tokens == 7
    assert [event["type"] for event in events] == ["thinking", "text", "tool_start"]
    response.aclose.assert_awaited_once()


@pytest.mark.parametrize("first_chunk", [False, True])
async def test_transport_watchdog_cancels_pending_read_and_closes_response(monkeypatch, first_chunk):
    litellm = transport.ModelTransport.ensure_available()
    cancelled = asyncio.Event()

    async def chunks():
        if first_chunk:
            yield _chunk(content="partial")
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    response = chunks()
    monkeypatch.setattr(litellm, "acompletion", AsyncMock(return_value=response))
    monkeypatch.setattr(transport, "FIRST_TOKEN_TIMEOUT", 0.01)
    monkeypatch.setattr(transport, "STALL_TIMEOUT", 0.01)
    output = transport.RoundOutput()
    events = [event async for event in transport.ModelTransport().stream_round(
        "fixture-model", [], [], output, round_num=0,
    )]
    assert cancelled.is_set()
    assert output.stall_error and ("中途" in output.stall_error) == first_chunk
    assert bool(events) == first_chunk
    assert response.ag_frame is None


async def test_transport_missing_key_stops_before_request(monkeypatch):
    from app.services import ai_config_service

    litellm = transport.ModelTransport.ensure_available()
    monkeypatch.setattr(ai_config_service, "AI_MODEL_CATALOG", [{"id": "fixture-model", "env_key": "FIXTURE_AI_KEY"}])
    monkeypatch.delenv("FIXTURE_AI_KEY", raising=False)
    completion = AsyncMock()
    monkeypatch.setattr(litellm, "acompletion", completion)
    config = SimpleNamespace(get_base_url_for_model=lambda _: None, get_api_key_for_model=lambda _: None)
    with pytest.raises(transport.ModelTransportError, match="FIXTURE_AI_KEY"):
        async for _ in transport.ModelTransport(config).stream_round("fixture-model", [], [], transport.RoundOutput(), round_num=0):
            pytest.fail("missing credentials must not emit model output")
    completion.assert_not_awaited()
