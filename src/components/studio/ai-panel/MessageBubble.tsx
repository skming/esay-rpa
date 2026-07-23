import { ArrowUpRight, CircleAlert, Clock3, Loader2, Paperclip, RefreshCw, ShieldAlert } from 'lucide-react';
import type { ReactElement, ReactNode } from 'react';
import { memo, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { cn } from '../../../lib/utils';
import { Collapsible } from '../../ui/collapsible';
import { CodeBlock, InlineCode } from '../../ui/CodeBlock';
import { Marker, MarkerContent, MarkerIcon, ThinkingDots } from '../../ui/marker';
import type { AiAttachment, AiMessage, FlowDiff, NodeLookupItem, ToolCallState } from './aiPanelTypes';
import { FlowDiffPreview } from './FlowDiffPreview';
import { ToolCallCard } from './ToolCallCard';

function MarkdownContent({ text, onFocusNode }: { text: string; onFocusNode?: (nodeId: string) => void }): ReactElement {
  const normalizedText = useMemo(() => compactNodeSummary(linkifyBareUrls(text)), [text]);
  return (
    <div className="markdown-body overflow-x-hidden text-[12px] leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children, ...props }) {
            const hasLang = /language-(\w+)/.test(className ?? '');
            if (hasLang) {
              // pre wrapper below renders this — avoid double output
              return <code className={className} {...props}>{children}</code>;
            }
            const text = String(children);
            if (isNodeId(text)) {
              return (
                <button
                  className={cn(
                    'group/node-id inline-flex items-center gap-0.5 rounded bg-slate-100 px-1 py-0.5 align-baseline font-mono text-[0.92em] font-medium text-slate-800 transition-colors',
                    onFocusNode && 'cursor-pointer hover:bg-accent-soft hover:text-accent-strong focus:outline-none focus:ring-2 focus:ring-accent-line'
                  )}
                  disabled={!onFocusNode}
                  onClick={() => onFocusNode?.(text)}
                  title={onFocusNode ? `定位到 ${text}` : text}
                  type="button"
                >
                  <span className={cn(onFocusNode && 'group-hover/node-id:underline group-hover/node-id:decoration-accent-strong group-hover/node-id:underline-offset-2')}>
                    {children}
                  </span>
                  {onFocusNode && <ArrowUpRight className="h-3 w-3 opacity-60 transition-opacity group-hover/node-id:opacity-100" strokeWidth={1.75} />}
                </button>
              );
            }
            return <InlineCode>{children}</InlineCode>;
          },
          pre({ children }: { children?: ReactNode }) {
            const codeEl = (children as ReactElement | null);
            const className: string = (codeEl?.props as { className?: string } | undefined)?.className ?? '';
            const match = /language-(\w+)/.exec(className);
            const lang = match?.[1] ?? '';
            const rawCode = String((codeEl?.props as { children?: ReactNode })?.children ?? '').replace(/\n$/, '');
            return <CodeBlock className="my-1.5" code={rawCode} language={lang} maxHeight={320} variant="light" />;
          },
          p({ children }: { children?: ReactNode }) {
            return <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>;
          },
          h1({ children }: { children?: ReactNode }) {
            return <h1 className="mb-1.5 mt-2 text-[14px] font-semibold text-slate-800">{children}</h1>;
          },
          h2({ children }: { children?: ReactNode }) {
            return <h2 className="mb-1 mt-2 text-[13px] font-semibold text-slate-800">{children}</h2>;
          },
          h3({ children }: { children?: ReactNode }) {
            return <h3 className="mb-1 mt-1.5 text-[12px] font-semibold text-slate-700">{children}</h3>;
          },
          ul({ children }: { children?: ReactNode }) {
            return <ul className="mb-2 ml-4 list-disc space-y-1">{children}</ul>;
          },
          ol({ children }: { children?: ReactNode }) {
            return <ol className="mb-2 ml-4 list-decimal space-y-1">{children}</ol>;
          },
          li({ children }: { children?: ReactNode }) {
            return <li className="pl-0.5 text-[12px] leading-relaxed">{children}</li>;
          },
          blockquote({ children }: { children?: ReactNode }) {
            return (
              <blockquote className="my-1.5 border-l-[3px] border-slate-300 pl-3 text-slate-500 italic">
                {children}
              </blockquote>
            );
          },
          hr() {
            return <hr className="my-2 border-slate-200" />;
          },
          a({ href, children }: { href?: string; children?: ReactNode }) {
            return (
              <a
                className="font-medium text-accent-strong underline decoration-accent-line underline-offset-2 hover:text-accent-press hover:decoration-accent-strong"
                href={href}
                rel="noopener noreferrer"
                target="_blank"
              >
                {children}
              </a>
            );
          },
          // table-fixed forces the table to obey CSS width; table-layout:auto can overflow
          // the parent even inside overflow-x-auto
          table({ children }: { children?: ReactNode }) {
            return (
              <div className="my-1.5 w-full rounded-sm border border-slate-200">
                <table className="w-full table-fixed border-collapse text-[11px]">{children}</table>
              </div>
            );
          },
          thead({ children }: { children?: ReactNode }) {
            return <thead className="bg-slate-50 [&_tr]:border-b [&_tr]:border-slate-200">{children}</thead>;
          },
          tbody({ children }: { children?: ReactNode }) {
            return <tbody className="[&_tr:last-child]:border-0">{children}</tbody>;
          },
          tr({ children }: { children?: ReactNode }) {
            return <tr className="border-b border-slate-100">{children}</tr>;
          },
          th({ children }: { children?: ReactNode }) {
            return (
              <th className="px-3 py-2 text-left align-middle font-semibold text-slate-700 wrap-break-word">
                {children}
              </th>
            );
          },
          td({ children }: { children?: ReactNode }) {
            return (
              <td className="px-3 py-2 align-middle text-slate-600 wrap-break-word">{children}</td>
            );
          },
          strong({ children }: { children?: ReactNode }) {
            return <strong className="font-semibold text-slate-800">{children}</strong>;
          },
        }}
      >
        {normalizedText}
      </ReactMarkdown>
    </div>
  );
}

// 把裸 URL 包成 markdown 链接以便渲染为可点；跳过代码围栏内的行，避免破坏代码示例里的 URL 文本
function linkifyBareUrls(text: string): string {
  let output = '';
  let inFence = false;
  for (const line of text.split('\n')) {
    if (line.trimStart().startsWith('```')) {
      inFence = !inFence;
      output += `${line}\n`;
      continue;
    }
    output += `${inFence ? line : line.replace(
      /(?<!\]\()(?<!["'`=])(https?:\/\/[^\s<>)\]}，。；、]+)/g,
      (url) => `[${url}](${url})`
    )}\n`;
  }
  return output.trimEnd();
}

// 判断内联代码片段是否是节点 id（如 n1_click），命中后渲染为可点的「定位到节点」按钮
function isNodeId(value: string): boolean {
  if (!/^[A-Za-z][\w-]*$/.test(value)) return false;
  if (!/^n\d+_/.test(value)) return false;
  return true;
}

// 把 AI 回复里「节点标题（`id`·`type`）：说明」这类啰嗦格式压缩成加粗的「标题（id）」，减少视觉噪音
function compactNodeSummary(text: string): string {
  return text
    .split('\n')
    .map((line) => {
      const nodeLine = /^(\s*[-*]\s+)(.+?)（\s*`?([A-Za-z][\w-]*)`?\s*·\s*`?[\w.-]+`?\s*）\s*[：:].*$/.exec(line);
      if (nodeLine) {
        return `${nodeLine[1]}**${nodeLine[2].trim()}（\`${nodeLine[3]}\`）**`;
      }
      return line.replace(/（\s*`?([A-Za-z][\w-]*)`?\s*·\s*`?[\w.-]+`?\s*）/g, '（`$1`）');
    })
    .join('\n');
}

function PlainText({ text }: { text: string }): ReactElement {
  return <span className="whitespace-pre-wrap wrap-break-word text-[12px] leading-relaxed">{text}</span>;
}

function formatProcessingTime(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
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

function buildProcessingSummary(toolCalls: ToolCallState[], processingMs?: number, streamingPending?: boolean): {
  label: string;
  badge: string;
  tone: 'running' | 'error' | 'blocked' | 'done' | 'stopped';
} {
  const errorCount = toolCalls.filter((tc) => tc.status === 'error').length;
  const runningCount = toolCalls.filter((tc) => tc.status === 'running').length;
  const stoppedCount = toolCalls.filter((tc) => tc.status === 'stopped').length;
  const blockedCount = toolCalls.filter((tc) => tc.status === 'blocked').length;
  const doneCount = toolCalls.filter((tc) => tc.status === 'done').length;

  if (streamingPending || runningCount > 0) {
    return {
      label: `处理中${processingMs !== undefined ? ` ${formatProcessingTime(processingMs)}` : ''}`,
      badge: `${doneCount}/${toolCalls.length} 步`,
      tone: 'running',
    };
  }
  if (errorCount > 0) {
    return {
      label: `已处理${processingMs !== undefined ? ` ${formatProcessingTime(processingMs)}` : ''}`,
      badge: `${errorCount} 个异常`,
      tone: 'error',
    };
  }
  // 编排守卫拦截：既不是失败也不该走绿色"已处理"，否则用户扫一眼摘要会以为全部顺利完成
  if (blockedCount > 0) {
    return {
      label: `需要确认${processingMs !== undefined ? ` ${formatProcessingTime(processingMs)}` : ''}`,
      badge: `${blockedCount} 步已阻断`,
      tone: 'blocked',
    };
  }
  if (stoppedCount > 0) {
    return {
      label: `已停止${processingMs !== undefined ? ` ${formatProcessingTime(processingMs)}` : ''}`,
      badge: `${stoppedCount} 步未完成`,
      tone: 'stopped',
    };
  }
  return {
    label: `已处理${processingMs !== undefined ? ` ${formatProcessingTime(processingMs)}` : ''}`,
    badge: `${toolCalls.length} 步`,
    tone: 'done',
  };
}

function ProcessingTimeline({
  toolCalls,
  processingMs,
  streamingPending,
  onFocusNode,
}: {
  toolCalls: ToolCallState[];
  processingMs?: number;
  streamingPending?: boolean;
  onFocusNode?: (nodeId: string) => void;
}): ReactElement {
  const summary = buildProcessingSummary(toolCalls, processingMs, streamingPending);

  return (
    <Collapsible
      chevronVariant="right"
      className={cn(
        'mt-0.5 w-full rounded-none border-x-0 border-t-0 bg-transparent',
        summary.tone === 'error' ? 'border-red-100' : summary.tone === 'blocked' ? 'border-amber-100' : 'border-slate-100'
      )}
      title={(
        <Marker className="text-slate-500">
          {summary.tone === 'running' ? (
            <MarkerIcon className="text-accent">
              <Loader2 className="animate-spin" strokeWidth={1.8} />
            </MarkerIcon>
          ) : summary.tone === 'error' ? (
            <MarkerIcon className="text-red-500">
              <CircleAlert strokeWidth={1.8} />
            </MarkerIcon>
          ) : summary.tone === 'blocked' ? (
            <MarkerIcon className="text-amber-500">
              <ShieldAlert strokeWidth={1.8} />
            </MarkerIcon>
          ) : summary.tone === 'stopped' ? (
            <MarkerIcon className="text-slate-500">
              <Clock3 strokeWidth={1.8} />
            </MarkerIcon>
          ) : null}
          <MarkerContent className="text-[12px] font-medium tabular-nums">{summary.label}</MarkerContent>
        </Marker>
      )}
      badge={(
        <span
          className={cn(
            'font-mono text-[10px] tabular-nums',
            summary.tone === 'error'
              ? 'text-red-500'
              : summary.tone === 'blocked'
                ? 'text-amber-600'
                : 'text-slate-500'
          )}
        >
          {summary.badge}
        </span>
      )}
    >
      <div className="space-y-1">
        {toolCalls.map((tc) => (
          <ToolCallCard key={tc.id} onFocusNode={onFocusNode} toolCall={tc} live={streamingPending} />
        ))}
      </div>
    </Collapsible>
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
