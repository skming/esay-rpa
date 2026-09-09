import { describe, expect, it } from 'vitest';

import { buildFlowDefinition } from '../lib/flowDefinition';
import { createFlowNode } from '../lib/flowOperations';
import type { StoredFlowDraft } from '../stores/useFlowDraftStore';
import type { FlowSnapshot } from '../types/electron';
import { createCanvasSignature, createSavedFlowSignature, readRestorableDraft } from './useFlowDraftAutosave';

function makeDraft(overrides: Partial<StoredFlowDraft> = {}): StoredFlowDraft {
  const node = createFlowNode({ label: 'HTTP 请求', nodeType: 'script' }, { x: 10, y: 20 }, 1);
  return {
    schemaVersion: 1,
    savedAt: '2026-07-21T02:00:00.000Z',
    flowId: 'flow_abc',
    flowName: '草稿流程',
    baseSignature: 'sig-base',
    definition: buildFlowDefinition([node], [], [{ name: 'token', scope: '全局', type: 'String', value: 'x' }]),
    ...overrides,
  };
}

describe('readRestorableDraft', () => {
  it('恢复画布、输入变量与基线签名', () => {
    const restored = readRestorableDraft(makeDraft());

    expect(restored?.nodes).toHaveLength(1);
    expect(restored?.baseSignature).toBe('sig-base');
    expect(restored?.savedAt).toBe('2026-07-21T02:00:00.000Z');
    expect(restored?.inputVariables.map((v) => v.name)).toEqual(['token']);
  });

  it('没有草稿或版本不兼容时返回 null，避免用旧结构覆盖画布', () => {
    expect(readRestorableDraft(null)).toBeNull();
    expect(readRestorableDraft(makeDraft({ schemaVersion: 99 as unknown as 1 }))).toBeNull();
  });

  it('definition 损坏到无法恢复时返回 null，而不是恢复出半个画布', () => {
    expect(readRestorableDraft(makeDraft({ definition: { nodes: 'not-an-array' } }))).toBeNull();
  });

  it('local- 前缀的临时流程不回填 flowId：这类流程未落库，回填后查不到记录', () => {
    expect(readRestorableDraft(makeDraft({ flowId: 'local-123' }))?.flowId).toBeNull();
    expect(readRestorableDraft(makeDraft({ flowId: '' }))?.flowId).toBeNull();
    expect(readRestorableDraft(makeDraft({ flowId: 'flow_abc' }))?.flowId).toBe('flow_abc');
  });
});

describe('草稿与保存快照的比较', () => {
  const nodes = [createFlowNode({ label: 'HTTP 请求', nodeType: 'script' }, { x: 10, y: 20 }, 1)];
  const variables = [{ name: 'query', scope: '全局', type: 'String', value: 'saved' }] as const;
  const flow: FlowSnapshot = {
    flowId: 'saved-flow', name: '已保存流程', version: 'v1.0.0', status: 'draft',
    createdAt: '', updatedAt: '', folderPath: '默认目录', snapshots: [],
    definition: buildFlowDefinition(nodes, [], [...variables]), inputVariables: [...variables],
  };

  it('保存后无修改时保持干净，运行状态和导出时间不触发草稿', () => {
    const baseline = createSavedFlowSignature(flow);
    expect(createCanvasSignature(nodes, [], [...variables])).toBe(baseline);
    expect(createCanvasSignature(nodes.map((node) => ({ ...node, data: { ...node.data, status: 'done' } })), [], [...variables])).toBe(baseline);
    expect(createSavedFlowSignature({ ...flow, name: '改名', definition: { ...flow.definition, exportedAt: 'later' } })).toBe(baseline);
  });

  it('节点内容、位置、连线和变量修改都与保存快照不同', () => {
    const baseline = createSavedFlowSignature(flow);
    expect(createCanvasSignature(nodes.map((node) => ({ ...node, data: { ...node.data, title: '修改标题' } })), [], [...variables])).not.toBe(baseline);
    expect(createCanvasSignature(nodes.map((node) => ({ ...node, data: { ...node.data, action: { ...node.data.action!, url: 'https://example.com' } } })), [], [...variables])).not.toBe(baseline);
    expect(createCanvasSignature(nodes.map((node) => ({ ...node, position: { x: 50, y: 20 } })), [], [...variables])).not.toBe(baseline);
    expect(createCanvasSignature(nodes, [{ id: 'edge', source: nodes[0].id, target: nodes[0].id }], [...variables])).not.toBe(baseline);
    expect(createCanvasSignature(nodes, [], [{ ...variables[0], value: 'edited' }])).not.toBe(baseline);
  });

  it('采用最新保存的快照，切换流程不会沿用前一个流程的基线', () => {
    const changedNodes = nodes.map((node) => ({ ...node, position: { x: 100, y: 50 } }));
    const saved = { ...flow, definition: buildFlowDefinition(changedNodes, [], [...variables]) };
    expect(createCanvasSignature(changedNodes, [], [...variables])).toBe(createSavedFlowSignature(saved));
    expect(createSavedFlowSignature({ ...saved, flowId: 'another-flow' })).not.toBe(createSavedFlowSignature(flow));
  });

  it('未保存的流程没有服务端基线，变量以快照外层字段为准', () => {
    expect(createSavedFlowSignature(null)).toBeNull();
    expect(createSavedFlowSignature({ ...flow, flowId: 'local-1', definition: {} })).toBeNull();
    const inputVariables = [{ ...variables[0], value: 'latest' }];
    expect(createSavedFlowSignature({ ...flow, inputVariables })).toBe(createCanvasSignature(nodes, [], inputVariables));
  });
});
