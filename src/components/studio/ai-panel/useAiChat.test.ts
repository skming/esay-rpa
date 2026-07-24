import { describe, expect, it } from 'vitest';

import type { AiMessage, ToolCallState } from './aiPanelTypes';
import { cleanForStore, isPersistableMessage, patchToolCall } from './useAiChat';

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
});
