import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type WorkspaceStore = {
  lastOpenedFlowId: string | null;
  navCollapsed: boolean;
  setLastOpenedFlowId: (flowId: string | null) => void;
  setNavCollapsed: (collapsed: boolean) => void;
};

export const useWorkspaceStore = create<WorkspaceStore>()(
  persist(
    (set) => ({
      lastOpenedFlowId: null,
      navCollapsed: false,
      setLastOpenedFlowId: (lastOpenedFlowId) => set({ lastOpenedFlowId }),
      setNavCollapsed: (navCollapsed) => set({ navCollapsed }),
    }),
    {
      name: 'rpa-studio.workspace'
    }
  )
);
