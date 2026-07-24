import { CircleAlert, Clock3, Loader2, ShieldAlert } from 'lucide-react';
import type { ReactElement } from 'react';

import { cn } from '../../../lib/utils';
import { Collapsible } from '../../ui/collapsible';
import { Marker, MarkerContent, MarkerIcon } from '../../ui/marker';
import type { ToolCallState } from './aiPanelTypes';
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

export function ProcessingTimeline({
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
