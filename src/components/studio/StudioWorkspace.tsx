import type { ReactElement } from 'react';
import { useMemo } from 'react';
import type { useFlowCanvas } from '../../hooks/useFlowCanvas';
import type { useAiPanelState } from '../../hooks/useAiPanelState';
import type { useElectronBridge } from '../../hooks/useElectronBridge';
import type { ContextMenuAction, RuntimeVariable } from '../../types/rpa';
import { cn } from '../../lib/utils';
import { AiPanel } from './ai-panel/AiPanel';
import { BottomPanel } from './BottomPanel';
import { ComponentLibrary } from './ComponentLibrary';
import { FlowCanvas } from './FlowCanvas';
import { PropertyPanel } from './PropertyPanel';

interface StudioWorkspaceProps {
  canvas: ReturnType<typeof useFlowCanvas>;
  ai: ReturnType<typeof useAiPanelState>;
  electron: ReturnType<typeof useElectronBridge>;
  inputVariables: RuntimeVariable[];
  bottomPanelOpen: boolean;
  setBottomPanelOpen: (open: boolean) => void;
  handleContextAction: (action: ContextMenuAction, nodeId: string) => void;
}

export function StudioWorkspace({
  canvas,
  ai,
  electron,
  inputVariables,
  bottomPanelOpen,
  setBottomPanelOpen,
  handleContextAction,
}: StudioWorkspaceProps): ReactElement {
  const aiNodeLookup = useMemo(
    () => Object.fromEntries(
      canvas.flowNodes.map((node) => [
        node.id,
        {
          id: node.id,
          title: typeof node.data?.title === 'string' ? node.data.title : node.id,
          type: typeof node.data?.action?.type === 'string' ? node.data.action.type : undefined,
        },
      ])
    ),
    [canvas.flowNodes]
  );

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <ComponentLibrary onQuickAdd={canvas.addNodeAfterSelection} />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <FlowCanvas
          aiPanelOpen={ai.aiPanelOpen}
          onAddNode={canvas.addNodeAtPosition}
          bottomPanelOpen={bottomPanelOpen}
          focusNodeRequest={canvas.focusNodeRequest}
          hasMissingStartEnd={canvas.hasMissingStartEnd}
          onConnectNodes={canvas.connectNodes}
          onContextAction={handleContextAction}
          onRestoreStartEnd={canvas.restoreStartEnd}
          flowEdges={canvas.flowEdges}
          flowNodes={canvas.flowNodes}
          inputVariables={inputVariables}
          nodeStates={electron.nodeStates}
          onEdgesChange={canvas.onEdgesChange}
          onNodesChange={canvas.onNodesChange}
          onSelectedNodeChange={canvas.setSelectedNodeId}
          onToggleAiPanel={() => ai.setAiPanelOpen(!ai.aiPanelOpen)}
          onToggleBottomPanel={() => setBottomPanelOpen(!bottomPanelOpen)}
          progress={electron.progress}
          selectedNodeId={canvas.selectedNodeId}
        />
        {/* BottomPanel: 始终挂载，用 grid-rows 动画展开/收起，避免状态丢失 */}
        <div
          className={cn(
            'grid shrink-0 transition-[grid-template-rows] duration-200 ease-in-out',
            bottomPanelOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
          )}
        >
          <div className="overflow-hidden">
            <BottomPanel
              electron={electron}
              flowNodes={canvas.flowNodes}
              onAiAnalyze={(taskId) => {
                ai.setAiPanelOpen(true);
                ai.setAiPendingMessage(`刚才运行失败了，任务 ID（task_id）是 ${taskId}，请调用 get_run_error 查看错误详情，定位失败节点，然后修复当前流程`);
              }}
              onBreakpointChange={canvas.updateNodeBreakpoint}
              onClose={() => setBottomPanelOpen(false)}
              onSelectedNodeChange={canvas.focusNode}
            />
          </div>
        </div>
      </div>
      <PropertyPanel
        electron={electron}
        flowEdges={canvas.flowEdges}
        flowNodes={canvas.flowNodes}
        inputVariables={inputVariables}
        onUpdateNodeData={canvas.updateNodeData}
        selectedNode={canvas.selectedNode}
      />
      {/* AiPanel: 始终挂载以保留对话历史，通过 open prop 控制动画显隐 */}
      <AiPanel
        flowId={electron.currentFlow?.flowId ?? null}
        onClose={ai.closePanel}
        onModeChange={ai.setAiPanelMode}
        onApplySuccess={(fid) => {
          // Same flow updated (e.g. update_flow / apply_node_fix): use lightweight refresh
          // to avoid clearing run logs and showing a confusing "已打开" toast.
          // Different flow (create_flow): full open to switch context.
          if (fid === electron.currentFlow?.flowId) {
            void electron.applyAiFlowUpdate(fid);
          } else {
            void electron.openFlowById(fid);
          }
        }}
        nodeLookup={aiNodeLookup}
        onFocusNode={canvas.focusNode}
        open={ai.aiPanelOpen}
        pendingMessage={ai.aiPendingMessage}
        onClearPendingMessage={() => ai.setAiPendingMessage(null)}
      />
    </div>
  );
}
