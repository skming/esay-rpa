const assert = require('node:assert/strict');
const { test } = require('node:test');
const {
  normalizeConcurrency, normalizeLimit, normalizeDebugCommand,
  normalizeFlowPayload, normalizeFlowRunPayload, normalizeRunPayload, normalizeSchedulePayload,
} = require('../../shared/backendPayloads.cjs');

test('并发和分页处理非有限数字、取整和上下界', () => {
  for (const value of [undefined, NaN, Infinity, -Infinity, '2']) {
    assert.equal(normalizeConcurrency(value), 1);
    assert.equal(normalizeLimit(value), 50);
  }
  assert.equal(normalizeConcurrency(-10), 1);
  assert.equal(normalizeConcurrency(2.7), 3);
  assert.equal(normalizeConcurrency(100), 20);
  assert.equal(normalizeLimit(-10), 1);
  assert.equal(normalizeLimit(2.7), 3);
  assert.equal(normalizeLimit(1000), 200);
});

test('运行请求拒绝数组变量和定义，并应用执行策略默认值', () => {
  const result = normalizeRunPayload({ variables: [], flowDefinition: [], scope: 'bad', failureStrategy: 'bad', browserExecutor: 'bad' });
  assert.deepEqual(result.variables, {});
  assert.equal(result.flowDefinition, undefined);
  assert.equal(result.scope, 'full');
  assert.equal(result.failureStrategy, 'stop');
  assert.equal(result.browserExecutor, 'playwright');
  assert.equal(result.mode, 'run');
  assert.equal(result.timeoutMs, 30000);
  assert.equal(result.screenshot, true);
  assert.deepEqual(normalizeFlowRunPayload({ variables: null }).variables, {});
});

test('新建流程保留所有有效状态，拒绝数组定义', () => {
  for (const status of ['draft', 'active', 'paused', 'disabled', 'archived']) {
    assert.equal(normalizeFlowPayload({ status }).status, status);
  }
  assert.equal(normalizeFlowPayload({ status: 'bad' }).status, 'draft');
  assert.deepEqual(normalizeFlowPayload({ definition: [] }).definition, {});
});

test('计划保留多流程和任务参数，省略旧采集字段以满足后端可选字段契约', () => {
  const result = normalizeSchedulePayload({ task: { flowIds: ['flow-1', 'flow-2'], timeoutMs: 65000, screenshot: false } });
  assert.deepEqual(result.task.flowIds, ['flow-1', 'flow-2']);
  assert.equal(result.task.timeoutMs, 65000);
  assert.equal(result.task.screenshot, false);
  assert.equal(result.task.adaptive, true);
  assert.equal(result.task.autoSave, true);
  assert.equal(result.task.selector, undefined);
  assert.equal(result.task.targetUrl, undefined);
});

test('不合法的调试命令必须拒绝', () => {
  assert.throws(() => normalizeDebugCommand('restart'), /调试命令不合法/);
  for (const command of ['continue', 'step-into', 'step-over']) {
    assert.equal(normalizeDebugCommand(command), command);
  }
});
