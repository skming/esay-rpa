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
    const before = buildFlow('base', {
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
    const after = buildFlow('target', {
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

    const diff = diffFlowSnapshots(before, after);

    expect(diff.nodeAdded).toBe(1);
    expect(diff.nodeChanged).toBe(1);
    expect(diff.nodeRemoved).toBe(1);
    expect(diff.edgeAdded).toBe(1);
    expect(diff.edgeChanged).toBe(1);
    expect(diff.edgeRemoved).toBe(1);
    expect(diff.items.map((item) => item.type)).toContain('changed');
  });

  it('变更条目列出具体改了哪个字段的什么值', () => {
    const before = buildFlow('base', {
      edges: [],
      nodes: [{ id: 'n1', selector: '.old', timeoutMs: 5000, title: '采集', type: 'browser.fetch' }]
    });
    const after = buildFlow('target', {
      edges: [],
      nodes: [{ id: 'n1', selector: '.new', timeoutMs: 5000, title: '采集', type: 'browser.fetch' }]
    });

    const changed = diffFlowSnapshots(before, after).items.find((item) => item.type === 'changed');

    expect(changed?.entityId).toBe('n1');
    expect(changed?.fields).toEqual([{ after: '.new', before: '.old', key: 'selector', label: '选择器', multiline: false }]);
  });

  it('只挪动位置不算节点变更，单独计数', () => {
    const before = buildFlow('base', {
      edges: [],
      nodes: [{ id: 'n1', position: { x: 0, y: 0 }, status: 'pending', title: '采集', type: 'browser.fetch' }]
    });
    const after = buildFlow('target', {
      edges: [],
      nodes: [{ id: 'n1', position: { x: 400, y: 120 }, status: 'success', title: '采集', type: 'browser.fetch' }]
    });

    const diff = diffFlowSnapshots(before, after);

    expect(diff.nodeChanged).toBe(0);
    expect(diff.items).toHaveLength(0);
    expect(diff.layoutOnly).toBe(1);
  });

  it('字段被删掉、置空、置 null 之间的互换不算变更', () => {
    const before = buildFlow('base', {
      edges: [],
      nodes: [{ description: '', id: 'n1', inputValue: null, title: '采集', type: 'browser.fetch' }]
    });
    const after = buildFlow('target', {
      edges: [],
      nodes: [{ id: 'n1', title: '采集', type: 'browser.fetch' }]
    });

    const diff = diffFlowSnapshots(before, after);

    expect(diff.nodeChanged).toBe(0);
    expect(diff.items).toHaveLength(0);
  });

  it('改标题也是变更，且看得到改前改后', () => {
    const before = buildFlow('base', { edges: [], nodes: [{ id: 'n1', title: '旧标题', type: 'browser.fetch' }] });
    const after = buildFlow('target', { edges: [], nodes: [{ id: 'n1', title: '新标题', type: 'browser.fetch' }] });

    const changed = diffFlowSnapshots(before, after).items.find((item) => item.type === 'changed');

    expect(changed?.title).toBe('新标题');
    expect(changed?.fields).toEqual([{ after: '新标题', before: '旧标题', key: 'title', label: '标题', multiline: false }]);
  });

  it('连线用节点标题描述，而不是裸 id', () => {
    const before = buildFlow('base', { edges: [], nodes: [] });
    const after = buildFlow('target', {
      edges: [{ id: 'e1', label: 'true', source: 'n1', target: 'n2' }],
      nodes: [
        { id: 'n1', title: '判断是否登录', type: 'control.condition' },
        { id: 'n2', title: '打开工作台', type: 'browser.open' }
      ]
    });

    const edgeItem = diffFlowSnapshots(before, after).items.find((item) => item.scope === 'edge');

    expect(edgeItem?.title).toBe('判断是否登录 → 打开工作台');
    expect(edgeItem?.subtitle).toBe('分支：true');
  });
});
