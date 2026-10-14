import pytest

from app.services.ai_guard_state import GuardState
from app.services.ai_orchestrator import AiOrchestrator, _overstated_result_claim, _RESULT_CLAIM_PHRASES
from app.services.ai_response_evidence import result_claim_correction


@pytest.mark.parametrize("text", ["尚未验收通过", "不能宣称已修复", "此前误称“验收通过”", "“已修复”这个说法是错误的"])
def test_negated_or_retracted_claims_do_not_trigger_rewrite(text):
    assert result_claim_correction(text, GuardState(), _overstated_result_claim, _RESULT_CLAIM_PHRASES) is None


@pytest.mark.parametrize("text", ["验收通过", "没有报错，已修复", "尚未验收通过，但已修复", "没有报错已修复"])
def test_unsupported_claim_is_checked_after_the_first_correction(text):
    state = GuardState(flow_has_nodes=True, current_flow_revision=1, result_claim_corrected=True)
    assert result_claim_correction(text, state, _overstated_result_claim, _RESULT_CLAIM_PHRASES)
    assert state.result_claim_corrected is True


async def test_repeated_unsupported_claim_uses_two_model_calls_then_deterministic_reply(monkeypatch):
    import litellm
    from test_ai_orchestrator import _RunnableFlowExecutor, _FakeStream, _chunk
    calls = []
    async def complete(**kwargs):
        calls.append(kwargs)
        assert len(calls) <= 2
        return _FakeStream([_chunk(content="验收通过", finish="stop")])
    monkeypatch.setattr(litellm, "acompletion", complete)
    events = [event async for event in AiOrchestrator(_RunnableFlowExecutor()).stream(
        [{"role": "user", "content": "只看结构"}], "test-model", "flow-run",
    )]
    visible = ""
    for event in events:
        if event["type"] == "retract":
            visible = ""
        elif event["type"] == "text":
            visible += event["delta"]
    assert len(calls) == 2
    assert "缺少平台证据" in visible
    assert "验收通过" not in visible
    assert events[-1]["type"] == "done"
