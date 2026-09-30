import { Clock3, FileJson, Inbox, Loader2, ScanSearch } from 'lucide-react';
import type { ReactElement } from 'react';

import { formatDateTime, formatElapsedTime } from '../../lib/time';
import { runOutputSummary, TASK_STATUS_META } from '../../lib/runPresentation';
import type { TaskSnapshot } from '../../types/electron';
import { IconButton } from '../ui/button';
import { RefreshIconButton } from '../ui/refresh-button';
import { SurfaceEmpty, Panel, StateTag } from './surfaces';

type RunHistoryListProps = {
  hasMore?: boolean;
  loadingMore?: boolean;
  onInspectRun: (run: TaskSnapshot) => void;
  onLoadMore?: () => void;
  onRefresh: () => void;
  runs: TaskSnapshot[];
  title?: string;
};

export function RunHistoryList({ hasMore, loadingMore, onInspectRun, onLoadMore, onRefresh, runs, title = '最近运行' }: RunHistoryListProps): ReactElement {
  return (
    <Panel
      label={title}
      icon={<Clock3 className="h-3.5 w-3.5" strokeWidth={1.5} />}
      bodyClassName="p-0"
      action={<RefreshIconButton label="刷新历史" onClick={onRefresh} />}
    >
      {runs.length === 0 ? (
        <SurfaceEmpty
          icon={<Inbox className="h-5 w-5" strokeWidth={1.25} />}
          title="暂无运行记录"
          hint="运行任何流程后，执行结果与产物会按时间倒序记录在此。"
        />
      ) : (
        <>
        <div aria-label={title} className="max-h-120 overflow-y-auto" role="table">
          <div className="sticky top-0 z-(--z-sticky) grid grid-cols-[minmax(0,1fr)_104px_92px_84px_148px_44px] items-center gap-3 border-b border-rule-2 bg-surface px-5 py-2.5 text-[11px] font-medium text-ink-3" role="row">
            <span role="columnheader">流程</span>
            <span role="columnheader">状态</span>
            <span role="columnheader">耗时</span>
            <span role="columnheader">输出</span>
            <span role="columnheader">更新时间</span>
            <span className="text-right" role="columnheader">操作</span>
          </div>
          {runs.map((run) => {
            const s = TASK_STATUS_META[run.status];
            return (
              <div
                key={run.taskId}
                className="grid grid-cols-[minmax(0,1fr)_104px_92px_84px_148px_44px] items-center gap-3 border-b border-rule px-5 py-3 transition-colors duration-150 last:border-b-0 hover:bg-paper"
                role="row"
              >
                <div className="min-w-0" role="cell">
                  <div className="truncate text-[12.5px] font-medium text-ink">{run.flowName}</div>
                  <div className="truncate font-mono text-[10px] text-ink-3">{run.taskId}</div>
                </div>
                <div role="cell"><StateTag state={s.tone} label={s.label} /></div>
                <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tabular-nums text-ink-3" role="cell">
                  <Clock3 className="h-3 w-3 text-ink-4" strokeWidth={1.5} />
                  {formatElapsedTime(run.progress.elapsedMs)}
                </span>
                <span className="inline-flex items-center gap-2 font-mono text-[11px] tabular-nums text-ink-3" role="cell">
                  <FileJson className="h-3 w-3 text-ink-4" strokeWidth={1.5} />
                  {runOutputSummary(run)}
                </span>
                <span className="font-mono text-[10.5px] tabular-nums text-ink-3" role="cell">{formatDateTime(run.updatedAt)}</span>
                <div className="flex justify-end" role="cell">
                  <IconButton className="h-7 w-7" label="查看详情" onClick={() => onInspectRun(run)}>
                    <ScanSearch className="h-3.5 w-3.5" strokeWidth={1.5} />
                  </IconButton>
                </div>
              </div>
            );
          })}
        </div>
        {onLoadMore !== undefined && hasMore === true && (
          <div className="border-t border-rule px-5 py-2">
            <button
              className="flex h-8 w-full items-center justify-center gap-1.5 rounded-md text-[11px] font-medium text-ink-3 transition-colors hover:bg-paper-sunk hover:text-ink-2 disabled:opacity-60"
              disabled={loadingMore === true}
              onClick={onLoadMore}
              type="button"
            >
              {loadingMore === true && <Loader2 className="h-3 w-3 animate-spin" strokeWidth={1.5} />}
              {loadingMore === true ? '加载中…' : '加载更多'}
            </button>
          </div>
        )}
        </>
      )}
    </Panel>
  );
}
