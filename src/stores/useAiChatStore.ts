import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { AiMessage } from '../components/studio/ai-panel/aiPanelTypes';

// 与后端 ai_config_service.DEFAULT_MODEL 保持一致；仍是这个值即代表用户没手动选过模型
export const DEFAULT_MODEL = 'claude-sonnet-5';

type AiChatStore = {
  // key 是按流程 ID 生成的会话标识（见 useAiChat.ts 的 sessionKey），不同流程互不共享对话历史
  sessions: Record<string, AiMessage[]>;
  model: string;
  getMessages: (key: string) => AiMessage[];
  setMessages: (key: string, messages: AiMessage[]) => void;
  clearMessages: (key: string) => void;
  migrateSession: (fromKey: string, toKey: string) => void;
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
      // 草稿存成流程后会话 key 会变，不搬的话这段对话在界面上直接消失
      migrateSession: (fromKey, toKey) =>
        set((s) => {
          const source = s.sessions[fromKey];
          // 目标已有对话就不动：那是这个流程自己的历史，覆盖等于拿草稿把正文冲掉
          if (source === undefined || source.length === 0 || (s.sessions[toKey]?.length ?? 0) > 0) return s;
          const { [fromKey]: _moved, ...rest } = s.sessions;
          return { sessions: { ...rest, [toKey]: source } };
        }),
      setModel: (model) => set({ model }),
    }),
    { name: 'rpa-studio.ai-chat' }
  )
);
