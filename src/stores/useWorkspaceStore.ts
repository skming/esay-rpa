import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type FlowListViewMode = 'card' | 'list';

type WorkspaceStore = {
  flowQuery: string;
  navCollapsed: boolean;
  selectedFolder: string;
  viewMode: FlowListViewMode;
  setFlowQuery: (query: string) => void;
  setNavCollapsed: (collapsed: boolean) => void;
  setSelectedFolder: (folder: string) => void;
  setViewMode: (mode: FlowListViewMode) => void;
};

export const useWorkspaceStore = create<WorkspaceStore>()(
  persist(
    (set) => ({
      flowQuery: '',
      navCollapsed: false,
      selectedFolder: '全部流程',
      viewMode: 'card',
      setFlowQuery: (flowQuery) => set({ flowQuery }),
      setNavCollapsed: (navCollapsed) => set({ navCollapsed }),
      setSelectedFolder: (selectedFolder) => set({ selectedFolder }),
      setViewMode: (viewMode) => set({ viewMode })
    }),
    {
      name: 'rpa-studio.workspace'
    }
  )
);
