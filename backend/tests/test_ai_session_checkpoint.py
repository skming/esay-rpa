"""会话检查点的回归测试。

这层东西坏了不会报错，只会安静地变回原样——预算重新归零、义务凭空消失，
表现和「没做过这个功能」完全一致。所以每条断言钉的都是「省下的代价确实省下了」，
以及反面：任务做完之后不许再背着旧熔断。
"""

import json
import time
from pathlib import Path
from typing import Any

import pytest

from app.services import ai_session_checkpoint as checkpoint


@pytest.fixture(autouse=True)
def _isolated_ai_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(checkpoint, "resolve_ai_dir", lambda: tmp_path)


def test_budgets_and_obligations_survive_a_round_trip() -> None:
    checkpoint.save("f1", {
        "failed_run_cycles": 2,
        "requires_inspect_page": True,
        "page_snapshot": {"huge": "irrelevant"},
    }, rounds=3)
    assert checkpoint.load("f1") == {"failed_run_cycles": 2, "requires_inspect_page": True}


def test_only_whitelisted_keys_persist() -> None:
    """guard_state 里大部分是本轮请求自带的上下文，存下来只会在下轮变成过期事实。"""
    checkpoint.save("f1", {"failed_run_cycles": 1, "model_no_vision": True}, rounds=1)
    assert "model_no_vision" not in checkpoint.load("f1")


def test_clean_state_leaves_no_file_behind() -> None:
    """留一份全空的检查点，只会让之后每次会话都白读一次盘。"""
    checkpoint.save("f1", {"failed_run_cycles": 3}, rounds=1)
    checkpoint.save("f1", {"failed_run_cycles": 0}, rounds=2)
    assert checkpoint.load("f1") == {}


def test_stale_checkpoint_is_ignored(tmp_path: Path) -> None:
    """隔了几小时再回来，页面和流程多半都变了，旧熔断不该继续挡人。"""
    checkpoint.save("f1", {"failure_budget_lock": True}, rounds=1)
    path = tmp_path / "checkpoints" / "flow_f1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["updated_at"] = time.time() - 3 * 3600
    path.write_text(json.dumps(data), encoding="utf-8")
    assert checkpoint.load("f1") == {}


def test_corrupt_checkpoint_is_ignored_not_raised(tmp_path: Path) -> None:
    """检查点是省钱的优化，不是正确性的前提——读坏了必须让会话照常开始。"""
    d = tmp_path / "checkpoints"
    d.mkdir(parents=True)
    (d / "flow_f1.json").write_text("{不是 json", encoding="utf-8")
    assert checkpoint.load("f1") == {}


def test_missing_flow_id_is_a_no_op() -> None:
    """临时流程没有 id，落盘会互相串台。"""
    checkpoint.save(None, {"failed_run_cycles": 5}, rounds=1)
    assert checkpoint.load(None) == {}


def test_clear_removes_the_checkpoint() -> None:
    checkpoint.save("f1", {"failed_run_cycles": 2}, rounds=1)
    checkpoint.clear("f1")
    assert checkpoint.load("f1") == {}


def test_static_page_evidence_survives_into_the_next_turn() -> None:
    """流程存下来之后，下一轮不再回读探测历史——不持久化这条，第二轮护栏就凭空消失。"""
    checkpoint.save("f1", {"page_evidence_source": "scrapling_static"}, rounds=1)
    assert checkpoint.load("f1").get("page_evidence_source") == "scrapling_static"


def test_summarize_explains_the_static_evidence_channel() -> None:
    """只带着限制不解释，模型会反复交出 browser.open 然后反复被拦。"""
    note = checkpoint.summarize({"page_evidence_source": "scrapling_static"})
    assert note is not None
    assert "browser.fetch" in note and "static" in note


def test_summarize_stays_quiet_for_browser_dom_evidence() -> None:
    """浏览器通道本来就可用，没有任何限制要交代。"""
    assert checkpoint.summarize({"page_evidence_source": "browser_dom"}) is None


def test_summarize_speaks_only_when_something_is_unfinished() -> None:
    assert checkpoint.summarize({}) is None
    assert checkpoint.summarize({"navigation_failure_counts": {"a": 1}}) is None


def test_summarize_explains_the_lock_instead_of_just_carrying_it() -> None:
    """不解释就等于模型凭空少了额度——它会反复试同一件事然后反复被拦。"""
    note = checkpoint.summarize({
        "repair_cycle_lock": {"cycles": 3},
        "requires_lint_fix": True,
    })
    assert note is not None
    assert "3" in note and "熔断" in note
    assert "lint" in note


def test_summarize_prefers_the_lock_over_the_raw_count() -> None:
    note = checkpoint.summarize({"repair_cycle_lock": {"cycles": 2}, "failed_run_cycles": 2})
    assert note is not None
    assert note.count("- ") == 1


async def test_interrupted_session_keeps_what_the_tools_already_cost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """用户中途点停止：生成器就停在某个 yield 上再也不往下走。

    落盘必须紧贴状态变更本身，晚到本轮收尾就等于永远不会执行——而这正是最该省的场景，
    因为已经付掉的是 run_flow 那几分钟。
    """
    import litellm

    from app.services.ai_orchestrator import AiOrchestrator

    from test_ai_orchestrator import _chunk, _FakeStream, _tool_call_chunk

    monkeypatch.setattr(checkpoint, "resolve_ai_dir", lambda: tmp_path)

    class _ErrorExecutor:
        async def execute(self, tool_name: str, args: dict[str, Any], progress_sink: Any = None) -> dict[str, Any]:
            # selector 超时：编排层据此立下「必须先 inspect_page」的义务
            return {"status": "ok", "inspect_hint": True, "last_browser_url": "https://a.test"}

    async def fake_acompletion(**kwargs: Any) -> Any:
        return _FakeStream([
            _chunk(tool_calls=[_tool_call_chunk(0, call_id="c1", name="get_run_error",
                                                arguments='{"task_id": "t1"}')], finish="tool_calls"),
        ])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    orchestrator = AiOrchestrator(tool_executor=_ErrorExecutor())  # type: ignore[arg-type]
    stream = orchestrator.stream(
        messages=[{"role": "user", "content": "跑一下"}], model="test-model", flow_id="f-int",
    )
    async for event in stream:
        if event["type"] == "tool_result":
            break
    await stream.aclose()

    assert checkpoint.load("f-int").get("requires_inspect_page"), "中断丢掉义务后，下轮会直接重跑而不是先看 DOM"


async def test_orchestrator_resumes_then_clears_on_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """端到端钉住生命周期：上轮的熔断要带进这轮的提示词，任务收尾要把它清掉。"""
    import litellm

    from app.services.ai_orchestrator import AiOrchestrator

    from test_ai_orchestrator import _chunk, _FakeExecutor, _FakeStream  # noqa: F401

    monkeypatch.setattr(checkpoint, "resolve_ai_dir", lambda: tmp_path)
    checkpoint.save("f-ck", {"repair_cycle_lock": {"cycles": 3}}, rounds=4)

    captured: list[list[dict[str, Any]]] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        captured.append(kwargs["messages"])
        return _FakeStream([_chunk(content="根因在站点侧，我不再重试了。", finish="stop")])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    orchestrator = AiOrchestrator(tool_executor=_FakeExecutor())  # type: ignore[arg-type]
    events = [e async for e in orchestrator.stream(
        messages=[{"role": "user", "content": "还是不行"}], model="test-model", flow_id="f-ck",
    )]

    resume_notes = [m for m in captured[0] if "上次未完成的会话" in str(m.get("content"))]
    assert resume_notes, "熔断没带进这轮，模型会重新领一份完整额度"
    assert events[-1] == {"type": "done"}
    assert checkpoint.load("f-ck") == {}, "任务已收尾，下一条新需求不该背着这次的熔断"
