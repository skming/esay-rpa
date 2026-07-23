import { Clock3, FileJson, Inbox, ScanSearch, Trash2 } from 'lucide-react';
import type { ReactElement } from 'react';

import { formatElapsedTime } from '../../lib/time';
import type { TaskSnapshot } from '../../types/electron';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel,
  AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger
} from '../ui/alert-dialog';
import { Button, IconButton } from '../ui/button';
import { RefreshIconButton } from '../ui/refresh-button';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { SurfaceEmpty, Panel, StateTag } from './surfaces';
import type { StatusTone } from './surfaces';

type RunHistoryListProps = {
  onClear?: () => void;
  onInspectRun: (run: TaskSnapshot) => void;
  onRefresh: () => void;
  runs: TaskSnapshot[];
  title?: string;
};

export function RunHistoryList({ onClear, onInspectRun, onRefresh, runs, title = '最近运行' }: RunHistoryListProps): ReactElement {
  return (
    <Panel
      label={title}
      icon={<Clock3 className="h-3.5 w-3.5" strokeWidth={1.5} />}
      bodyClassName="p-0"
      action={
        <div className="flex items-center gap-1">
          {onClear !== undefined && runs.length > 0 && (
            <AlertDialog>
              <Tooltip>
                <TooltipTrigger asChild>
                  <AlertDialogTrigger asChild>
                    <Button
                      aria-label="清除历史"
                      className="h-7 w-7 border border-transparent text-ink-4 hover:bg-paper-sunk hover:text-red-500"
                      size="icon"
                      variant="ghost"
                    >
                      <Trash2 className="h-3.5 w-3.5" strokeWidth={1.5} />
                    </Button>
                  </AlertDialogTrigger>
                </TooltipTrigger>
                <TooltipContent side="bottom">清除历史</TooltipContent>
              </Tooltip>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>清除运行历史</AlertDialogTitle>
                  <AlertDialogDescription>
                    将清空当前列表中的 {runs.length} 条记录，历史数据仍保留在后端，刷新后可重新加载。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction onClick={onClear}>确认清除</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
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
        <div className="max-h-120 overflow-y-auto">
          <div className="sticky top-0 z-(--z-sticky) grid grid-cols-[minmax(0,1fr)_104px_92px_84px_148px_44px] items-center gap-3 border-b border-rule-2 bg-surface px-5 py-2.5 text-[11px] font-medium text-ink-3">
            <span>流程</span>
            <span>状态</span>
            <span>耗时</span>
            <span>输出</span>
            <span>更新时间</span>
            <span className="text-right">操作</span>
          </div>
          {runs.map((run) => {
            const s = RUN_STATE[run.status];
            return (
              <div
                key={run.taskId}
                className="grid grid-cols-[minmax(0,1fr)_104px_92px_84px_148px_44px] items-center gap-3 border-b border-rule px-5 py-3 transition-colors duration-150 last:border-b-0 hover:bg-paper"
              >
                <div className="min-w-0">
                  <div className="truncate text-[12.5px] font-medium text-ink">{run.flowName}</div>
                  <div className="truncate font-mono text-[10px] text-ink-3">{run.taskId}</div>
                </div>
                <StateTag state={s.state} label={s.label} />
                <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tabular-nums text-ink-3">
                  <Clock3 className="h-3 w-3 text-ink-4" strokeWidth={1.5} />
                  {formatElapsedTime(run.progress.elapsedMs)}
                </span>
                <span className="inline-flex items-center gap-2 font-mono text-[11px] tabular-nums text-ink-3">
                  <FileJson className="h-3 w-3 text-ink-4" strokeWidth={1.5} />
                  {run.variables?.length ?? 0}
                  <span className="text-ink-4">/</span>
                  {run.artifacts?.length ?? 0}
                </span>
                <span className="font-mono text-[10.5px] tabular-nums text-ink-3">{formatDateTime(run.updatedAt)}</span>
                <div className="flex justify-end">
                  <IconButton className="h-7 w-7" label="查看详情" onClick={() => onInspectRun(run)}>
                    <ScanSearch className="h-3.5 w-3.5" strokeWidth={1.5} />
                  </IconButton>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

const RUN_STATE: Record<TaskSnapshot['status'], { state: StatusTone; label: string }> = {
  success: { state: 'success', label: '成功' },
  error: { state: 'error', label: '失败' },
  running: { state: 'live', label: '运行中' },
  queued: { state: 'warning', label: '排队' },
  stopped: { state: 'idle', label: '已停止' },
  paused_for_human: { state: 'warning', label: '等待操作' },
};

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}
