import { Dialog as DialogPrimitive } from '@base-ui/react/dialog';
import { CheckCircle2, Clock3, FileJson, History, Inbox, Loader2, ScanSearch, XCircle, X } from 'lucide-react';
import type { ReactElement } from 'react';

import { formatDateTime, formatElapsedTime } from '../../lib/time';
import { runOutputSummary, TASK_STATUS_META } from '../../lib/runPresentation';
import type { TaskSnapshot } from '../../types/electron';
import { Button, IconButton } from '../ui/button';
import { RefreshIconButton } from '../ui/refresh-button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../ui/tooltip';
import { StateTag } from './surfaces';

type Props = {
  flowName: string;
  hasMore?: boolean;
  loadingMore?: boolean;
  onClose: () => void;
  onInspectRun: (run: TaskSnapshot) => void;
  onLoadMore?: () => void;
  onRefresh: () => void | Promise<void>;
  open: boolean;
  runs: TaskSnapshot[];
};

export function RunHistoryDrawer({ flowName, hasMore, loadingMore, onClose, onInspectRun, onLoadMore, onRefresh, open, runs }: Props): ReactElement {
  const successCount = runs.filter((r) => r.status === 'success').length;
  const errorCount = runs.filter((r) => r.status === 'error').length;

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-(--z-drawer-backdrop) bg-slate-950/20 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0 data-open:duration-200 data-closed:duration-150" />

        <DialogPrimitive.Popup
          aria-describedby={undefined}
          className="fixed right-0 top-0 z-(--z-drawer) flex h-full w-175 max-w-[95vw] flex-col bg-surface shadow-2xl outline-none border-l border-rule data-open:animate-in data-open:slide-in-from-right data-open:duration-300 data-closed:animate-out data-closed:slide-out-to-right data-closed:duration-200"
        >
          <div className="flex shrink-0 items-start justify-between gap-4 border-b border-rule px-5 py-4">
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft">
                <History className="h-4 w-4 text-accent" strokeWidth={1.5} />
              </div>
              <div className="min-w-0">
                <DialogPrimitive.Title className="text-[13px] font-semibold leading-tight text-ink truncate">
                  运行历史
                </DialogPrimitive.Title>
                <p className="mt-0.5 truncate text-[11px] text-ink-3">{flowName}</p>
              </div>
            </div>

            <div className="flex shrink-0 items-center gap-3">
              {runs.length > 0 && (
                <div className="flex items-center gap-2.5 rounded-md border border-rule bg-paper-sunk px-3 py-1.5">
                  <Stat icon={<CheckCircle2 className="h-3 w-3 text-emerald-500" strokeWidth={1.5} />} label="成功" value={successCount} />
                  <div className="h-3 w-px bg-rule-2" />
                  <Stat icon={<XCircle className="h-3 w-3 text-red-400" strokeWidth={1.5} />} label="失败" value={errorCount} />
                  <div className="h-3 w-px bg-rule-2" />
                  <Stat label="共" value={runs.length} />
                </div>
              )}
              <RefreshIconButton label="刷新历史" onClick={onRefresh} />
              <DialogPrimitive.Close render={<IconButton label="关闭" />}>
                <X className="h-3.5 w-3.5" strokeWidth={1.5} />
              </DialogPrimitive.Close>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {runs.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
                <Inbox className="h-8 w-8 text-ink-4" strokeWidth={1.25} />
                <div>
                  <p className="text-[13px] font-medium text-ink-2">暂无运行记录</p>
                  <p className="mt-1 text-[11px] text-ink-3">运行该流程后，执行结果将按时间倒序显示在此。</p>
                </div>
              </div>
            ) : (
              <TooltipProvider delay={400}>
                <div className="sticky top-0 z-(--z-sticky) grid grid-cols-[56px_92px_80px_88px_minmax(0,1fr)_44px] items-center gap-3 border-b border-rule-2 bg-paper-sunk px-5 py-2.5 text-[11px] font-medium text-ink-3">
                  <span>状态</span>
                  <span>耗时</span>
                  <span>步骤</span>
                  <span>输出</span>
                  <span>时间</span>
                  <span className="text-right">详情</span>
                </div>

                {runs.map((run) => {
                  const s = TASK_STATUS_META[run.status];
                  return (
                    <div
                      key={run.taskId}
                      className="grid grid-cols-[56px_92px_80px_88px_minmax(0,1fr)_44px] items-center gap-3 border-b border-rule px-5 py-3 transition-colors last:border-b-0 hover:bg-paper"
                    >
                      <StateTag state={s.tone} label={s.label} />

                      <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tabular-nums text-ink-3">
                        <Clock3 className="h-3 w-3 text-ink-4" strokeWidth={1.5} />
                        {formatElapsedTime(run.progress.elapsedMs)}
                      </span>

                      <span className="font-mono text-[11px] tabular-nums text-ink-3">
                        {run.progress.currentStep}<span className="text-ink-4">/{run.progress.totalSteps}</span>
                      </span>

                      <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tabular-nums text-ink-3">
                        <FileJson className="h-3 w-3 text-ink-4" strokeWidth={1.5} />
                        {runOutputSummary(run)}
                      </span>

                      <div className="min-w-0">
                        <div className="truncate font-mono text-[11px] tabular-nums text-ink-3">
                          {formatDateTime(run.updatedAt)}
                        </div>
                        <div className="truncate font-mono text-[10px] text-ink-3">{run.taskId}</div>
                      </div>

                      <div className="flex justify-end">
                        <Tooltip>
                          <TooltipTrigger render={<Button aria-label="查看详情" className="h-7 w-7 text-slate-500 hover:bg-slate-100 hover:text-slate-700" onClick={() => onInspectRun(run)} size="icon" variant="ghost" />}>
                            <ScanSearch className="h-3.5 w-3.5" strokeWidth={1.5} />
                          </TooltipTrigger>
                          <TooltipContent side="left">查看运行详情</TooltipContent>
                        </Tooltip>
                      </div>
                    </div>
                  );
                })}
              </TooltipProvider>
            )}
          </div>

          {onLoadMore !== undefined && hasMore === true && (
            <div className="shrink-0 border-t border-rule px-5 py-2.5">
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
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function Stat({ icon, label, value }: { icon?: ReactElement; label: string; value: number }): ReactElement {
  return (
    <span className="inline-flex items-center gap-1 text-[11px]">
      {icon}
      <span className="font-mono font-semibold tabular-nums text-ink-2">{value}</span>
      <span className="text-ink-4">{label}</span>
    </span>
  );
}
