import asyncio
from contextlib import aclosing

import pytest
from langgraph.graph import END, START, StateGraph

from app.services.ai_graph_runtime import GraphDelivery, OrchestrationState


async def test_closing_after_delivery_cancels_graph_before_the_next_side_effect():
    delivery = GraphDelivery()
    actions = []
    closed = asyncio.Event()

    async def node(state):
        try:
            actions.append("first")
            await delivery.emit({"type": "tool_result"})
            actions.append("second")
        finally:
            closed.set()
        return {"route": END}

    graph = StateGraph(OrchestrationState).add_node("model", node).add_edge(START, "model").add_edge("model", END).compile()
    stream = delivery.stream(graph, 2)
    assert await anext(stream) == {"type": "tool_result"}
    # Leave the worker runnable long enough to expose missing backpressure.
    await asyncio.sleep(0)
    assert actions == ["first"]
    await stream.aclose()
    assert closed.is_set()
    assert actions == ["first"]


async def test_consuming_all_events_releases_the_graph_and_preserves_order():
    delivery = GraphDelivery()

    async def node(state):
        for index in range(3):
            await delivery.emit({"index": index})
        return {"route": END}

    graph = StateGraph(OrchestrationState).add_node("model", node).add_edge(START, "model").add_edge("model", END).compile()
    async with aclosing(delivery.stream(graph, 2)) as events:
        assert [event async for event in events] == [{"index": 0}, {"index": 1}, {"index": 2}]


async def test_graph_failure_reaches_the_consumer_without_hanging():
    delivery = GraphDelivery()

    async def node(state):
        raise ValueError("graph failed")

    graph = StateGraph(OrchestrationState).add_node("model", node).add_edge(START, "model").add_edge("model", END).compile()
    async with aclosing(delivery.stream(graph, 2)) as events:
        with pytest.raises(ValueError, match="graph failed"):
            await asyncio.wait_for(anext(events), timeout=2)


async def test_cancelling_while_waiting_for_an_event_releases_the_node():
    delivery = GraphDelivery()
    entered, closed = asyncio.Event(), asyncio.Event()

    async def node(state):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    graph = StateGraph(OrchestrationState).add_node("model", node).add_edge(START, "model").add_edge("model", END).compile()
    stream = delivery.stream(graph, 2)
    receiving = asyncio.create_task(anext(stream))
    await asyncio.wait_for(entered.wait(), timeout=2)
    receiving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await receiving
    assert closed.is_set()


@pytest.mark.parametrize("mode", ["empty", "vision", "tool"])
async def test_round_limit_applies_to_model_retries_and_tool_cycles(monkeypatch, mode):
    import litellm
    from app.services import ai_orchestrator
    from app.services.ai_model_transport import ModelTransportError
    from test_ai_orchestrator import _FakeExecutor, _FakeStream, _chunk, _tool_call_chunk

    monkeypatch.setattr(ai_orchestrator, "_STANDARD_TOOL_ROUNDS", 1)
    attempts = []

    async def complete(**kwargs):
        attempts.append(kwargs)
        if mode == "vision":
            raise ModelTransportError("unsupported vision", vision_unsupported=True)
        if mode == "tool":
            return _FakeStream([_chunk(tool_calls=[_tool_call_chunk(
                0, call_id="c", name="list_node_types", arguments='{"types":["browser.open"]}',
            )], finish="tool_calls")])
        return _FakeStream([])

    monkeypatch.setattr(litellm, "acompletion", complete)
    messages = [{"role": "user", "content": [{"type": "text", "text": "只检查"}, {
        "type": "image_url", "image_url": {"url": "data:image/png;base64,synthetic"},
    }]}]
    events = [event async for event in ai_orchestrator.AiOrchestrator(_FakeExecutor()).stream(messages, "test-model")]
    assert len(attempts) == 1
    assert events[-1] == {"type": "done"}
    assert any("仍未收敛" in event.get("delta", "") for event in events)
