import { ChevronRight, Clock3, Inbox, Loader2 } from 'lucide-react';
import type { ReactElement } from 'react';

import { formatDateTime, formatElapsedTime } from '../../lib/time';
import { runOutputSummary, TASK_STATUS_META } from '../../lib/runPresentation';
import { cn } from '../../lib/utils';
import type { TaskSnapshot } from '../../types/electron';
import { Button, IconButton } from '../ui/button';
import { RefreshIconButton } from '../ui/refresh-button';
import { SurfaceEmpty, Panel, StateTag } from './surfaces';

type RunHistoryListProps = {
  hasMore?: boolean;
  loadingMore?: boolean;
  onInspectRun: (run: TaskSnapshot) => void;
  onLoadMore?: () => void;
  onRefresh: () => void | Promise<void>;
  runs: TaskSnapshot[];
  title?: string;
};

const ROW_GRID = 'grid grid-cols-[minmax(0,1fr)_80px_28px] items-center gap-3 @min-[520px]:grid-cols-[minmax(0,1fr)_88px_64px_28px] @min-[680px]:grid-cols-[minmax(0,1fr)_88px_64px_96px_28px] @min-[860px]:grid-cols-[minmax(0,1fr)_88px_64px_96px_148px_28px]';

export function RunHistoryList({ hasMore, loadingMore, onInspectRun, onLoadMore, onRefresh, runs, title = '最近运行' }: RunHistoryListProps): ReactElement {
  return (
    <Panel
      label={title}
      icon={<Clock3 className="h-3.5 w-3.5" strokeWidth={1.5} />}
      className="@container min-w-0"
      bodyClassName="p-0"
      action={
        <div className="flex items-center gap-3">
          {runs.length > 0 && <span className="text-[11px] tabular-nums text-ink-3">已加载 {runs.length} 条</span>}
          <RefreshIconButton label="刷新历史" onClick={onRefresh} />
        </div>
      }
    >
      {runs.length === 0 ? (
        <SurfaceEmpty
          icon={<Inbox className="h-5 w-5" strokeWidth={1.25} />}
          title="暂无运行记录"
          hint="运行任何流程后，执行结果与产物会按时间倒序记录在此。"
        />
      ) : (
        <>
          <div aria-label={title} role="table">
            <div className={cn(ROW_GRID, 'sticky top-0 z-(--z-sticky) border-b border-rule bg-paper px-4 py-2 text-[11px] font-medium text-ink-3 @min-[520px]:px-5')} role="row">
              <span role="columnheader">流程</span>
              <span role="columnheader">状态</span>
              <span className="hidden text-right @min-[520px]:block" role="columnheader">耗时</span>
              <span className="hidden text-right @min-[680px]:block" role="columnheader">输出</span>
              <span className="hidden text-right @min-[860px]:block" role="columnheader">更新时间</span>
              <span role="columnheader"><span className="sr-only">详情</span></span>
            </div>
            {runs.map((run) => {
              const s = TASK_STATUS_META[run.status];
              const detail = run.status === 'error' ? run.error
                : run.status === 'awaiting_confirmation' ? run.confirmationMessage
                : run.status === 'running' ? `执行进度 ${run.progress.currentStep} / ${run.progress.totalSteps} 步`
                : null;
              const elapsed = formatElapsedTime(run.progress.elapsedMs);
              const output = runOutputSummary(run);
              return (
                <div
                  key={run.taskId}
                  className={cn(ROW_GRID, 'border-b border-rule px-4 py-3 transition-colors duration-150 last:border-b-0 hover:bg-paper focus-within:bg-paper @min-[520px]:px-5')}
                  role="row"
                >
                  <div className="min-w-0" role="cell">
                    <button
                      aria-label={`查看 ${run.flowName} 的运行详情，运行 ID ${run.taskId}`}
                      className="block max-w-full truncate rounded-sm text-left text-[12.5px] font-medium text-ink hover:text-accent-strong hover:underline hover:underline-offset-4"
                      onClick={() => onInspectRun(run)}
                      title={run.flowName}
                      type="button"
                    >
                      {run.flowName}
                    </button>
                    <div
                      className={cn('mt-1 truncate text-[11px] leading-4', detail && run.status === 'error' ? 'text-red-600' : detail && run.status === 'awaiting_confirmation' ? 'text-amber-700' : 'text-ink-3')}
                      title={detail || run.taskId}
                    >
                      {detail || <>{run.mode === 'debug' ? '调试' : '运行'} · <span className="font-mono text-[10px]">{run.taskId.slice(0, 10)}</span></>}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] tabular-nums text-ink-3 @min-[860px]:hidden">
                      <span className="@min-[520px]:hidden">耗时 {elapsed}</span>
                      <span className="@min-[680px]:hidden">输出 {output}</span>
                      <time className="basis-full whitespace-nowrap" dateTime={run.updatedAt}>{formatDateTime(run.updatedAt)}</time>
                    </div>
                  </div>
                  <div role="cell"><StateTag state={s.tone} label={s.label} /></div>
                  <span className="hidden text-right font-mono text-[11px] tabular-nums text-ink-2 @min-[520px]:block" role="cell">
                    {elapsed}
                  </span>
                  <span className="hidden text-right text-[11px] tabular-nums text-ink-2 @min-[680px]:block" role="cell">
                    {output}
                  </span>
                  <span className="hidden text-right font-mono text-[10.5px] tabular-nums text-ink-3 @min-[860px]:block" role="cell"><time dateTime={run.updatedAt}>{formatDateTime(run.updatedAt)}</time></span>
                  <div className="flex justify-end" role="cell">
                    <IconButton className="h-7 w-7" label={`查看 ${run.flowName} 的运行详情（${run.taskId.slice(0, 10)}）`} onClick={() => onInspectRun(run)}>
                      <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.5} />
                    </IconButton>
                  </div>
                </div>
              );
            })}
          </div>
          {onLoadMore !== undefined && hasMore === true && (
            <div className="border-t border-rule px-5 py-2">
              <Button
                className="h-8 w-full"
                aria-busy={loadingMore === true}
                disabled={loadingMore === true}
                onClick={onLoadMore}
                type="button"
              >
                {loadingMore === true && <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin motion-reduce:animate-none" strokeWidth={1.5} />}
                {loadingMore === true ? '加载中…' : '加载更多'}
              </Button>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
