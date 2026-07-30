import { beforeEach, describe, expect, it } from 'vitest';

import { useAiChatStore } from './useAiChatStore';
import type { AiMessage } from '../components/studio/ai-panel/aiPanelTypes';

function message(id: string, content: string): AiMessage {
  return { id, role: 'user', content } as AiMessage;
}

describe('useAiChatStore.migrateSession', () => {
  beforeEach(() => {
    useAiChatStore.setState({ sessions: {} });
  });

  it('把草稿会话搬到保存后的流程 key 上', () => {
    const store = useAiChatStore.getState();
    store.setMessages('flow_local-1', [message('m1', '抓取这个帖子')]);

    store.migrateSession('flow_local-1', 'flow_db38cff1');

    expect(useAiChatStore.getState().getMessages('flow_db38cff1').map((m) => m.content)).toEqual(['抓取这个帖子']);
    expect(useAiChatStore.getState().getMessages('flow_local-1')).toEqual([]);
  });

  it('目标已有对话时按消息 id 合并', () => {
    const store = useAiChatStore.getState();
    store.setMessages('flow_local-1', [message('draft', '草稿')]);
    store.setMessages('flow_real', [message('kept', '正文')]);

    store.migrateSession('flow_local-1', 'flow_real');

    expect(useAiChatStore.getState().getMessages('flow_real').map((m) => m.content)).toEqual(['草稿', '正文']);
    expect(useAiChatStore.getState().getMessages('flow_local-1')).toEqual([]);
  });
});
