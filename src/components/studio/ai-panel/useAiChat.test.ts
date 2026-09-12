import { describe, expect, it, vi } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { backend } from '../../../lib/backendClient';
import { useAiChatStore } from '../../../stores/useAiChatStore';

import type { AiMessage, ToolCallState } from './aiPanelTypes';
import { cleanForStore, isPersistableMessage, patchToolCall, useAiChat } from './useAiChat';

function msg(overrides: Partial<AiMessage>): AiMessage {
  return { id: 'm1', role: 'assistant', content: '', createdAt: 1, ...overrides };
}

describe('isPersistableMessage', () => {
  it('用户消息始终保留', () => {
    expect(isPersistableMessage(msg({ role: 'user', content: '' }))).toBe(true);
  });

  it('只有 toolCalls、正文为空的 agent 回合也要保留（回归：重开助手丢失 agent 输出）', () => {
    const agentTurn = msg({
      toolCalls: [{ id: 't1', tool: 'create_flow', args: '{}', status: 'done' }],
    });
    expect(isPersistableMessage(agentTurn)).toBe(true);
  });

  it('只有 error 的回合也要保留（断流时正文为空，丢掉历史里只剩用户提问）', () => {
    expect(isPersistableMessage(msg({ error: '连接中断' }))).toBe(true);
  });

  it('既无正文也无 toolCalls 的空 assistant 消息丢弃', () => {
    expect(isPersistableMessage(msg({ content: '  ' }))).toBe(false);
  });
});

describe('patchToolCall', () => {
  const parallel: ToolCallState[] = [
    { id: 'r0_0', tool: 'inspect_page', args: '{"url":"a"}', status: 'running' },
    { id: 'r0_1', tool: 'inspect_page', args: '{"url":"b"}', status: 'running' },
  ];

  it('同轮并行调用同一工具时，结果按 call_id 落到对应卡片（回归：第二张永远转圈）', () => {
    const patched = patchToolCall(parallel, 'r0_1', { result: { ok: true }, status: 'done' });
    expect(patched.map((tc) => tc.status)).toEqual(['running', 'done']);
    expect(patched[1].result).toEqual({ ok: true });
    // 未命中的卡片保持原引用，避免无谓重渲染
    expect(patched[0]).toBe(parallel[0]);
  });

  it('call_id 对不上时原样返回，不误改任何卡片', () => {
    expect(patchToolCall(parallel, 'r9_9', { status: 'done' })).toBe(parallel);
    expect(patchToolCall(undefined, 'r0_0', { status: 'done' })).toEqual([]);
  });
});

describe('cleanForStore', () => {
  it('剥离流式临时字段，running 工具卡片降级为 stopped 防止重载后永远转圈', () => {
    const [cleaned] = cleanForStore([
      msg({
        content: '进行中',
        reasoning: '推理…',
        statusText: '正在调用工具',
        toolCalls: [
          { id: 't1', tool: 'lint_flow', args: '{}', status: 'running' },
          { id: 't2', tool: 'create_flow', args: '{}', status: 'done' },
        ],
      }),
    ]);
    expect(cleaned.reasoning).toBeUndefined();
    expect(cleaned.statusText).toBeUndefined();
    expect(cleaned.toolCalls?.map((tc) => tc.status)).toEqual(['stopped', 'done']);
  });

  it('过滤规则与 isPersistableMessage 一致', () => {
    const kept = cleanForStore([
      msg({ id: 'u', role: 'user', content: '你好' }),
      msg({ id: 'empty' }),
      msg({ id: 'tools', toolCalls: [{ id: 't', tool: 'run_flow', args: '', status: 'done' }] }),
    ]);
    expect(kept.map((m) => m.id)).toEqual(['u', 'tools']);
  });

  it('保留后端给出的流程验证状态，重开面板后仍能识别证据等级', () => {
    const [cleaned] = cleanForStore([
      msg({
        content: '运行完成',
        verificationStatus: 'run_verified',
        verificationRevision: 7,
      }),
    ]);
    expect(cleaned.verificationStatus).toBe('run_verified');
    expect(cleaned.verificationRevision).toBe(7);
  });
});


it('等待模型响应期间需求已按草稿ID保存', async () => {
  let resolveResponse!: (response: Response) => void;
  const stream = vi.spyOn(backend, 'streamAiChat').mockImplementation(() => new Promise((resolve) => { resolveResponse = resolve; }));
  const save = vi.spyOn(backend, 'saveAiChat').mockResolvedValue(undefined);
  let chat!: ReturnType<typeof useAiChat>;
  function Probe() {
    chat = useAiChat('persisted-draft');
    return null;
  }
  try {
    renderToStaticMarkup(createElement(Probe));
    const sending = chat.send('抓取帖子内容');
    expect(useAiChatStore.getState().getMessages('flow_persisted-draft')).toEqual([
      expect.objectContaining({ role: 'user', content: '抓取帖子内容' }),
    ]);
    await Promise.resolve();
    expect(save).toHaveBeenCalledWith('flow_persisted-draft', expect.any(Array));
    resolveResponse(new Response(null));
    await sending;
  } finally {
    stream.mockRestore();
    save.mockRestore();
    useAiChatStore.getState().clearMessages('flow_persisted-draft');
  }
});


it('中断错误经过保存和恢复后仍保留，不变成空回复', () => {
  const interrupted = msg({ error: '连接中断，请继续', content: '' });
  const saved = cleanForStore([interrupted]);
  expect(saved[0].error).toBe('连接中断，请继续');
  expect(cleanForStore(saved)).toEqual(saved);
});


it('清空等待已开始的保存完成，并丢弃尚未开始的旧保存', async () => {
  let release!: () => void;
  const order: string[] = [];
  const save = vi.spyOn(backend, 'saveAiChat').mockImplementation(async () => {
    order.push('save');
    await new Promise<void>((resolve) => { release = resolve; });
  });
  const remove = vi.spyOn(backend, 'deleteAiChat').mockImplementation(async () => { order.push('delete'); });
  const stream = vi.spyOn(backend, 'streamAiChat').mockResolvedValue(new Response(null));
  let chat!: ReturnType<typeof useAiChat>;
  function Probe() { chat = useAiChat('clear-test'); return null; }
  try {
    renderToStaticMarkup(createElement(Probe));
    await chat.send('第一条');
    await chat.send('第二条');
    chat.clearMessages();
    expect(order).toEqual(['save']);
    expect(useAiChatStore.getState().getMessages('flow_clear-test')).toEqual([]);
    release();
    await vi.waitFor(() => expect(order).toEqual(['save', 'delete']));
  } finally {
    save.mockRestore(); remove.mockRestore(); stream.mockRestore();
    useAiChatStore.getState().clearMessages('flow_clear-test');
  }
});
