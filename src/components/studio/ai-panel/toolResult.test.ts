import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { redactToolPayload, toolDisplayStatus, toolResultStatus, toolResultSummary } from './toolResult';
import { ToolCallCard } from './ToolCallCard';

describe('工具证据状态', () => {
  it.each([
    [{ error: 'invalid_arguments' }, 'error'],
    [{ status: 'blocking_lint_findings' }, 'blocked'],
    [{ status: 'blocked_extension_busy', error: 'busy' }, 'blocked'],
    [{ passed: false }, 'error'],
    [{ status: 'success', acceptance_audit: { passed: false } }, 'error'],
    [{ status: 'success', acceptance_audit: { passed: true } }, 'done'],
    [{ count: 0, values: [] }, 'done'],
  ] as const)('按实际返回判定 %j', (result, expected) => {
    expect(toolResultStatus(result)).toBe(expected);
  });
  it('历史绿色状态不能掩盖返回错误，未返回的历史调用显示中断', () => {
    expect(toolDisplayStatus({ id: 'a', tool: 'run_flow', args: '{}', status: 'done', result: { error: 'timeout' } }, false)).toBe('error');
    expect(toolDisplayStatus({ id: 'a', tool: 'run_flow', args: '{}', status: 'running' }, false)).toBe('stopped');
  });
  it('展开先显示结构化证据，原始数据保持二次折叠', () => {
    const html = renderToStaticMarkup(createElement(ToolCallCard, { expanded: true, toolCall: {
      id: 'a', tool: 'run_flow', args: '{}', status: 'done', result: {
        status: 'blocking_lint_findings', message: '请修复行选择器',
        lint_findings: [{ node_id: 'n1', severity: 'error', issue: 'container', message: '选择器指向表格容器' }],
      },
    } }));
    expect(html).toContain('已阻断');
    expect(html).toContain('选择器指向表格容器');
    expect(html).toContain('原始数据');
    expect(html).toContain('<details');
    expect(html).not.toContain('收起');
  });
});

it('用实际工具结果生成紧凑证据摘要', () => {
  expect(toolResultSummary({ id: '1', tool: 'run_flow', args: '{}', status: 'done', result: {
    status: 'success', acceptance_audit: { passed: true },
  } })).toBe('运行成功 · 验收通过');
  expect(toolResultSummary({ id: '2', tool: 'update_flow', args: '{}', status: 'done', result: {
    status: 'applied', revision: 8, changed_nodes: [{ id: 'n1' }, { id: 'n2' }],
  } })).toBe('r8 · 2 个节点变更');
  expect(toolResultSummary({ id: '3', tool: 'get_run_output', args: '{}', status: 'done', result: {
    status: 'success', variables: {}, artifacts: [],
  } })).toBe('无输出数据');
  expect(toolResultSummary({ id: '4', tool: 'inspect_page', args: '{}', status: 'done', result: {
    page_outcome: 'target_content_ready',
    inputs: [{ actions: ['fill:v1:input'] }],
    buttons: [{ actions: ['click:v1:button'] }],
    page_actions: ['scroll:v1:page:down'],
  } })).toBe('目标内容已就绪 · 3 个可操作目标');
});

it('原始工具数据隐藏凭据和运行变量值', () => {
  expect(redactToolPayload({
    password: 'secret', nested: { accessToken: 'token' }, variables: { page: 1, account: 'alice' }, visible: 'ok',
    input_variables: [{ name: 'login', category: 'credential', value: 'alice' }],
  }, '', true)).toEqual({
    password: '••••', nested: { accessToken: '••••' }, variables: { page: '••••', account: '••••' }, visible: 'ok',
    input_variables: [{ name: 'login', category: 'credential', value: '••••' }],
  });
  expect(redactToolPayload({ variables: { rows: [{ id: 1 }] } })).toEqual({ variables: { rows: [{ id: 1 }] } });
});


it('正常工具收起时只有名称，不展示空发现和页面长标题', () => {
  for (const [tool, result] of [
    ['lint_flow', { findings: [] }],
    ['inspect_page', { title: '不应重复展示的页面长标题' }],
  ] as const) {
    const html = renderToStaticMarkup(createElement(ToolCallCard, { toolCall: {
      id: tool, tool, args: '{}', status: 'done', result,
    } }));
    expect(html).not.toContain('0 项检查发现');
    expect(html).not.toContain('不应重复展示的页面长标题');
    expect(html).toContain('class="sr-only"');
  }
});
