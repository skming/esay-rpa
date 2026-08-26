import type { ReactElement } from 'react';
import { Suspense, lazy } from 'react';

import { useStudioShortcuts } from '../hooks/useStudioShortcuts';
import type { AppRuntimeContext } from './appContext';

const StudioWorkspace = lazy(() =>
  import('../components/studio/StudioWorkspace').then((module) => ({ default: module.StudioWorkspace }))
);

// 快捷键挂在这个路由而不是 App：它们全部作用于画布（保存流程、撤销、删除选中节点、切断点），
// 挂在 App 上就是 7 个路由共享一份 window 监听——在运行历史或设置页按 Delete 会删掉画布上
// 一个看不见的节点、按 ⌘S 会静默保存一份用户以为自己没在编辑的流程。
export function StudioRoute({
  ai,
  bottomPanelOpen,
  canvas,
  electron,
  handleContextAction,
  inputVariables,
  setBottomPanelOpen,
}: AppRuntimeContext): ReactElement {
  useStudioShortcuts({
    onContextAction: handleContextAction,
    onDeleteEdge: canvas.deleteEdge,
    onFocusProperties: () => canvas.focusNode(canvas.selectedNodeId),
    onSave: () => void electron.saveFlow(),
    onRedo: canvas.redoAction,
    onSelectNode: canvas.setSelectedNodeId,
    onToggleAiPanel: () => ai.setAiPanelOpen(!ai.aiPanelOpen),
    onUndo: canvas.undoAction,
    selectedEdgeId: canvas.selectedEdgeId,
    selectedNodeId: canvas.selectedNodeId,
  });

  return (
    <Suspense fallback={<StudioWorkspaceFallback />}>
      <StudioWorkspace
        ai={ai}
        bottomPanelOpen={bottomPanelOpen}
        canvas={canvas}
        electron={electron}
        handleContextAction={handleContextAction}
        inputVariables={inputVariables}
        setBottomPanelOpen={setBottomPanelOpen}
      />
    </Suspense>
  );
}

function StudioWorkspaceFallback(): ReactElement {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center bg-slate-50 text-xs text-slate-500">
      加载工作台...
    </div>
  );
}
