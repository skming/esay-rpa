import { CircleAlert, Loader2, Paperclip, RefreshCw } from 'lucide-react';
import type { ReactElement } from 'react';
import { memo } from 'react';

import { cn } from '../../../lib/utils';
import { Marker, MarkerContent, MarkerIcon, ThinkingDots } from '../../ui/marker';
import type { AiAttachment, AiMessage, FlowDiff, NodeLookupItem } from './aiPanelTypes';
import { FlowDiffPreview } from './FlowDiffPreview';
import { MarkdownContent } from './MarkdownContent';
import { ProcessingTimeline } from './ProcessingTimeline';
import { ToolCallCard } from './ToolCallCard';

function PlainText({ text }: { text: string }): ReactElement {
  return <span className="whitespace-pre-wrap wrap-break-word text-[12px] leading-relaxed">{text}</span>;
}

function formatTime(ms: number): string {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
}

function MessageTimestamp({ message }: { message: AiMessage }): ReactElement {
  const isUser = message.role === 'user';
  const label = formatTime(message.createdAt);
  return (
    <span
      className={cn(
        'pointer-events-none mt-0.5 block select-none font-mono text-[10px] tabular-nums text-ink-3 opacity-0 transition-opacity duration-150 group-hover/msg:opacity-100',
        isUser ? 'text-right' : 'text-left'
      )}
    >
      {label}
    </span>
  );
}

function AttachmentStrip({ attachments, isUser }: { attachments: AiAttachment[]; isUser: boolean }): ReactElement {
  return (
    <div className={cn('mt-1.5 flex flex-wrap gap-1.5', isUser ? 'justify-end' : 'justify-start')}>
      {attachments.map(att => (
        <div
          className={cn(
            'flex max-w-40 items-center gap-1 rounded-lg border px-2 py-1 text-[10px]',
            isUser
              ? 'border-accent-line bg-accent-soft text-accent-strong'
              : 'border-slate-200 bg-slate-50 text-slate-600'
          )}
          key={att.id}
          title={att.name}
        >
          {att.type === 'image' ? (
            <img
              alt={att.name}
              className="h-10 w-10 rounded object-cover"
              src={att.dataUrl}
            />
          ) : (
            <Paperclip className="h-3 w-3 shrink-0 opacity-60" />
          )}
          <span className="min-w-0 truncate">{att.name}</span>
        </div>
      ))}
    </div>
  );
}

// 流式每收到一批 chunk 就换一次 messages 数组，不 memo 的话历史里每条消息都要重新
// 解析 Markdown、重跑代码高亮；长会话下打字会肉眼卡顿
export const MessageBubble = memo(function MessageBubble({
  message,
  onApplyDiff,
  onRejectDiff,
  onRetry,
  streamingPending,
  nodeLookup,
  onFocusNode,
}: {
  message: AiMessage;
  onApplyDiff: (diff: FlowDiff) => Promise<{ ok: boolean; error?: string }>;
  onRejectDiff: (messageId: string) => void;
  onRetry?: () => void;
  streamingPending?: boolean;
  nodeLookup?: Record<string, NodeLookupItem>;
  onFocusNode?: (nodeId: string) => void;
}): ReactElement {
  const isUser = message.role === 'user';

  return (
    <div className={cn('group/msg flex px-3 py-2', isUser ? 'justify-end' : 'justify-start')}>
      <div className={cn('min-w-0', isUser ? 'max-w-[85%] items-end' : 'w-full items-start', 'flex flex-col')}>
        {message.attachments && message.attachments.length > 0 && (
          <AttachmentStrip attachments={message.attachments} isUser={isUser} />
        )}

        {streamingPending && !isUser && message.reasoning && !message.content && (
          <div className="mt-0.5 w-full rounded-xl rounded-tl-sm border border-slate-100 bg-slate-50/60 px-3 py-2">
            <Marker className="mb-1 text-slate-300">
              <MarkerIcon>
                <ThinkingDots />
              </MarkerIcon>
              <MarkerContent className="text-[10px] font-medium text-slate-500">思考中</MarkerContent>
            </Marker>
            <p className="line-clamp-3 text-[11px] leading-relaxed text-slate-500">{message.reasoning}</p>
          </div>
        )}

        {/* 无名转圈点只在没有具体状态文案时兜底，有文案时由下面那行代劳 */}
        {streamingPending && !isUser && !message.content && !message.reasoning && message.statusText === undefined
          && (!message.toolCalls || message.toolCalls.length === 0) && (
            <div className="mt-0.5 rounded-xl rounded-tl-sm border border-slate-100 bg-white px-3 py-2.5">
              <Marker className="text-accent">
                <MarkerIcon>
                  <ThinkingDots size="md" />
                </MarkerIcon>
                <MarkerContent className="sr-only">正在思考</MarkerContent>
              </Marker>
            </div>
          )}

        {streamingPending && !isUser && !message.content && !message.reasoning && message.statusText === undefined
          && message.toolCalls && message.toolCalls.length > 0
          && message.toolCalls.every((tc) => tc.status === 'done' || tc.status === 'error') && (
            <Marker className="mt-1 px-0.5 py-1 text-slate-300">
              <MarkerIcon>
                <ThinkingDots />
              </MarkerIcon>
              <MarkerContent className="sr-only">处理下一步</MarkerContent>
            </Marker>
          )}

        {/* 工具执行期间唯一的活体信号：run_flow 要跑几分钟，没有这行面板看起来就是卡死的 */}
        {streamingPending && !isUser && !message.content && message.statusText !== undefined && (
          <Marker className="mt-1 px-0.5 py-1 text-accent">
            <MarkerIcon>
              <Loader2 className="animate-spin" strokeWidth={1.8} />
            </MarkerIcon>
            <MarkerContent className="text-[11px] text-slate-500">
              {message.statusText}
              {message.statusDetail !== undefined && (
                <span className="ml-1 font-mono text-[10px] tabular-nums text-slate-400">· {message.statusDetail}</span>
              )}
            </MarkerContent>
          </Marker>
        )}

        {/* 工具调用折叠进处理时间线，保持最终回答可读 */}
        {!isUser && message.toolCalls && message.toolCalls.length > 0 && (
          <div className="mt-1 w-full">
            <ProcessingTimeline
              onFocusNode={onFocusNode}
              processingMs={message.processingMs}
              streamingPending={streamingPending}
              toolCalls={message.toolCalls}
            />
          </div>
        )}

        {message.content && (
          <div
            className={cn(
              'mt-0.5 min-w-0 text-[12px] leading-relaxed',
              isUser
                ? 'rounded-xl rounded-tr-sm bg-accent-strong px-3 py-2 text-white'
                : 'w-full px-0.5 py-0.5 text-slate-700'
            )}
          >
            {isUser
              ? <PlainText text={message.content} />
              : <MarkdownContent text={message.content} onFocusNode={onFocusNode} />
            }
          </div>
        )}

        {isUser && message.toolCalls && message.toolCalls.length > 0 && (
          <div className="mt-1 w-full">
            {message.toolCalls.map((tc) => (
              <ToolCallCard key={tc.id} onFocusNode={onFocusNode} toolCall={tc} />
            ))}
          </div>
        )}

        {!isUser && message.error && (
          <div className="mt-1.5 flex w-full items-start gap-2 rounded-xl border border-red-100 bg-red-50 px-3 py-2">
            <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-400" strokeWidth={1.8} />
            <p className="min-w-0 flex-1 text-[11.5px] leading-relaxed text-red-600">{message.error}</p>
            {onRetry && (
              <button
                className="shrink-0 flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-red-500 transition-colors hover:bg-red-100 hover:text-red-600"
                onClick={onRetry}
                type="button"
              >
                <RefreshCw className="h-3 w-3" strokeWidth={2} />
                重试
              </button>
            )}
          </div>
        )}

        {message.diffPreview && (
          <div className="mt-1 w-full">
            <FlowDiffPreview
              diff={message.diffPreview}
              onApply={async () => {
                const result = await onApplyDiff(message.diffPreview!);
                if (!result.ok) throw new Error(result.error ?? '应用失败');
                // 延迟清除，先让用户看到成功态
                setTimeout(() => onRejectDiff(message.id), 1500);
              }}
              nodeLookup={nodeLookup}
              onFocusNode={onFocusNode}
              onReject={() => onRejectDiff(message.id)}
              streamingPending={streamingPending}
            />
          </div>
        )}

        {!streamingPending && <MessageTimestamp message={message} />}
      </div>
    </div>
  );
});
