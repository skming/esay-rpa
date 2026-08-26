import type { ReactElement } from 'react';
import { useCallback, useEffect } from 'react';
import { HashRouter } from 'react-router-dom';
import type { Node } from '@xyflow/react';

import { AppShell } from './app/AppShell';
import { TooltipProvider } from './components/ui/tooltip';
import { useAiPanelState } from './hooks/useAiPanelState';
import { useElectronBridge } from './hooks/useElectronBridge';
import { useFlowCanvas } from './hooks/useFlowCanvas';
import { useFlowDraftAutosave } from './hooks/useFlowDraftAutosave';
import { insertNodeAfter, insertNodeBefore, type ComponentDragPayload } from './lib/flowOperations';
import { useBottomPanelStore } from './stores/useBottomPanelStore';
import { useFlowVariableStore } from './stores/useFlowVariableStore';
import { useWorkspaceStore } from './stores/useWorkspaceStore';
import type { ContextMenuAction, RpaNodeData } from './types/rpa';

export default function App(): ReactElement {
  const canvas = useFlowCanvas();
  const ai = useAiPanelState();

  const inputVariables = useFlowVariableStore((state) => state.inputVariables);
  const bottomPanelOpen = useBottomPanelStore((state: ReturnType<typeof useBottomPanelStore.getState>) => state.open);
  const setBottomPanelOpen = useBottomPanelStore((state: ReturnType<typeof useBottomPanelStore.getState>) => state.setOpen);
  const lastOpenedFlowId = useWorkspaceStore((state) => state.lastOpenedFlowId);

  const electron = useElectronBridge({
    edges: canvas.flowEdges,
    nodes: canvas.flowNodes,
    setEdges: canvas.setFlowEdges,
    setNodes: canvas.setFlowNodes,
    setSelectedNodeId: canvas.setSelectedNodeId,
  });

  const draftAutosave = useFlowDraftAutosave({
    currentFlow: electron.currentFlow,
    edges: canvas.flowEdges,
    inputVariables,
    nodes: canvas.flowNodes,
    setEdges: canvas.setFlowEdges,
    setNodes: canvas.setFlowNodes,
  });

  useEffect(() => {
    if (!draftAutosave.hydrated) return;
    if (electron.currentFlow !== null) return;
    if (draftAutosave.restoredFlowId) {
      // 草稿已恢复画布，只需补 currentFlow 元数据
      void electron.silentlyRestoreCurrentFlow(draftAutosave.restoredFlowId);
    } else if (lastOpenedFlowId) {
      // 无草稿时需同时恢复 currentFlow 和画布节点
      void electron.silentlyRestoreCurrentFlow(lastOpenedFlowId, { restoreCanvas: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftAutosave.hydrated]);

  const handleContextAction = useCallback(
    (action: ContextMenuAction, nodeId: string): void => {
      if (action === 'delete') { canvas.requestDeleteNode(nodeId); return; }

      if (action === 'run-from-here') {
        const source = canvas.flowNodes.find((node) => node.id === nodeId);
        if (source === undefined || source.id === 'start' || source.id === 'end') return;
        canvas.setSelectedNodeId(nodeId);
        void electron.startRun({ mode: 'run', scope: 'from-selection', startNodeId: nodeId });
        return;
      }

      if (action === 'disable') {
        canvas.setFlowNodes((nodes) =>
          nodes.map((node) =>
            node.id === nodeId
              ? { ...node, data: { ...node.data, disabled: !node.data.disabled, status: node.data.disabled ? 'pending' : 'skipped' } }
              : node
          )
        );
        return;
      }

      if (action === 'breakpoint') {
        if (nodeId === 'start' || nodeId === 'end') return;
        canvas.setFlowNodes((nodes) =>
          nodes.map((node) => (node.id === nodeId ? { ...node, data: { ...node.data, breakpoint: !node.data.breakpoint } } : node))
        );
        return;
      }

      if (action === 'duplicate') {
        const source = canvas.flowNodes.find((node) => node.id === nodeId);
        if (source === undefined || source.id === 'start' || source.id === 'end') return;
        const clone: Node<RpaNodeData> = {
          ...source,
          id: `n_${crypto.randomUUID()}`,
          position: { x: source.position.x + 30, y: source.position.y + 30 },
          selected: true,
        };
        canvas.setFlowNodes((nodes) => [...nodes, clone]);
        canvas.setSelectedNodeId(clone.id);
        return;
      }

      if (action === 'insert-before' || action === 'insert-after') {
        const payload: ComponentDragPayload = { label: '打开网页', nodeType: 'browser' };
        const insertion = action === 'insert-before'
          ? insertNodeBefore(canvas.flowNodes, canvas.flowEdges, nodeId, payload)
          : insertNodeAfter(canvas.flowNodes, canvas.flowEdges, nodeId, payload);
        if (insertion !== null) {
          canvas.setFlowNodes((nodes) => [...nodes, insertion.node]);
          canvas.setFlowEdges(insertion.edges);
          canvas.setSelectedNodeId(insertion.node.id);
        }
        return;
      }

      canvas.focusNode(nodeId);
    },
    [canvas, electron]
  );

  return (
    <TooltipProvider delayDuration={400}>
      <HashRouter>
        <AppShell
          ai={ai}
          bottomPanelOpen={bottomPanelOpen}
          canvas={canvas}
          draftAutosave={draftAutosave}
          electron={electron}
          handleContextAction={handleContextAction}
          inputVariables={inputVariables}
          setBottomPanelOpen={setBottomPanelOpen}
        />
      </HashRouter>
    </TooltipProvider>
  );
}
