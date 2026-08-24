"""会话检查点的回归测试。

这层东西坏了不会报错，只会安静地变回原样——预算重新归零、义务凭空消失，
表现和「没做过这个功能」完全一致。所以每条断言钉的都是「省下的代价确实省下了」，
以及反面：任务做完之后不许再背着旧熔断。
"""

import ast
import inspect
import json
import time
from pathlib import Path
from typing import Any

import pytest

from app.services import ai_orchestrator, ai_phases
from app.services import ai_session_checkpoint as checkpoint
from app.services.ai_phases import VERIFY_ATTEMPT_BUDGET

# guard_state 在这两个模块里出现的全部局部名。漏一个名字这条元测试就少扫一批写入点，
# 于是变成一盏假绿灯——名字要跟着改动一起加。
_STATE_VAR_NAMES = frozenset({"state", "guard_state", "facts"})


def _budget(spent: int, *, attempts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"spent": spent, "signatures": {}, "attempts": attempts or []}


@pytest.fixture(autouse=True)
def _isolated_ai_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(checkpoint, "resolve_ai_dir", lambda: tmp_path)


def test_budgets_and_obligations_survive_a_round_trip() -> None:
    checkpoint.save("f1", {
        "attempt_budget": _budget(2),
        "audit_findings": {"issues": [{"issue": "mixed_ui_rows"}], "repair_plan": []},
        "page_snapshot": {"huge": "irrelevant"},
    }, rounds=3)
    assert checkpoint.load("f1") == {
        "attempt_budget": _budget(2),
        "audit_findings": {"issues": [{"issue": "mixed_ui_rows"}], "repair_plan": []},
    }


def test_only_whitelisted_keys_persist() -> None:
    """guard_state 里大部分是本轮请求自带的上下文，存下来只会在下轮变成过期事实。"""
    checkpoint.save("f1", {"attempt_budget": _budget(1), "model_no_vision": True}, rounds=1)
    assert "model_no_vision" not in checkpoint.load("f1")


def test_clean_state_leaves_no_file_behind() -> None:
    """留一份全空的检查点，只会让之后每次会话都白读一次盘。

    未动过的预算（spent=0）不算未了结事项：它是「这轮从零开始」的默认值，
    存一份只会让下一轮白读一次盘。
    """
    checkpoint.save("f1", {"attempt_budget": _budget(3)}, rounds=1)
    checkpoint.save("f1", {"attempt_budget": None}, rounds=2)
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
    checkpoint.save(None, {"attempt_budget": _budget(5)}, rounds=1)
    assert checkpoint.load(None) == {}


def test_clear_removes_the_checkpoint() -> None:
    checkpoint.save("f1", {"attempt_budget": _budget(2)}, rounds=1)
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
    assert checkpoint.summarize({"attempt_budget": _budget(0)}) is None


def test_summarize_explains_the_exhausted_budget_instead_of_just_carrying_it() -> None:
    """不解释就等于模型凭空少了额度——它会反复试同一件事然后反复被拦。"""
    note = checkpoint.summarize({
        "attempt_budget": _budget(VERIFY_ATTEMPT_BUDGET),
        "runtime_escape_findings": [{"issue": "undefined_variable_ref_runtime_escape"}],
    })
    assert note is not None
    assert "额度已经耗尽" in note
    assert "未定义变量" in note


def test_summarize_hands_back_the_directions_already_tried() -> None:
    """只说「额度有限」模型会换一个同类改法再试；把试过的方向交回去才躲得开。"""
    note = checkpoint.summarize({
        "attempt_budget": _budget(1, attempts=[{"kind": "run_error", "detail": "改了 tbody tr"}]),
    })
    assert note is not None
    assert "改了 tbody tr" in note
    # 不报剩余数字：报数字等于把上限当额度用
    assert str(VERIFY_ATTEMPT_BUDGET) not in note


def test_summarize_carries_the_pending_dom_evidence_obligation() -> None:
    """义务本身由阶段机拦，但不解释模型只会撞一次墙才知道；url 要一并交回去。"""
    note = checkpoint.summarize({
        "page_evidence_required": {"url": "https://a.test/", "reason": "run_failed_on_page_element"},
    })
    assert note is not None
    assert "inspect_page" in note and "https://a.test/" in note


def test_static_diagnostics_are_not_carried_across_sessions() -> None:
    """静态诊断存下来只会在流程已经修好之后还挡着人：它每轮由状态块重算。

    运行期逃逸必须反过来——静态扫描看不见它，重算永远算不出来，不存就丢。
    """
    assert "blocking_diagnostics" not in checkpoint._PERSISTED_KEYS
    assert "runtime_escape_findings" in checkpoint._PERSISTED_KEYS


def test_every_guard_state_key_is_explicitly_decided() -> None:
    """写入侧唯一的症状来源。

    编排层往 guard_state 里写 29 个键，「这个键要不要跨轮留下」全靠人记得。
    忘了存 → 中断续跑丢义务；不该存却存了 → 流程已经修好还挡着人。两种都不报错。
    所以每个被写过的键必须在两张表里表过态，新增一个而没表态就在这里红。
    """
    written: set[str] = set()
    for module in (ai_orchestrator, ai_phases):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id in _STATE_VAR_NAMES
                        and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)
                    ):
                        written.add(target.slice.value)
            # setdefault 也是写：node_selector_fix_counts 就只经由它建出来
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setdefault"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in _STATE_VAR_NAMES
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                written.add(node.args[0].value)

    assert written, "取不到任何 state 键，这条元测试会静默通过"
    undecided = written - set(checkpoint._PERSISTED_KEYS) - checkpoint._PER_ROUND_KEYS
    assert not undecided, f"以下键没在检查点表过态：{sorted(undecided)}"
    # 反向：表里写了却没人写这个键，说明键名改过而表没跟上
    stale = (set(checkpoint._PERSISTED_KEYS) | checkpoint._PER_ROUND_KEYS) - written
    assert not stale, f"以下键已无人写入，检查点名单过期：{sorted(stale)}"


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
        async def execute(self, tool_name: str, args: dict[str, Any],
                          progress_sink: Any = None, change_context: Any = None) -> dict[str, Any]:
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

    assert checkpoint.load("f-int").get("page_evidence_required"), "中断丢掉义务后，下轮会直接重跑而不是先看 DOM"


async def test_orchestrator_resumes_then_clears_on_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """端到端钉住生命周期：上轮的熔断要带进这轮的提示词，任务收尾要把它清掉。"""
    import litellm

    from app.services.ai_orchestrator import AiOrchestrator

    from test_ai_orchestrator import _chunk, _FakeExecutor, _FakeStream  # noqa: F401

    monkeypatch.setattr(checkpoint, "resolve_ai_dir", lambda: tmp_path)
    checkpoint.save("f-ck", {"attempt_budget": _budget(VERIFY_ATTEMPT_BUDGET)}, rounds=4)

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
