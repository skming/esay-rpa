import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type FlowListViewMode = 'card' | 'list';

type WorkspaceStore = {
  flowQuery: string;
  lastOpenedFlowId: string | null;
  navCollapsed: boolean;
  selectedFolder: string;
  viewMode: FlowListViewMode;
  setFlowQuery: (query: string) => void;
  setLastOpenedFlowId: (flowId: string | null) => void;
  setNavCollapsed: (collapsed: boolean) => void;
  setSelectedFolder: (folder: string) => void;
  setViewMode: (mode: FlowListViewMode) => void;
};

export const useWorkspaceStore = create<WorkspaceStore>()(
  persist(
    (set) => ({
      flowQuery: '',
      lastOpenedFlowId: null,
      navCollapsed: false,
      selectedFolder: '全部流程',
      viewMode: 'card',
      setFlowQuery: (flowQuery) => set({ flowQuery }),
      setLastOpenedFlowId: (lastOpenedFlowId) => set({ lastOpenedFlowId }),
      setNavCollapsed: (navCollapsed) => set({ navCollapsed }),
      setSelectedFolder: (selectedFolder) => set({ selectedFolder }),
      setViewMode: (viewMode) => set({ viewMode })
    }),
    {
      name: 'rpa-studio.workspace'
    }
  )
);
