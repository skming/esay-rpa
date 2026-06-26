import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type AiPanelStore = {
  open: boolean;
  mode: 'sidebar' | 'float';
  setOpen: (open: boolean) => void;
  setMode: (mode: 'sidebar' | 'float') => void;
  close: () => void;
};

export const useAiPanelStore = create<AiPanelStore>()(
  persist<AiPanelStore>(
    (set) => ({
      open: false,
      mode: 'sidebar',
      setOpen: (open) => set({ open }),
      setMode: (mode) => set({ mode }),
      close: () => set({ open: false, mode: 'sidebar' }),
    }),
    {
      name: 'rpa-studio.ai-panel',
      partialize: (state) => ({ open: state.open }) as AiPanelStore,
    }
  )
);
