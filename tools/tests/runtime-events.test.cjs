const assert = require('node:assert/strict');
const { test } = require('node:test');
const {
  applyBackendNodeState, collectKnownNodeIds, finalizeLastActiveNode,
  readBackendTotalSteps, resolveBackendLogNodeId,
} = require('../../shared/runtimeEvents.cjs');

function fixture(definition = { nodes: [{ id: 'collect' }, { id: 'write' }] }) {
  const events = [];
  const run = { runId: 'task-1', knownNodeIds: collectKnownNodeIds(definition), lastActiveNodeId: null, nodeStates: new Map() };
  const emit = event => events.push(event);
  return { run, events, emit };
}

test('使用真实节点 id，忽略流程之外的节点', () => {
  const { run, events, emit } = fixture();
  applyBackendNodeState(run, emit, 'collect', { level: 'running' });
  applyBackendNodeState(run, emit, 'n1', { level: 'running' });
  assert.deepEqual(events.map(event => event.payload.nodeId), ['collect']);
  assert.equal(run.lastActiveNodeId, 'collect');
});

test('没有流程定义时依照后端日志，而不假定内置节点', () => {
  const { run, events, emit } = fixture({});
  applyBackendNodeState(run, emit, 'actual-node', { level: 'running' });
  assert.equal(events[0].payload.nodeId, 'actual-node');
});

test('循环再次进入相同节点时恢复 running 状态', () => {
  const { run, events, emit } = fixture();
  for (const level of ['running', 'success', 'success', 'running', 'success']) {
    applyBackendNodeState(run, emit, 'collect', { level });
  }
  assert.deepEqual(events.map(event => event.payload.status), ['running', 'done', 'running', 'done']);
});

test('进入下个节点时完成前一个节点，并在错误终态收尾当前节点', () => {
  const { run, events, emit } = fixture();
  applyBackendNodeState(run, emit, 'collect', { level: 'running' });
  applyBackendNodeState(run, emit, 'write', { level: 'running' });
  finalizeLastActiveNode(run, emit, 'error');
  finalizeLastActiveNode(run, emit, 'error');
  assert.deepEqual(events.map(event => [event.payload.nodeId, event.payload.status]), [
    ['collect', 'running'], ['collect', 'done'], ['write', 'running'], ['write', 'error'],
  ]);
});

test('优先日志中的节点 id，缺失时按任务边界或当前节点归属', () => {
  assert.equal(resolveBackendLogNodeId({ nodeId: 'collect', message: '任务完成' }, 'write'), 'collect');
  assert.equal(resolveBackendLogNodeId({ message: '任务完成' }, 'write'), 'end');
  assert.equal(resolveBackendLogNodeId({ message: '执行动作' }, 'write'), 'write');
  assert.equal(resolveBackendLogNodeId({ message: '准备启动' }), 'start');
});

test('进度优先后端数字，缺失时使用最小可显示值', () => {
  assert.equal(readBackendTotalSteps({ totalSteps: 7 }), 7);
  assert.equal(readBackendTotalSteps({ totalSteps: 0 }), 1);
  assert.equal(readBackendTotalSteps({ totalSteps: NaN }), 1);
  assert.equal(readBackendTotalSteps(), 1);
});
