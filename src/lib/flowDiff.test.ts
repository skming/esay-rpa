import { describe, expect, it } from 'vitest';

import type { FlowSnapshot } from '../types/electron';
import { diffFlowSnapshots } from './flowDiff';

function buildFlow(flowId: string, definition: Record<string, unknown>): FlowSnapshot {
  return {
    createdAt: '2026-06-10T00:00:00.000Z',
    definition,
    flowId,
    folderPath: '默认目录',
    inputVariables: [],
    name: '订单自动处理',
    snapshots: [],
    status: 'active',
    updatedAt: '2026-06-10T00:00:00.000Z',
    version: flowId === 'base' ? 'v3.0.2' : 'v3.0.3'
  };
}

describe('flowDiff', () => {
  it('统计节点和连线的新增、移除、变更', () => {
    const base = buildFlow('base', {
      edges: [
        { id: 'e-start-n1', source: 'start', target: 'n1' },
        { id: 'e-n1-end', source: 'n1', target: 'end' }
      ],
      nodes: [
        { id: 'start', title: '开始', type: 'start' },
        { id: 'n1', selector: '.quote .text::text', title: '采集文本', type: 'browser.fetch' },
        { id: 'end', title: '结束', type: 'end' }
      ]
    });
    const target = buildFlow('target', {
      edges: [
        { id: 'e-start-n1', source: 'start', target: 'n1', label: '主线' },
        { id: 'e-n1-n2', source: 'n1', target: 'n2' }
      ],
      nodes: [
        { id: 'start', title: '开始', type: 'start' },
        { id: 'n1', selector: '.quote .author::text', title: '采集作者', type: 'browser.fetch' },
        { id: 'n2', title: '保存数据', type: 'data.step' }
      ]
    });

    const diff = diffFlowSnapshots(base, target);

    expect(diff.nodeAdded).toBe(1);
    expect(diff.nodeChanged).toBe(1);
    expect(diff.nodeRemoved).toBe(1);
    expect(diff.edgeAdded).toBe(1);
    expect(diff.edgeChanged).toBe(1);
    expect(diff.edgeRemoved).toBe(1);
    expect(diff.items.map((item) => item.type)).toContain('changed');
  });
});
