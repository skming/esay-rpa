import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { AiMessage } from '../components/studio/ai-panel/aiPanelTypes';

const DEFAULT_MODEL = 'claude-sonnet-4-6';

type AiChatStore = {
  /** Chat message lists keyed by session key (e.g. "flow_<id>" or "local"). */
  sessions: Record<string, AiMessage[]>;
  /** Last-used model id — survives app restarts. */
  model: string;
  getMessages: (key: string) => AiMessage[];
  setMessages: (key: string, messages: AiMessage[]) => void;
  clearMessages: (key: string) => void;
  setModel: (model: string) => void;
};

export const useAiChatStore = create<AiChatStore>()(
  persist(
    (set, get) => ({
      sessions: {},
      model: DEFAULT_MODEL,
      getMessages: (key) => get().sessions[key] ?? [],
      setMessages: (key, messages) =>
        set((s) => ({ sessions: { ...s.sessions, [key]: messages } })),
      clearMessages: (key) =>
        set((s) => {
          const { [key]: _removed, ...rest } = s.sessions;
          return { sessions: rest };
        }),
      setModel: (model) => set({ model }),
    }),
    { name: 'rpa-studio.ai-chat' }
  )
);
