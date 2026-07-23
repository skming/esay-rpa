import type { useAiPanelState } from '../hooks/useAiPanelState';
import type { useElectronBridge } from '../hooks/useElectronBridge';
import type { useFlowCanvas } from '../hooks/useFlowCanvas';
import type { useFlowDraftAutosave } from '../hooks/useFlowDraftAutosave';
import type { ContextMenuAction, RuntimeVariable } from '../types/rpa';

export interface AppRuntimeContext {
  ai: ReturnType<typeof useAiPanelState>;
  bottomPanelOpen: boolean;
  canvas: ReturnType<typeof useFlowCanvas>;
  draftAutosave: ReturnType<typeof useFlowDraftAutosave>;
  electron: ReturnType<typeof useElectronBridge>;
  handleContextAction: (action: ContextMenuAction, nodeId: string) => void;
  inputVariables: RuntimeVariable[];
  setBottomPanelOpen: (open: boolean) => void;
}
