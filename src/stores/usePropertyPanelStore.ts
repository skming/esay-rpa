import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import type { PanelTab, RpaNodeConfigDraft } from '../types/rpa';

export type PendingNodeDraft = { nodeId: string; draft: RpaNodeConfigDraft };

type PropertyPanelStore = {
  activeTab: PanelTab;
  collapsed: boolean;
  // 属性面板里改了但没点「保存修改」的草稿。运行前要先落到画布，否则跑的是编辑前的旧值
  pendingDraft: PendingNodeDraft | null;
  setActiveTab: (tab: PanelTab) => void;
  setCollapsed: (collapsed: boolean) => void;
  setPendingDraft: (pending: PendingNodeDraft | null) => void;
};

export const usePropertyPanelStore = create<PropertyPanelStore>()(
  persist<PropertyPanelStore>(
    (set) => ({
      activeTab: 'config',
      collapsed: false,
      pendingDraft: null,
      setActiveTab: (activeTab) => set({ activeTab }),
      setCollapsed: (collapsed) => set({ collapsed }),
      setPendingDraft: (pendingDraft) => set({ pendingDraft })
    }),
    {
      name: 'rpa-studio.property-panel',
      // 草稿是会话内状态：持久化会让重启后凭空多出一份指向已被改动过的节点的修改
      partialize: (state) => ({ activeTab: state.activeTab, collapsed: state.collapsed }) as PropertyPanelStore
    }
  )
);
