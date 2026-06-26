import { useCallback, useEffect, useRef, useState } from 'react';
import { useAiChatStore } from '../../../stores/useAiChatStore';
import type { AiAttachment, AiMessage, FlowDiff, ToolCallState } from './aiPanelTypes';
import { DEFAULT_BROWSER_BACKEND_URL } from '../../../lib/backendClient';

function nanoid(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

// ─── Session key ──────────────────────────────────────────────────────────────

function sessionKey(flowId: string | null): string {
  return flowId ? `flow_${flowId}` : 'local';
}

// ─── Backend persistence ──────────────────────────────────────────────────────

const API = DEFAULT_BROWSER_BACKEND_URL;

async function backendLoad(key: string): Promise<AiMessage[]> {
  try {
    const res = await fetch(`${API}/api/ai/chats/${encodeURIComponent(key)}`);
    if (!res.ok) return [];
    const data = (await res.json()) as { messages: AiMessage[] };
    return Array.isArray(data.messages) ? data.messages : [];
  } catch {
    return [];
  }
}

async function backendSave(key: string, messages: AiMessage[]): Promise<void> {
  try {
    const clean = messages
      .filter((m) => m.role === 'user' || (m.role === 'assistant' && m.content.trim() !== ''));
    await fetch(`${API}/api/ai/chats/${encodeURIComponent(key)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: clean }),
    });
  } catch { /* fire-and-forget; no data loss on failure */ }
}

async function backendDelete(key: string): Promise<void> {
  try {
    await fetch(`${API}/api/ai/chats/${encodeURIComponent(key)}`, { method: 'DELETE' });
  } catch { /* ignore */ }
}

// ─── Store helpers ─────────────────────────────────────────────────────────────

function cleanForStore(messages: AiMessage[]): AiMessage[] {
  return messages
    .filter((m) => m.role === 'user' || (m.role === 'assistant' && m.content.trim() !== ''))
    // Drop transient streaming-only fields so they aren't persisted or replayed
    .map(({ diffPreview: _dp, reasoning: _r, statusText: _s, error: _e, ...rest }) => rest);
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

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
  // Always-current references used inside long-lived async closures to avoid stale captures
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const onFlowChangedRef = useRef(onFlowChanged);
  onFlowChangedRef.current = onFlowChanged;
  const aiCreatedFlowRef = useRef(false);
  // Keep a ref to the current key so the unmount cleanup can use the right session key
  // even if flowId changed between mount and unmount.
  const keyRef = useRef(key);
  keyRef.current = key;

  // ── Persist model choice to store ─────────────────────────────────────────
  const setModel = useCallback((next: string) => {
    setModelLocal(next);
    storeSetModel(next);
  }, [storeSetModel]);

  useEffect(() => {
    // Only fetch the backend default when using the hard-coded default (i.e. user has never
    // explicitly chosen a model).
    if (storeModel !== 'claude-sonnet-4-6') return;
    fetch(`${API}/api/ai/config`)
      .then(r => r.json())
      .then((cfg: { default_model?: string }) => {
        if (cfg.default_model && cfg.default_model !== storeModel) {
          setModel(cfg.default_model);
        }
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Persist on unmount ────────────────────────────────────────────────────
  // Fires when AiPanel closes (aiPanelOpen toggled) or the component tree is
  // torn down for any reason. Snapshots messagesRef so that a streaming reply
  // in progress at the moment of unmount is saved up to the last rendered chunk
  // rather than being silently discarded.
  useEffect(() => {
    return () => {
      const msgs = messagesRef.current;
      const k = keyRef.current;
      if (msgs.length === 0) return;
      const cleaned = cleanForStore(msgs);
      // Access Zustand store directly (not via hook) — hooks cannot be called
      // inside effect cleanup functions.
      useAiChatStore.getState().setMessages(k, cleaned);
      void backendSave(k, cleaned);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally empty — run cleanup only on final unmount

  // ── Load from backend on mount / when flowId changes ──────────────────────
  useEffect(() => {
    // Abort any in-flight request for the previous session so it doesn't
    // overwrite the new session's state or keep a zombie SSE connection open.
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setPending(false);

    if (aiCreatedFlowRef.current) {
      aiCreatedFlowRef.current = false;
      const currentMsgs = messagesRef.current;
      if (currentMsgs.length > 0) {
        void backendSave(key, currentMsgs);
        storeSetMessages(key, cleanForStore(currentMsgs));
        return;
      }
    }

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

  // ── Sent history for ↑/↓ navigation in ChatInput ─────────────────────────
  const sentHistory = messages
    .filter((m) => m.role === 'user' && m.content.trim() !== '')
    .map((m) => m.content);

  // ── Persist helper ─────────────────────────────────────────────────────────
  const persistMessages = useCallback((msgs: AiMessage[]) => {
    const cleaned = cleanForStore(msgs);
    storeSetMessages(key, cleaned);
    if (!saveInFlightRef.current) {
      saveInFlightRef.current = true;
      void backendSave(key, cleaned).finally(() => { saveInFlightRef.current = false; });
    }
  }, [key, storeSetMessages]);

  // ─── Send ─────────────────────────────────────────────────────────────────
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

      // Snapshot current messages + append new ones. Using the ref gives us the
      // latest state without adding `messages` to the dep array (which would
      // recreate `send` on every streaming chunk update).
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

      // Track whether we already persisted so `finally` doesn't double-save
      let persisted = false;
      // Track the last error message from the stream so the `done` handler can
      // merge it even when messagesRef is stale (React batching delay).
      let streamError: string | null = null;

      try {
        const response = await fetch(`${API}/api/ai/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            // Use snapshot so history is fixed at send time
            messages: [...historySnapshot, userMsg].map((m) => ({
              role: m.role,
              content: buildContent(m, m.id === userMsg.id),
            })),
            model,
            flow_id: flowId,
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          let errMsg = `HTTP ${response.status}`;
          try { const j = await response.json() as { detail?: string }; errMsg = j.detail ?? errMsg; } catch { /* ignore */ }
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? finishAssistantMessage(m, { error: `请求失败：${errMsg}` }) : m
            )
          );
          setPending(false);
          return;
        }

        if (!response.body) { setPending(false); return; }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        // eslint-disable-next-line no-constant-condition
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
            let chunk: { type: string; delta?: string; tool?: string; args?: string; result?: unknown; message?: string };
            try { chunk = JSON.parse(raw); } catch { continue; }

            if (chunk.type === 'heartbeat') continue; // keepalive — no UI action needed
            if (chunk.type === 'thinking' && chunk.delta) {
              // Reasoning tokens (deepseek-v4-flash / R1 / Qwen3-thinking). Show them
              // live so a model that "thinks" for several seconds before any answer
              // doesn't make the panel look frozen on "AI 正在思考…".
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, reasoning: (m.reasoning ?? '') + chunk.delta } : m))
              );
            } else if (chunk.type === 'status' && chunk.delta) {
              // Status text shown in the thinking bubble while AI is between operations
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, statusText: chunk.delta } : m))
              );
            } else if (chunk.type === 'text' && chunk.delta) {
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + chunk.delta! } : m))
              );
            } else if (chunk.type === 'tool_start' && chunk.tool) {
              const tc: ToolCallState = { id: nanoid(), tool: chunk.tool, args: chunk.args ?? '', status: 'running' };
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, toolCalls: [...(m.toolCalls ?? []), tc] } : m
                )
              );
            } else if (chunk.type === 'tool_args' && chunk.tool) {
              // Update args on the already-created tool card (emitted early during streaming)
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                      ...m,
                      toolCalls: (m.toolCalls ?? []).map((tc) =>
                        tc.tool === chunk.tool && tc.status === 'running' && tc.args === ''
                          ? { ...tc, args: chunk.args ?? '' }
                          : tc
                      ),
                    }
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
                      toolCalls: (m.toolCalls ?? []).map((tc) =>
                        tc.tool === chunk.tool && tc.status === 'running'
                          ? { ...tc, result: chunk.result, status: 'done' }
                          : tc
                      ),
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
              // Use messagesRef to read state accumulated during streaming without
              // nesting persistMessages inside a setMessages updater (which would race
              // with the finally block's persisted flag).
              const current = messagesRef.current;
              const finalMsgs = current.map((m) => {
                if (m.id !== assistantId) return m;
                // Merge any error that arrived just before `done` — React may not have
                // committed that setMessages yet, so messagesRef.current is stale.
                const error = m.error ?? (streamError && !m.error ? streamError : undefined);
                const content = m.content;
                if (content.trim() === '' && !error) {
                  return finishAssistantMessage(m, { content: '（未返回内容，请检查 API Key 或更换模型）' });
                }
                return finishAssistantMessage({ ...m, content, error });
              });
              setMessages(finalMsgs);
              persistMessages(finalMsgs);
              persisted = true;
            }
          }
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m;
              return {
                ...m,
                content: m.content || '（已中止）',
                // Mark any still-running tool calls as stopped so cards don't spin forever
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
                ? finishAssistantMessage(m, { error: `请求失败：${String(err)}` })
                : m
            )
          );
        }
        // messagesRef reflects state from last render; good enough for abort/error persist
        persistMessages(messagesRef.current);
        persisted = true;
      } finally {
        setPending(false);
        abortRef.current = null;
        // Only persist if neither `done` nor `catch` handled it — covers the edge case
        // where the stream ends without a `done` event (backend crash, network drop).
        if (!persisted) {
          const finalMsgs = messagesRef.current.map((m) =>
            m.id === assistantId && m.processingMs === undefined
              ? finishAssistantMessage(m)
              : m
          );
          setMessages(finalMsgs);
          persistMessages(finalMsgs);
        }
      }
    },
    // `messages` removed from deps — read via messagesRef.current instead so this
    // callback doesn't recreate on every streaming chunk update.
    // `onFlowChanged` captured via onFlowChangedRef to avoid stale closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [model, flowId, pending, key, persistMessages]
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const retry = useCallback(async () => {
    if (pending) return;
    const msgs = messagesRef.current;
    // Find last user message (precedes the errored assistant reply)
    let lastUserIdx = -1;
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'user') { lastUserIdx = i; break; }
    }
    if (lastUserIdx === -1) return;
    const lastUserMsg = msgs[lastUserIdx];
    // Trim to history before the last user message so `send` doesn't duplicate it
    const trimmed = msgs.slice(0, lastUserIdx);
    messagesRef.current = trimmed;
    setMessages(trimmed);
    await send(lastUserMsg.content, lastUserMsg.attachments);
  }, [pending, send]);

  const applyDiff = useCallback(async (diff: FlowDiff): Promise<{ ok: boolean; error?: string }> => {
    try {
      const res = await fetch(`${API}/api/ai/diff/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ diff }),
      });
      if (res.ok) return { ok: true };
      let errMsg = `HTTP ${res.status}`;
      try { const j = await res.json() as { detail?: string }; errMsg = j.detail ?? errMsg; } catch { /* ignore */ }
      return { ok: false, error: errMsg };
    } catch (e) { return { ok: false, error: String(e) }; }
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
