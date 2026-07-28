import { describe, expect, it } from 'vitest';
import type { Node } from '@xyflow/react';

import { createNodeConfigDraft } from './nodeConfigDraft';
import { applyPendingDraftToNodes } from './pendingNodeDraft';
import type { RpaNodeData } from '../types/rpa';

function openNode(targetUrl: string): Node<RpaNodeData> {
  return {
    id: 'n1_open',
    position: { x: 0, y: 0 },
    data: {
      title: '打开首页',
      description: '',
      kind: 'browser',
      status: 'pending',
      action: { type: 'browser.open', targetUrl, timeoutMs: 30_000 }
    }
  };
}

describe('pendingNodeDraft', () => {
  it('运行前把面板里未保存的草稿落到节点上', () => {
    // 面板改了地址没点保存就点运行：跑的必须是面板里显示的地址，否则结果 success 但内容是旧站点的
    const nodes = [openNode('https://www.v2ex.com/')];
    const draft = { ...createNodeConfigDraft(nodes[0].data), targetUrl: 'https://www.v2ex.com/changes' };

    const next = applyPendingDraftToNodes(nodes, { nodeId: 'n1_open', draft });

    expect(next[0].data.action?.targetUrl).toBe('https://www.v2ex.com/changes');
    expect(nodes[0].data.action?.targetUrl).toBe('https://www.v2ex.com/');
  });

  it('草稿指向已删除的节点时原样返回', () => {
    // 返回同一个引用，调用方据此跳过画布更新
    const nodes = [openNode('https://www.v2ex.com/')];
    const draft = createNodeConfigDraft(nodes[0].data);

    expect(applyPendingDraftToNodes(nodes, { nodeId: '已删除的节点', draft })).toBe(nodes);
    expect(applyPendingDraftToNodes(nodes, null)).toBe(nodes);
  });
});
