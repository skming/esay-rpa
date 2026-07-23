import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import type { PanelTab } from '../types/rpa';

type PropertyPanelStore = {
  activeTab: PanelTab;
  collapsed: boolean;
  setActiveTab: (tab: PanelTab) => void;
  setCollapsed: (collapsed: boolean) => void;
};

export const usePropertyPanelStore = create<PropertyPanelStore>()(
  persist<PropertyPanelStore>(
    (set) => ({
      activeTab: 'config',
      collapsed: false,
      setActiveTab: (activeTab) => set({ activeTab }),
      setCollapsed: (collapsed) => set({ collapsed })
    }),
    {
      name: 'rpa-studio.property-panel'
    }
  )
);
