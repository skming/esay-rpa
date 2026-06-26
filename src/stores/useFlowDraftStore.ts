import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/** Mirrors the internal shape used by useFlowDraftAutosave. */
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
};

export const useFlowDraftStore = create<FlowDraftStore>()(
  persist(
    (set) => ({
      draft: null,
      setDraft: (draft) => set({ draft }),
      clearDraft: () => set({ draft: null }),
    }),
    { name: 'rpa-studio.flow-draft' }
  )
);
