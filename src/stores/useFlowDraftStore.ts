import { create } from 'zustand';
import { persist } from 'zustand/middleware';

// 与 useFlowDraftAutosave 内部使用的结构保持一致
export type StoredFlowDraft = {
  schemaVersion: 1;
  savedAt: string;
  flowId: string | null;
  flowName: string;
  baseSignature: string;
  definition: Record<string, unknown>;
};

type FlowDraftStore = {
  draft: StoredFlowDraft | null;
  setDraft: (draft: StoredFlowDraft) => void;
  clearDraft: () => void;
  /** 草稿指向的流程已被删除时用。不能整份清掉：画布上的内容是用户未保存的工作 */
  detachFlowId: (flowId: string) => void;
};

export const useFlowDraftStore = create<FlowDraftStore>()(
  persist(
    (set) => ({
      draft: null,
      setDraft: (draft) => set({ draft }),
      clearDraft: () => set({ draft: null }),
      detachFlowId: (flowId) =>
        set((state) =>
          state.draft === null || state.draft.flowId !== flowId ? state : { draft: { ...state.draft, flowId: null } }
        ),
    }),
    { name: 'rpa-studio.flow-draft' }
  )
);
