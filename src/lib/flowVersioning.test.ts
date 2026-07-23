import { describe, expect, it } from 'vitest';

import type { FlowSnapshot } from '../types/electron';
import { buildNextFlowVersion, buildUpdatePayload, hasDefinitionChanged } from './flowVersioning';

function buildFlow(overrides: Partial<FlowSnapshot> = {}): FlowSnapshot {
  return {
    createdAt: '2026-06-10T00:00:00.000Z',
    definition: {
      edges: [{ id: 'e-start-n1', source: 'start', target: 'n1' }],
      nodes: [{ id: 'start', title: '开始' }]
    },
    flowId: 'flow-1',
    inputVariables: [{ category: 'flow', name: 'username', sensitive: false, scope: '全局', type: 'String', value: 'zhang.san' }],
    flowName: undefined,
    name: '订单自动处理',
    snapshots: [],
    status: 'active',
    updatedAt: '2026-06-10T00:00:00.000Z',
    version: 'v3.0.2',
    ...overrides
  } as FlowSnapshot;
}

describe('flowVersioning', () => {
  it('按同名流程的最大 patch 版本创建下一快照版本', () => {
    const current = buildFlow({ version: 'v3.0.2' });
    const flows = [
      current,
      buildFlow({ flowId: 'flow-2', version: 'v3.0.5' }),
      buildFlow({ flowId: 'flow-3', name: '其他流程', version: 'v9.9.9' })
    ];

    expect(buildNextFlowVersion(current, flows)).toBe('v3.0.6');
  });

  it('仅忽略 exportedAt 判断流程定义是否变化', () => {
    const current = buildFlow({
      definition: {
        exportedAt: 'old',
        nodes: [{ id: 'n1', title: '打开网页' }],
        edges: []
      }
    });

    expect(
      hasDefinitionChanged(current, {
        exportedAt: 'new',
        nodes: [{ id: 'n1', title: '打开网页' }],
        edges: []
      }, current.inputVariables)
    ).toBe(false);
    expect(
      hasDefinitionChanged(current, {
        nodes: [{ id: 'n1', title: '输入文本' }],
        edges: []
      }, current.inputVariables)
    ).toBe(true);
    expect(
      hasDefinitionChanged(current, {
        exportedAt: 'new',
        nodes: [{ id: 'n1', title: '打开网页' }],
        edges: []
      }, [{ category: 'environment', name: 'row_count', sensitive: false, scope: '全局', type: 'Integer', value: '1' }])
    ).toBe(true);
  });

  it('保存时生成 active 更新 payload', () => {
    const current = buildFlow({ version: 'v3.0.2' });
    const updatePayload = buildUpdatePayload(current, [current], { nodes: [], edges: [] }, current.inputVariables);

    expect(updatePayload.version).toBe('v3.0.3');
    expect(updatePayload.status).toBe('active');
    expect(updatePayload.name).toBe('订单自动处理');
    expect(updatePayload.inputVariables?.[0]?.name).toBe('username');
    expect(updatePayload.inputVariables?.[0]?.category).toBe('flow');
    expect(updatePayload.inputVariables?.[0]?.sensitive).toBe(false);
  });
});
