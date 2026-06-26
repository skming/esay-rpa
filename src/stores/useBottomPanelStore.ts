import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import type { BottomTab, RunLogLevel } from '../types/rpa';

/** Pixel height bounds enforced when the user drags the panel resize handle. */
export const BOTTOM_PANEL_MIN_HEIGHT = 88;
export const BOTTOM_PANEL_DEFAULT_HEIGHT = 188;
export const BOTTOM_PANEL_MAX_HEIGHT = 320;

/** Log levels hidden by default — verbose execution noise most users don't need. */
const DEFAULT_HIDDEN_LOG_LEVELS: RunLogLevel[] = ['info', 'running'];

type BottomPanelStore = {
  activeTab: BottomTab;
  height: number;
  hiddenLogLevels: RunLogLevel[];
  open: boolean;
  setActiveTab: (tab: BottomTab) => void;
  setHeight: (height: number) => void;
  setOpen: (open: boolean) => void;
  toggleLogLevel: (level: RunLogLevel) => void;
  toggleOpen: () => void;
};

export const useBottomPanelStore = create<BottomPanelStore>()(
  persist<BottomPanelStore>(
    (set) => ({
      activeTab: 'logs',
      height: BOTTOM_PANEL_DEFAULT_HEIGHT,
      hiddenLogLevels: DEFAULT_HIDDEN_LOG_LEVELS,
      open: true,
      setActiveTab: (activeTab) => set({ activeTab }),
      setHeight: (height) => set({ height: clampHeight(height) }),
      setOpen: (open) => set({ open }),
      toggleLogLevel: (level) =>
        set((state) => ({
          hiddenLogLevels: state.hiddenLogLevels.includes(level)
            ? state.hiddenLogLevels.filter((l) => l !== level)
            : [...state.hiddenLogLevels, level]
        })),
      toggleOpen: () => set((state) => ({ open: !state.open }))
    }),
    {
      name: 'rpa-studio.bottom-panel'
    }
  )
);

function clampHeight(value: number): number {
  return Math.min(BOTTOM_PANEL_MAX_HEIGHT, Math.max(BOTTOM_PANEL_MIN_HEIGHT, value));
}
