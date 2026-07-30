import { Bot, Bug, CalendarClock, PencilRuler, PlayCircle, Workflow } from 'lucide-react';
import type { ReactElement } from 'react';
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from '../../ui/message-scroller';
import type { AiMessage, FlowDiff, NodeLookupItem } from './aiPanelTypes';
import { MessageBubble } from './MessageBubble';

const CAPABILITIES: { icon: ReactElement; label: string; prompt: string; needsFlow?: boolean }[] = [
  { icon: <Workflow className="h-3 w-3" strokeWidth={1.5} />, label: '创建抓取流程', prompt: '帮我根据一个网页创建抓取流程' },
  { icon: <PencilRuler className="h-3 w-3" strokeWidth={1.5} />, label: '审查当前流程', prompt: '审查当前流程，找出影响可靠性的问题并给出最优修复', needsFlow: true },
  { icon: <PlayCircle className="h-3 w-3" strokeWidth={1.5} />, label: '运行并验收', prompt: '运行当前流程，并根据实际输出完成验收', needsFlow: true },
  { icon: <Bug className="h-3 w-3" strokeWidth={1.5} />, label: '分析运行错误', prompt: '分析当前流程最近一次运行错误并定位根因', needsFlow: true },
  { icon: <CalendarClock className="h-3 w-3" strokeWidth={1.5} />, label: '创建定时任务', prompt: '为当前流程创建定时任务', needsFlow: true },
];

export function ChatMessages({
  messages,
  pending,
  onApplyDiff,
  onRejectDiff,
  onRetry,
  nodeLookup,
  onFocusNode,
  hasFlow,
  onSuggestion,
}: {
  messages: AiMessage[];
  pending: boolean;
  onApplyDiff: (diff: FlowDiff) => Promise<{ ok: boolean; error?: string }>;
  onRejectDiff: (messageId: string) => void;
  onRetry?: () => void;
  nodeLookup?: Record<string, NodeLookupItem>;
  onFocusNode?: (nodeId: string) => void;
  hasFlow: boolean;
  onSuggestion: (prompt: string) => void;
}): ReactElement {
  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center px-5">
        <div className="flex flex-col items-center text-center">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-accent shadow-[0_2px_10px_var(--color-accent-soft)]">
            <Bot className="h-5 w-5 text-white" strokeWidth={1.75} />
          </div>
          <p className="mt-3 text-[13px] font-semibold text-slate-700">RPA 助手</p>
          <p className="mt-1.5 max-w-64 text-[11px] leading-relaxed text-slate-500">
            描述目标，我会先确认关键条件，再创建、运行并用实际结果验收。
          </p>
          <div className="mt-4 flex max-w-72 flex-wrap items-center justify-center gap-1.5">
            {CAPABILITIES.filter((c) => !c.needsFlow || hasFlow).map((c) => (
              <button
                className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-[10.5px] text-slate-600 transition-colors hover:border-accent-line hover:bg-accent-soft hover:text-accent-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-soft"
                key={c.label}
                onClick={() => onSuggestion(c.prompt)}
                type="button"
              >
                {c.icon}
                {c.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // 重试按钮只挂在最后一条报错消息上，避免历史里每条错误都显示可点的重试
  const lastErrorIdx = !pending
    ? messages.reduce((found, m, i) => (m.role === 'assistant' && m.error ? i : found), -1)
    : -1;

  return (
    <MessageScrollerProvider autoScroll defaultScrollPosition="last-anchor">
      <MessageScroller className="flex-1">
        <MessageScrollerViewport>
          <MessageScrollerContent>
            {messages.map((msg, index) => (
              <MessageScrollerItem key={msg.id} messageId={msg.id} scrollAnchor={msg.role === 'user'}>
                <MessageBubble
                  message={msg}
                  nodeLookup={nodeLookup}
                  onApplyDiff={onApplyDiff}
                  onFocusNode={onFocusNode}
                  onRejectDiff={onRejectDiff}
                  onRetry={index === lastErrorIdx ? onRetry : undefined}
                  streamingPending={pending && index === messages.length - 1}
                />
              </MessageScrollerItem>
            ))}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  );
}
