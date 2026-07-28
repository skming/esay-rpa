import type { Node } from '@xyflow/react';

import { applyNodeConfigDraft } from './nodeConfigDraft';
import type { PendingNodeDraft } from '../stores/usePropertyPanelStore';
import type { RpaNodeData } from '../types/rpa';

/** 把属性面板里未点保存的草稿写进节点数组；没有草稿或草稿指向已删除的节点时原样返回。
 *
 * 返回原数组（而不是等值新数组）让调用方能用引用相等判断「什么都没变」，省掉一次画布重渲染。
 */
export function applyPendingDraftToNodes(
  nodes: Node<RpaNodeData>[],
  pendingDraft: PendingNodeDraft | null
): Node<RpaNodeData>[] {
  if (pendingDraft === null || !nodes.some((node) => node.id === pendingDraft.nodeId)) {
    return nodes;
  }
  return nodes.map((node) =>
    node.id === pendingDraft.nodeId ? { ...node, data: applyNodeConfigDraft(node.data, pendingDraft.draft) } : node
  );
}
