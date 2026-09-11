import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { toolDisplayStatus, toolResultStatus } from './toolResult';
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
  it('展开直接显示详情，不增加二次折叠或收起按钮', () => {
    const html = renderToStaticMarkup(createElement(ToolCallCard, { expanded: true, toolCall: {
      id: 'a', tool: 'run_flow', args: '{}', status: 'done', result: {
        status: 'blocking_lint_findings', message: '请修复行选择器',
        lint_findings: [{ node_id: 'n1', severity: 'error', issue: 'container', message: '选择器指向表格容器' }],
      },
    } }));
    expect(html).toContain('已阻断');
    expect(html).toContain('选择器指向表格容器');
    expect(html).toContain('结果');
    expect(html).not.toContain('原始调用数据');
    expect(html).not.toContain('<details');
    expect(html).not.toContain('收起');
  });
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
