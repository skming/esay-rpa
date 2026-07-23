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
      // 故意只持久化 open：mode（sidebar/float）每次启动都应回到默认布局，不跨会话保留
      partialize: (state) => ({ open: state.open }) as AiPanelStore,
    }
  )
);
