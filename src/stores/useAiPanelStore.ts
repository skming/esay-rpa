import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type AiPanelStore = {
  open: boolean;
  mode: 'sidebar' | 'float';
  /** 助手是否正在处理请求。面板关掉后对话仍在跑，没有这个信号，悬浮球看上去和空闲时一模一样。 */
  busy: boolean;
  setOpen: (open: boolean) => void;
  setMode: (mode: 'sidebar' | 'float') => void;
  setBusy: (busy: boolean) => void;
  close: () => void;
};

export const useAiPanelStore = create<AiPanelStore>()(
  persist<AiPanelStore>(
    (set) => ({
      open: false,
      mode: 'sidebar',
      busy: false,
      setOpen: (open) => set({ open }),
      setMode: (mode) => set({ mode }),
      setBusy: (busy) => set({ busy }),
      close: () => set({ open: false, mode: 'sidebar' }),
    }),
    {
      name: 'rpa-studio.ai-panel',
      // 故意只持久化 open：mode（sidebar/float）每次启动都应回到默认布局，不跨会话保留
      partialize: (state) => ({ open: state.open }) as AiPanelStore,
    }
  )
);
