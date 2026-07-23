import { describe, expect, it } from 'vitest';

import { buildFlowDefinition } from '../lib/flowDefinition';
import { createFlowNode } from '../lib/flowOperations';
import type { StoredFlowDraft } from '../stores/useFlowDraftStore';
import { readRestorableDraft } from './useFlowDraftAutosave';

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
