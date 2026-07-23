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

const CAPABILITIES: { icon: ReactElement; label: string }[] = [
  { icon: <Workflow className="h-3 w-3" strokeWidth={1.5} />, label: '创建流程' },
  { icon: <PencilRuler className="h-3 w-3" strokeWidth={1.5} />, label: '修改与校验' },
  { icon: <PlayCircle className="h-3 w-3" strokeWidth={1.5} />, label: '运行与汇报' },
  { icon: <Bug className="h-3 w-3" strokeWidth={1.5} />, label: '失败排查' },
  { icon: <CalendarClock className="h-3 w-3" strokeWidth={1.5} />, label: '定时任务' },
];

export function ChatMessages({
  messages,
  pending,
  onApplyDiff,
  onRejectDiff,
  onRetry,
  nodeLookup,
  onFocusNode,
}: {
  messages: AiMessage[];
  pending: boolean;
  onApplyDiff: (diff: FlowDiff) => Promise<{ ok: boolean; error?: string }>;
  onRejectDiff: (messageId: string) => void;
  onRetry?: () => void;
  nodeLookup?: Record<string, NodeLookupItem>;
  onFocusNode?: (nodeId: string) => void;
}): ReactElement {
  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center px-5">
        <div className="flex flex-col items-center text-center">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-accent shadow-[0_2px_10px_var(--color-accent-soft)]">
            <Bot className="h-5 w-5 text-white" strokeWidth={1.75} />
          </div>
          <p className="mt-3 text-[13px] font-semibold text-slate-700">RPA 助手</p>
          <div className="mt-4 flex max-w-64 flex-wrap items-center justify-center gap-1.5">
            {CAPABILITIES.map((c) => (
              <span
                className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10.5px] text-slate-500"
                key={c.label}
              >
                {c.icon}
                {c.label}
              </span>
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
