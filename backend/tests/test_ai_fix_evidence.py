from types import SimpleNamespace

from app.models.schemas import FlowCreateRequest
from app.services.ai_guard_state import GuardState
from app.services.ai_orchestrator import _after_flow_write
from app.services.ai_tools.executor import RpaToolExecutor
from app.services.flow_service import FlowService


async def test_clean_node_fix_replaces_old_blocking_evidence():
    service = FlowService()
    flow = await service.create_flow(FlowCreateRequest(name='修复证据', definition={
        'nodes': [{'id': 'n1', 'type': 'variable.set', 'variableName': 'result', 'value': 'old'}],
        'edges': [],
    }))
    result = await RpaToolExecutor(service, SimpleNamespace()).execute('apply_node_fix', {
        'flow_id': flow.flow_id, 'node_id': 'n1', 'config_patch': {'value': 'new'},
    })
    assert result['status'] == 'patched', result
    assert result['lint_clean'] is True
    state = GuardState(flow_id=flow.flow_id)
    state.blocking_diagnostics = [{'issue': 'stale'}]
    _after_flow_write(result, state)
    assert state.blocking_diagnostics is None


def test_write_without_lint_evidence_keeps_blocking_diagnostics():
    state = GuardState()
    state.blocking_diagnostics = [{'issue': 'unresolved'}]
    _after_flow_write({'status': 'patched'}, state)
    assert state.blocking_diagnostics == [{'issue': 'unresolved'}]
