import json

from app.services.ai_context_window import (
    _CONTEXT_SYNTHETIC_KEY,
    _compact_tool_messages,
    _elide_repeated_result,
)


def _assistant_call(*call_ids: str, arguments: str = "{}") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": "inspect_page", "arguments": arguments}}
            for call_id in call_ids
        ],
    }


def _tool(call_id: str, content: str = "ok") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _assert_tool_calls_paired(messages: list[dict]) -> None:
    pending: set[str] = set()
    for message in messages:
        if message["role"] == "assistant":
            pending.update(call["id"] for call in message.get("tool_calls") or [])
        elif message["role"] == "tool":
            assert message["tool_call_id"] in pending
            pending.remove(message["tool_call_id"])
    assert not pending


def test_screenshot_inside_multi_tool_batch_is_not_a_turn_boundary() -> None:
    screenshot = {
        "role": "user",
        _CONTEXT_SYNTHETIC_KEY: True,
        "content": [
            {"type": "text", "text": "[inspect_screenshot 页面截图]"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "z" * 3_000}},
        ],
    }
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "早期目标" + "x" * 2_000},
        _assistant_call("a", "b"),
        _tool("a"),
        screenshot,
        _tool("b"),
        {"role": "user", "content": "最新真实目标"},
    ]

    _compact_tool_messages(messages, budget=500, protect_prefix=1)

    assert messages[-1]["content"] == "最新真实目标"
    assert not any(message.get("tool_call_id") == "b" for message in messages)
    assert not any(message.get("tool_calls") for message in messages)
    _assert_tool_calls_paired(messages)


def test_pending_tool_call_blocks_a_user_cut_even_without_metadata() -> None:
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "早期目标" + "x" * 2_000},
        _assistant_call("a", "b"),
        _tool("a"),
        {"role": "user", "content": "[截图]"},
        _tool("b"),
        {"role": "user", "content": "最新真实目标"},
    ]

    _compact_tool_messages(messages, budget=500, protect_prefix=1)

    assert messages[-1]["content"] == "最新真实目标"
    _assert_tool_calls_paired(messages)


def test_screenshot_after_batch_and_a_normal_tool_turn_are_dropped_together() -> None:
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "早期目标" + "x" * 2_000},
        _assistant_call("a"),
        _tool("a"),
        {"role": "user", _CONTEXT_SYNTHETIC_KEY: True, "content": "[历史截图已移除]"},
        {"role": "user", "content": "最新真实目标"},
        _assistant_call("b"),
        _tool("b"),
    ]

    _compact_tool_messages(messages, budget=500, protect_prefix=1)

    assert [call["id"] for message in messages for call in message.get("tool_calls") or []] == ["b"]
    assert [message["tool_call_id"] for message in messages if message["role"] == "tool"] == ["b"]
    _assert_tool_calls_paired(messages)


def test_long_tool_arguments_count_toward_budget() -> None:
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "早期目标"},
        _assistant_call("a", arguments=json.dumps({"selector": "x" * 3_000})),
        _tool("a"),
        {"role": "user", "content": "最新真实目标"},
    ]

    _compact_tool_messages(messages, budget=500, protect_prefix=1)

    assert messages[-1]["content"] == "最新真实目标"
    assert not any(message.get("tool_calls") for message in messages)
    _assert_tool_calls_paired(messages)


def test_repeated_result_is_resent_when_original_is_compacted_or_dropped() -> None:
    result = {"html": "x" * 4_000}
    payload = json.dumps(result, ensure_ascii=False)
    seen: dict[tuple[str, str], str] = {}
    messages = [{"role": "user", "content": "目标"}]
    args = '{"url":"https://example.test"}'

    assert _elide_repeated_result("inspect_page", args, result, seen, messages) == payload
    messages.append(_tool("a", payload))
    assert json.loads(_elide_repeated_result("inspect_page", args, result, seen, messages))["_unchanged"]

    messages.extend([_tool("b"), _tool("c")])
    _compact_tool_messages(messages, budget=500, protect_prefix=0)
    assert messages[1]["content"] != payload
    assert _elide_repeated_result("inspect_page", args, result, seen, messages) == payload

    messages.clear()
    assert _elide_repeated_result("inspect_page", args, result, seen, messages) == payload
