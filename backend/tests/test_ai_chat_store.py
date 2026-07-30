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


def test_rename_merges_with_an_existing_target_conversation(tmp_path) -> None:
    """流式保存和会话搬迁可能竞态，目标先落盘时也不能把草稿历史留在旧文件。"""
    store = _store(tmp_path)
    store.save("flow_local-1", [{
        "id": "draft", "role": "user", "content": "草稿", "createdAt": 1,
    }])
    store.save("flow_real", [{
        "id": "kept", "role": "assistant", "content": "正文", "createdAt": 2,
    }])

    assert store.rename("flow_local-1", "flow_real") is True

    assert [m["content"] for m in store.load("flow_real")] == ["草稿", "正文"]
    assert store.load("flow_local-1") == []


def test_rename_deduplicates_messages_already_saved_to_the_target(tmp_path) -> None:
    store = _store(tmp_path)
    repeated = {"id": "same", "role": "user", "content": "抓取网页", "createdAt": 1}
    store.save("flow_local-1", [repeated])
    store.save("flow_real", [repeated, {
        "id": "answer", "role": "assistant", "content": "已处理", "createdAt": 2,
    }])

    assert store.rename("flow_local-1", "flow_real") is True
    assert [m["id"] for m in store.load("flow_real")] == ["same", "answer"]


def test_rename_without_a_source_is_a_no_op(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.rename("local", "flow_real") is False
    assert store.load("flow_real") == []
