from __future__ import annotations

from app.services.ai_chat_store import AiChatStore


def _store(tmp_path) -> AiChatStore:
    return AiChatStore(app_data_dir=str(tmp_path))


def test_rename_moves_a_draft_session_to_the_saved_flow(tmp_path) -> None:
    store = _store(tmp_path)
    store.save("flow_local-1785294803539", [{"id": "m1", "role": "user", "content": "抓取这个帖子"}])

    assert store.rename("flow_local-1785294803539", "flow_db38cff1") is True

    assert [m["content"] for m in store.load("flow_db38cff1")] == ["抓取这个帖子"]
    assert store.load("flow_local-1785294803539") == []


def test_rename_keeps_the_existing_conversation_of_the_target_flow(tmp_path) -> None:
    """AI 自己建流程时对话已经写在真实 id 下，此时再搬草稿等于用草稿覆盖正文。"""
    store = _store(tmp_path)
    store.save("flow_local-1", [{"id": "draft", "role": "user", "content": "草稿"}])
    store.save("flow_real", [{"id": "kept", "role": "user", "content": "正文"}])

    assert store.rename("flow_local-1", "flow_real") is False

    assert [m["content"] for m in store.load("flow_real")] == ["正文"]
    assert [m["content"] for m in store.load("flow_local-1")] == ["草稿"]


def test_rename_without_a_source_is_a_no_op(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.rename("local", "flow_real") is False
    assert store.load("flow_real") == []
