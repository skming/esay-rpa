import type { Node } from '@xyflow/react';
import type { ReactElement } from 'react';
import { useMemo } from 'react';

import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import { applyNodeConfigDraft } from '../../../lib/nodeConfigDraft';
import { mergeRuntimeVariables } from '../../../lib/runtimeVariables';
import type { PanelTab, RpaNodeConfigDraft, RpaNodeData, RuntimeVariable } from '../../../types/rpa';
import { AdvancedTab } from './AdvancedTab';
import { ConfigTab } from './ConfigTab';
import { InputOutputTab } from './InputOutputTab';
import { NodeExecutionSummary } from './NodeExecutionSummary';
import { NodeValidationSummary } from './NodeValidationSummary';
import { NodeSummary } from './NodeSummary';

export function PropertyContent({
  activeTab,
  draft,
  electron,
  flowEdges,
  flowNodes,
  inputVariables,
  node,
  onDraftChange
}: {
  activeTab: PanelTab;
  draft: RpaNodeConfigDraft;
  electron: ElectronBridgeState;
  flowEdges: import('@xyflow/react').Edge[];
  flowNodes: Node<RpaNodeData>[];
  inputVariables: RuntimeVariable[];
  node: Node<RpaNodeData>;
  onDraftChange: (draft: RpaNodeConfigDraft) => void;
}): ReactElement {
  // 从 browser.open/tab.open 推断目标 URL 供选择器联想；含未解析占位符或非 http(s) 时返回 undefined
  const flowTargetUrl = useMemo<string | undefined>(() => {
    const openNode = flowNodes.find((n) => n.data.action?.type === 'browser.open' || n.data.action?.type === 'browser.tab.open');
    const url = openNode?.data.action?.targetUrl ?? openNode?.data.action?.url;
    if (typeof url !== 'string' || url.trim().length === 0) return undefined;
    const resolved = url.trim().replace(/\$\{var\.([^}]+)\}/g, (_, name: string) => {
      const variable = inputVariables.find((v) => v.name === name);
      return typeof variable?.value === 'string' ? variable.value : `\${var.${name}}`;
    });
    if (resolved.includes('${') || !/^https?:\/\//i.test(resolved)) return undefined;
    return resolved;
  }, [flowNodes, inputVariables]);

  // 把未保存的 draft 叠加到节点上，用于校验/执行摘要提前反映编辑中的效果，不代表已持久化的节点数据
  const previewNode = useMemo<Node<RpaNodeData>>(
    () => ({
      ...node,
      data: applyNodeConfigDraft(node.data, draft)
    }),
    [draft, node]
  );
  const previewNodes = useMemo(
    () => flowNodes.map((item) => (item.id === previewNode.id ? previewNode : item)),
    [flowNodes, previewNode]
  );
  const availableVariables = useMemo(() => mergeRuntimeVariables(inputVariables, electron.variables), [electron.variables, inputVariables]);

  const isStartEnd = node.id === 'start' || node.id === 'end';

  return (
    <>
      <NodeSummary node={node} />
      <NodeValidationSummary flowEdges={flowEdges} flowNodes={previewNodes} inputVariables={inputVariables} node={previewNode} runtimeVariables={electron.variables} />
      <NodeExecutionSummary node={previewNode} />
      {activeTab === 'advanced' ? (
        <AdvancedTab electron={electron} />
      ) : isStartEnd ? (
        <div className="px-4 py-6 text-center text-[11px] text-slate-500">
          {node.id === 'start' ? '流程开始节点，无需配置参数' : '流程结束节点，无需配置参数'}
        </div>
      ) : (
        <>
          {activeTab === 'config' && <ConfigTab draft={draft} electron={electron} flowTargetUrl={flowTargetUrl} node={node} onDraftChange={onDraftChange} />}
          {activeTab === 'io' && <InputOutputTab flowNodes={previewNodes} node={previewNode} variables={availableVariables} />}
        </>
      )}
    </>
  );
}
