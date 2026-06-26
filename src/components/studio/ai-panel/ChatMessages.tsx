import { ArrowUpRight, Bot, Bug, PlayCircle, Workflow } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useRef } from 'react';
import type { AiMessage, FlowDiff, NodeLookupItem } from './aiPanelTypes';
import { MessageBubble } from './MessageBubble';

const STARTER_PROMPTS: { icon: ReactElement; label: string; prompt: string }[] = [
  {
    icon: <Workflow className="h-3.5 w-3.5" strokeWidth={1.5} />,
    label: '创建一个抓取流程',
    prompt: '帮我创建一个流程：打开指定网页，抓取列表中每一行的标题和链接，导出为 Excel。',
  },
  {
    icon: <PlayCircle className="h-3.5 w-3.5" strokeWidth={1.5} />,
    label: '运行当前流程并汇报结果',
    prompt: '运行当前流程，完成后告诉我抓取了多少条数据、生成了哪些产物文件。',
  },
  {
    icon: <Bug className="h-3.5 w-3.5" strokeWidth={1.5} />,
    label: '排查上次运行失败的原因',
    prompt: '上次运行失败了，帮我查看运行日志，定位失败的节点并说明原因。',
  },
];

export function ChatMessages({
  messages,
  pending,
  onApplyDiff,
  onPickPrompt,
  onRejectDiff,
  onRetry,
  nodeLookup,
  onFocusNode,
}: {
  messages: AiMessage[];
  pending: boolean;
  onApplyDiff: (diff: FlowDiff) => Promise<{ ok: boolean; error?: string }>;
  onPickPrompt?: (prompt: string) => void;
  onRejectDiff: (messageId: string) => void;
  onRetry?: () => void;
  nodeLookup?: Record<string, NodeLookupItem>;
  onFocusNode?: (nodeId: string) => void;
}): ReactElement {
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // True when the scroll position is at (or near) the bottom.
  // Resets to true whenever a new message arrives; goes false when user scrolls up.
  const isAtBottom = useRef(true);

  const handleScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    isAtBottom.current = distanceFromBottom < 80;
  };

  const last = messages[messages.length - 1];

  // New message sent or received → always jump to bottom and reset the flag.
  useEffect(() => {
    isAtBottom.current = true;
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length]);

  // Streaming chunk update → only follow if user hasn't scrolled away.
  useEffect(() => {
    if (isAtBottom.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [last?.content, last?.reasoning, last?.statusText]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center px-5">
        <div className="w-full max-w-72">
          <div className="mb-5 flex flex-col items-center text-center">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-accent shadow-sm shadow-[0_2px_10px_var(--color-accent-soft)]">
              <Bot className="h-5 w-5 text-white" strokeWidth={1.75} />
            </div>
            <p className="mt-3 text-[13px] font-semibold text-slate-700">RPA 助手</p>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
              描述你的目标，我来创建、修改、运行和调试 RPA 流程
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            {STARTER_PROMPTS.map((s) => (
              <button
                className="group flex items-center gap-2.5 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-left transition-all hover:border-accent-linehover:bg-accent-soft"
                key={s.label}
                onClick={() => onPickPrompt?.(s.prompt)}
                type="button"
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-500 transition-colors group-hover:bg-accent-soft group-hover:text-accent-strong">
                  {s.icon}
                </span>
                <span className="flex-1 text-[11.5px] font-medium text-slate-600 group-hover:text-slate-800">{s.label}</span>
                <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-slate-300 transition-colors group-hover:text-accent" strokeWidth={1.5} />
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  const lastErrorIdx = !pending
    ? messages.reduce((found, m, i) => (m.role === 'assistant' && m.error ? i : found), -1)
    : -1;

  return (
    <div className="flex flex-1 flex-col overflow-y-auto" ref={containerRef} onScroll={handleScroll}>
      {messages.map((msg, index) => (
        <MessageBubble
          key={msg.id}
          message={msg}
          nodeLookup={nodeLookup}
          onApplyDiff={onApplyDiff}
          onFocusNode={onFocusNode}
          onRejectDiff={onRejectDiff}
          onRetry={index === lastErrorIdx ? onRetry : undefined}
          streamingPending={pending && index === messages.length - 1}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
