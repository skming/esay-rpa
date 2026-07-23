import { describe, expect, it } from 'vitest';

import type { RunLogEntry } from '../../../types/rpa';
import { buildErrorSummary } from './bottomPanelUtils';

function row(overrides: Partial<RunLogEntry>): RunLogEntry {
  return { id: 'log-1', time: '10:00:00', level: 'error', message: '出错了', ...overrides };
}

describe('buildErrorSummary', () => {
  it('把节点 id 解析为节点标题并拼接 detail', () => {
    const summary = buildErrorSummary(
      [row({ nodeId: 'n1', message: '元素未找到', detail: 'selector: .price' })],
      { n1: '抓取价格' },
    );
    expect(summary).toBe('- [节点「抓取价格」] 元素未找到 — selector: .price');
  });

  it('节点标题缺失时回退到节点 id，无节点信息时省略前缀', () => {
    const summary = buildErrorSummary([row({ nodeId: 'ghost' }), row({ message: '全局超时' })], {});
    expect(summary).toContain('[节点「ghost」]');
    expect(summary).toContain('- 全局超时');
  });

  it('超过 3 条只保留前 3 条并注明总数，超长消息截断', () => {
    const rows = Array.from({ length: 5 }, (_, i) => row({ id: `log-${i}`, message: `错误${i}`.padEnd(300, 'x') }));
    const summary = buildErrorSummary(rows, {});
    const lines = summary.split('\n');
    expect(lines).toHaveLength(4);
    expect(lines[3]).toBe('- ……共 5 条错误，其余略');
    expect(lines[0].endsWith('…')).toBe(true);
    expect(lines[0].length).toBeLessThan(210);
  });
});
