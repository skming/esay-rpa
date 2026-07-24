import { useCallback, useEffect, useRef, useState } from 'react';
import { DEFAULT_MODEL, useAiChatStore } from '../../../stores/useAiChatStore';
import type { AiAttachment, AiMessage, FlowDiff, ToolCallState } from './aiPanelTypes';
import { backend } from '../../../lib/backendClient';

function nanoid(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

// guard 拦截返回的是 status 以 `blocked_` 开头的正常结果，渲染成绿色 done 会让拦截完全不可见
function isBlockedResult(result: unknown): boolean {
  if (result === null || typeof result !== 'object') return false;
  const status = (result as Record<string, unknown>).status;
  return typeof status === 'string' && status.startsWith('blocked_');
}

// 同一轮并行调用同一个工具时，按工具名匹配会把两次结果都盖到第一张卡片上，
// 第二张永远转圈。改按后端下发的 call_id 定位。
export function patchToolCall(
  toolCalls: ToolCallState[] | undefined,
  callId: string | undefined,
  patch: Partial<ToolCallState>
): ToolCallState[] {
  const list = toolCalls ?? [];
  const target = list.findIndex((tc) => tc.id === callId);
  if (target === -1) return list;
  const next = [...list];
  next[target] = { ...next[target], ...patch };
  return next;
}

// 流式中的增量落盘间隔：一轮可跑几分钟，只在收尾写盘则中途退出后磁盘上只剩用户提问
const STREAM_PERSIST_INTERVAL_MS = 5_000;

function sessionKey(flowId: string | null): string {
  return flowId ? `flow_${flowId}` : 'local';
}

async function backendLoad(key: string): Promise<AiMessage[]> {
  try {
    const { messages } = await backend.getAiChat<AiMessage>(key);
    return Array.isArray(messages) ? messages : [];
  } catch {
    return [];
  }
}

// 有 toolCalls 但正文为空的回合也要保留：只看 content 会在流式中途的保存里丢掉整个回合
export function isPersistableMessage(m: AiMessage): boolean {
  if (m.role === 'user') return true;
  // error 也算内容：断流/请求失败的回合正文是空的，丢掉的话历史里只剩用户那条提问
  return m.role === 'assistant'
    && (m.content.trim() !== '' || (m.toolCalls?.length ?? 0) > 0 || (m.error ?? '') !== '');
}

async function backendSave(key: string, messages: AiMessage[]): Promise<void> {
  try {
    await backend.saveAiChat(key, messages.filter(isPersistableMessage));
  } catch { /* fire-and-forget；本地 store 仍持有消息，失败不丢数据 */ }
}

async function backendDelete(key: string): Promise<void> {
  try {
    await backend.deleteAiChat(key);
  } catch { /* ignore */ }
}

function formatToolProgress(
  elapsedSeconds?: number,
  progress?: { current_step?: number; total_steps?: number; percent?: number } | null
): string | undefined {
  const parts: string[] = [];
  if (typeof elapsedSeconds === 'number' && elapsedSeconds > 0) {
    const minutes = Math.floor(elapsedSeconds / 60);
    parts.push(minutes > 0 ? `已用时 ${minutes} 分 ${elapsedSeconds % 60} 秒` : `已用时 ${elapsedSeconds} 秒`);
  }
  if (progress && typeof progress.current_step === 'number' && typeof progress.total_steps === 'number') {
    parts.push(`第 ${progress.current_step}/${progress.total_steps} 步`);
  }
  return parts.length > 0 ? parts.join(' · ') : undefined;
}

export function cleanForStore(messages: AiMessage[]): AiMessage[] {
  return messages
    .filter(isPersistableMessage)
    // 剥离流式临时字段；running 降级为 stopped，否则重载后工具卡片永远转圈
    .map(({ diffPreview: _dp, reasoning: _r, statusText: _s, statusDetail: _sd, error: _e, ...rest }) => ({
      ...rest,
      toolCalls: rest.toolCalls?.map((tc) =>
        tc.status === 'running' ? { ...tc, status: 'stopped' as const } : tc
      ),
    }));
}

export function useAiChat(flowId: string | null, onFlowChanged?: (flowId: string) => void) {
  const key = sessionKey(flowId);

  const storeGetMessages = useAiChatStore((s) => s.getMessages);
  const storeSetMessages = useAiChatStore((s) => s.setMessages);
  const storeClearMessages = useAiChatStore((s) => s.clearMessages);
  const storeModel = useAiChatStore((s) => s.model);
  const storeSetModel = useAiChatStore((s) => s.setModel);

  const [messages, setMessages] = useState<AiMessage[]>(() => storeGetMessages(key));
  const [pending, setPending] = useState(false);
  const [model, setModelLocal] = useState<string>(storeModel);

  const abortRef = useRef<AbortController | null>(null);
  const saveInFlightRef = useRef(false);
  // 供长生命周期异步闭包读取最新值，避免 stale capture
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const onFlowChangedRef = useRef(onFlowChanged);
  onFlowChangedRef.current = onFlowChanged;
  const aiCreatedFlowRef = useRef(false);
  const persistAfterCommitRef = useRef(false);
  const lastStreamPersistRef = useRef(0);
  const keyRef = useRef(key);
  keyRef.current = key;

  const setModel = useCallback((next: string) => {
    setModelLocal(next);
    storeSetModel(next);
  }, [storeSetModel]);

  useEffect(() => {
    // 仅当用户从未手动选过模型（仍是硬编码默认值）时才取后端默认模型
    if (storeModel !== DEFAULT_MODEL) return;
    backend.getAiConfig()
      .then((cfg) => {
        if (cfg.default_model && cfg.default_model !== storeModel) {
          setModel(cfg.default_model);
        }
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 卸载时快照 messagesRef，正在流式的回复保存到最后一个已渲染 chunk 而非静默丢弃
  useEffect(() => {
    return () => {
      const msgs = messagesRef.current;
      const k = keyRef.current;
      if (msgs.length === 0) return;
      const cleaned = cleanForStore(msgs);
      // cleanup 里不能调 hook，直接取 Zustand store
      useAiChatStore.getState().setMessages(k, cleaned);
      void backendSave(k, cleaned);
    };
  }, []); // 依赖故意留空：只在最终卸载时执行

  useEffect(() => {
    if (aiCreatedFlowRef.current) {
      // key 切换源自 AI 自己 create_flow，流还在继续不能 abort 自己，只把消息快照挂到新 key
      aiCreatedFlowRef.current = false;
      const currentMsgs = messagesRef.current;
      if (currentMsgs.length > 0) {
        const cleaned = cleanForStore(currentMsgs);
        void backendSave(key, cleaned);
        storeSetMessages(key, cleaned);
        return;
      }
    }

    // 掐断上一个会话的请求，防止其覆盖新会话状态或留下僵尸 SSE 连接
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setPending(false);

    setMessages(storeGetMessages(key));
    let cancelled = false;
    void backendLoad(key).then((msgs) => {
      if (!cancelled && msgs.length > 0) {
        setMessages(msgs);
        storeSetMessages(key, cleanForStore(msgs));
      }
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const sentHistory = messages
    .filter((m) => m.role === 'user' && m.content.trim() !== '')
    .map((m) => m.content);

  const persistMessages = useCallback((msgs: AiMessage[]) => {
    // 经 keyRef 取 key：AI create_flow 中途切换会话后，done 时才不会写回旧草稿 key
    const k = keyRef.current;
    const cleaned = cleanForStore(msgs);
    storeSetMessages(k, cleaned);
    if (!saveInFlightRef.current) {
      saveInFlightRef.current = true;
      void backendSave(k, cleaned).finally(() => { saveInFlightRef.current = false; });
    }
  }, [storeSetMessages]);

  // 落库统一放到提交之后：done/abort/流式检查点触发时 messagesRef 还停在上一次渲染，
  // 直接读它会把同一批到达的末尾 token 与 tool_result 一起写丢
  useEffect(() => {
    if (!persistAfterCommitRef.current) return;
    persistAfterCommitRef.current = false;
    persistMessages(messages);
  }, [messages, persistMessages]);

  const send = useCallback(
    async (text: string, attachments?: AiAttachment[]) => {
      if (pending || (text.trim() === '' && (!attachments || attachments.length === 0))) return;

      const controller = new AbortController();
      abortRef.current = controller;
      setPending(true);

      const userMsg: AiMessage = {
        id: nanoid(),
        role: 'user',
        content: text,
        attachments,
        createdAt: Date.now(),
      };
      const assistantId = nanoid();
      const assistantStartedAt = Date.now();

      // 经 ref 取最新历史，避免把 `messages` 加进依赖导致 send 每个 chunk 重建
      const historySnapshot = messagesRef.current;
      setMessages([
        ...historySnapshot,
        userMsg,
        { id: assistantId, role: 'assistant', content: '', createdAt: assistantStartedAt },
      ]);

      const finishAssistantMessage = (message: AiMessage, patch: Partial<AiMessage> = {}): AiMessage => ({
        ...message,
        ...patch,
        processingMs: Math.max(0, Date.now() - assistantStartedAt),
        finishedAt: Date.now(),
      });

      const buildContent = (m: AiMessage, includeImages: boolean): string | Array<unknown> => {
        const imgs = includeImages ? (m.attachments ?? []).filter(a => a.type === 'image') : [];
        if (imgs.length === 0) return m.content;
        const parts: Array<unknown> = [];
        if (m.content.trim()) parts.push({ type: 'text', text: m.content });
        for (const img of imgs) {
          parts.push({ type: 'image_url', image_url: { url: img.dataUrl } });
        }
        return parts;
      };

      // 已持久化标记，防止 finally 重复保存
      let persisted = false;
      lastStreamPersistRef.current = Date.now();
      // 记录流内最后一条错误：React 批处理下 messagesRef 可能滞后，done 时据此合并
      let streamError: string | null = null;

      try {
        const response = await backend.streamAiChat({
          // 用快照固定发送时刻的历史
          messages: [...historySnapshot, userMsg].map((m) => ({
            role: m.role,
            content: buildContent(m, m.id === userMsg.id),
            // 带上工具回合，后端才能还原成 tool_calls；只发 content 会让纯工具回合变成空消息
            toolCalls: m.toolCalls?.map((tc) => ({ tool: tc.tool, args: tc.args, result: tc.result })),
          })),
          model,
          flow_id: flowId,
        }, controller.signal);

        if (!response.body) { setPending(false); return; }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          for (const line of lines) {
            if (!line.startsWith('data:')) continue;
            const raw = line.slice(5).trim();
            if (!raw) continue;
            let chunk: {
              type: string; delta?: string; tool?: string; args?: string; result?: unknown; message?: string;
              call_id?: string;
              elapsed_s?: number; progress?: { current_step?: number; total_steps?: number; percent?: number } | null;
            };
            try { chunk = JSON.parse(raw); } catch { continue; }

            if (chunk.type === 'heartbeat') {
              // run_flow 这类工具要跑几分钟，这期间除了心跳没有任何事件；
              // 不把用时和步数显示出来，面板看起来就是卡死的
              const detail = formatToolProgress(chunk.elapsed_s, chunk.progress);
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, statusDetail: detail } : m))
              );
              continue;
            }
            if (chunk.type === 'thinking' && chunk.delta) {
              // 推理 token 实时展示，避免思考型模型让面板卡在「正在思考…」
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, reasoning: (m.reasoning ?? '') + chunk.delta } : m))
              );
            } else if (chunk.type === 'status' && chunk.delta) {
              // 换阶段就把上一个阶段的用时清掉，否则新状态会挂着旧计时
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, statusText: chunk.delta, statusDetail: undefined } : m))
              );
            } else if (chunk.type === 'retract') {
              // 编排层判定已吐出的结论超出证据；清空正文交给下一轮重写，
              // 保留 toolCalls（那些调用真的发生过）
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, content: '' } : m))
              );
            } else if (chunk.type === 'text' && chunk.delta) {
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + chunk.delta! } : m))
              );
            } else if (chunk.type === 'tool_start' && chunk.tool && chunk.call_id) {
              const tc: ToolCallState = {
                id: chunk.call_id, tool: chunk.tool, args: chunk.args ?? '', status: 'running',
              };
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, toolCalls: [...(m.toolCalls ?? []), tc] } : m
                )
              );
            } else if (chunk.type === 'tool_args' && chunk.tool) {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, toolCalls: patchToolCall(m.toolCalls, chunk.call_id, { args: chunk.args ?? '' }) }
                    : m
                )
              );
            } else if (chunk.type === 'tool_result' && chunk.tool) {
              if (chunk.tool === 'update_flow' && chunk.result && typeof chunk.result === 'object') {
                const res = chunk.result as Record<string, unknown>;
                if (res.status === 'applied' && typeof res.flow_id === 'string' && res.flow_id) {
                  onFlowChangedRef.current?.(res.flow_id);
                }
              } else if (chunk.tool === 'create_flow' && chunk.result && typeof chunk.result === 'object') {
                const res = chunk.result as Record<string, unknown>;
                if (typeof res.flow_id === 'string' && res.flow_id) {
                  aiCreatedFlowRef.current = true;
                  onFlowChangedRef.current?.(res.flow_id);
                }
              }
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                      ...m,
                      toolCalls: patchToolCall(m.toolCalls, chunk.call_id, {
                        result: chunk.result,
                        status: isBlockedResult(chunk.result) ? 'blocked' : 'done',
                      }),
                    }
                    : m
                )
              );
            } else if (chunk.type === 'error') {
              const errMsg = (chunk as unknown as { message?: string }).message ?? '未知错误';
              streamError = errMsg;
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, error: errMsg } : m
                )
              );
            } else if (chunk.type === 'done') {
              setPending(false);
              setMessages((prev) =>
                prev.map((m) => {
                  if (m.id !== assistantId) return m;
                  const error = m.error ?? streamError ?? undefined;
                  if (m.content.trim() === '' && !error) {
                    // 调过工具却零正文多半是被 guard 拦下后直接收尾，让用户去查 API Key 是南辕北辙
                    return finishAssistantMessage(m, {
                      content: (m.toolCalls ?? []).length > 0
                        ? '本轮结束但没有给出文字说明，具体做过什么见上面的处理时间线。可以让我接着说明或继续下一步。'
                        : '未返回内容，请检查 API Key 或更换模型',
                    });
                  }
                  return finishAssistantMessage(m, { error });
                })
              );
              persistAfterCommitRef.current = true;
              persisted = true;
            }
          }

          // 按批而非按行判断，避免高频 text delta 让节流判断本身变成开销
          if (!persisted && Date.now() - lastStreamPersistRef.current >= STREAM_PERSIST_INTERVAL_MS) {
            lastStreamPersistRef.current = Date.now();
            persistAfterCommitRef.current = true;
          }
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m;
              return {
                ...m,
                // 正文为空的助手消息会被 isPersistableMessage 丢掉，重开会话只剩用户自己那条提问
                content: m.content || ((m.toolCalls ?? []).length > 0
                  ? '已按你的要求停止。上面的处理时间线是停止前完成的操作，告诉我下一步怎么走我再继续。'
                  : '已按你的要求停止，本轮没有产生任何改动。'),
                // running 工具卡片标记为 stopped，避免永远转圈
                toolCalls: (m.toolCalls ?? []).map((tc) =>
                  tc.status === 'running' ? { ...tc, status: 'stopped' as const } : tc
                ),
                processingMs: Math.max(0, Date.now() - assistantStartedAt),
              };
            })
          );
        } else {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? finishAssistantMessage(m, { error: `请求失败：${err instanceof Error ? err.message : String(err)}` })
                : m
            )
          );
        }
        persistAfterCommitRef.current = true;
        persisted = true;
      } finally {
        setPending(false);
        abortRef.current = null;
        // 兜底：流没发 done 就结束（后端崩溃/断网）时补一次持久化
        if (!persisted) {
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantId || m.processingMs !== undefined) return m;
              // 走到这里说明流被掐断（后端崩溃/断网），空正文就补一句可重试的说明
              return finishAssistantMessage(m, m.content.trim() ? {} : {
                error: '连接中断，本轮没有收到完整回复。上面的处理时间线是中断前完成的操作，可以重试继续。',
              });
            })
          );
          persistAfterCommitRef.current = true;
        }
      }
    },
    // messages/onFlowChanged 走 ref 读取，避免每个 chunk 重建回调与 stale closure
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [model, flowId, pending, key, persistMessages]
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const retry = useCallback(async () => {
    if (pending) return;
    const msgs = messagesRef.current;
    let lastUserIdx = -1;
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'user') { lastUserIdx = i; break; }
    }
    if (lastUserIdx === -1) return;
    const lastUserMsg = msgs[lastUserIdx];
    // 截到最后一条用户消息之前，send 才不会重复追加它
    const trimmed = msgs.slice(0, lastUserIdx);
    messagesRef.current = trimmed;
    setMessages(trimmed);
    await send(lastUserMsg.content, lastUserMsg.attachments);
  }, [pending, send]);

  const applyDiff = useCallback(async (diff: FlowDiff): Promise<{ ok: boolean; error?: string }> => {
    try {
      await backend.applyAiDiff(diff);
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
  }, []);

  const clearDiff = useCallback((messageId: string) => {
    setMessages((prev) => {
      const next = prev.map((m) => (m.id === messageId ? { ...m, diffPreview: undefined } : m));
      void backendSave(key, next);
      storeSetMessages(key, cleanForStore(next));
      return next;
    });
  }, [key, storeSetMessages]);

  const clearMessages = useCallback(() => {
    setMessages([]);
    storeClearMessages(key);
    void backendDelete(key);
  }, [key, storeClearMessages]);

  return { messages, pending, sentHistory, model, setModel, send, stop, retry, applyDiff, clearDiff, clearMessages };
}
