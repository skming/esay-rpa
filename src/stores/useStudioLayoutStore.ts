import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/** 进入专注模式前的面板状态，退出时原样还原；为 null 表示当前不在专注模式。 */
export type FocusSnapshot = {
  aiPanelOpen: boolean;
  bottomPanelOpen: boolean;
  libraryCollapsed: boolean;
  propertyCollapsed: boolean;
};

type StudioLayoutStore = {
  focusSnapshot: FocusSnapshot | null;
  libraryCollapsed: boolean;
  setFocusSnapshot: (snapshot: FocusSnapshot | null) => void;
  setLibraryCollapsed: (collapsed: boolean) => void;
};

export const useStudioLayoutStore = create<StudioLayoutStore>()(
  persist(
    (set) => ({
      focusSnapshot: null,
      libraryCollapsed: false,
      setFocusSnapshot: (focusSnapshot) => set({ focusSnapshot }),
      setLibraryCollapsed: (libraryCollapsed) => set({ libraryCollapsed })
    }),
    {
      name: 'rpa-studio.studio-layout',
      // 专注模式不跨会话保留：重启后带着一个收起全部面板的界面回来会让人以为面板丢了
      partialize: (state) => ({ libraryCollapsed: state.libraryCollapsed }) as StudioLayoutStore
    }
  )
);
