import { CircleAlert, Clock3, Loader2, ShieldAlert } from 'lucide-react';
import type { ReactElement } from 'react';

import { cn } from '../../../lib/utils';
import { Collapsible } from '../../ui/collapsible';
import { Marker, MarkerContent, MarkerIcon } from '../../ui/marker';
import type { AiUsage, ToolCallState } from './aiPanelTypes';
import { ToolCallCard } from './ToolCallCard';

/** 把一轮对话的工具调用折叠成一条「处理中/已处理/需要确认」时间线。 */
function formatProcessingTime(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
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

function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k` : String(n);
}

/** 展开后底部那行用量：轮次是会话是否在空转的第一信号，缓存命中决定同样轮数差几倍成本。 */
function UsageFooter({ usage }: { usage: AiUsage }): ReactElement {
  const cacheRate = usage.prompt_tokens > 0
    ? Math.round((usage.cached_tokens / usage.prompt_tokens) * 100)
    : 0;
  const nearLimit = usage.rounds >= usage.max_rounds - 3;
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-slate-100 px-1 pt-1.5 font-mono text-[10px] tabular-nums text-slate-400">
      <span className={cn(nearLimit && 'text-amber-600')}>
        {usage.rounds}/{usage.max_rounds} 轮
      </span>
      <span>
        {formatTokens(usage.total_tokens)} tokens
        {usage.cached_tokens > 0 && <span className="text-slate-300"> · 缓存 {cacheRate}%</span>}
      </span>
      <span>模型耗时 {usage.llm_seconds}s</span>
      {usage.blocked_calls > 0 && (
        <span className="text-amber-600">{usage.blocked_calls} 次被护栏拦下</span>
      )}
    </div>
  );
}

export function ProcessingTimeline({
  toolCalls,
  processingMs,
  streamingPending,
  usage,
  onFocusNode,
}: {
  toolCalls: ToolCallState[];
  processingMs?: number;
  streamingPending?: boolean;
  usage?: AiUsage;
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
        {usage && <UsageFooter usage={usage} />}
      </div>
    </Collapsible>
  );
}
