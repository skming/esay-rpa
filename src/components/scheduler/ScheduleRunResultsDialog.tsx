import type { ReactElement, ReactNode } from 'react';

import { TASK_STATUS_META } from '../../lib/runPresentation';
import type { ScheduleRunEntry } from '../../lib/scheduleRunDetails';
import { formatScheduleDateTime } from '../../lib/schedulePresentation';
import type { ScheduleSnapshot, TaskSnapshot } from '../../types/electron';
import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { RunHistoryList } from '../workspace/RunHistoryList';
import { SurfaceLoading } from '../workspace/surfaces';

export type ScheduleRunBatch = {
  schedule: ScheduleSnapshot;
  taskIds: string[];
  entries: ScheduleRunEntry[];
  runAt?: string | null;
};

export function ScheduleRunResultsDialog({ batch, loading, onClose, onInspectRun, onRefresh, children }: {
  batch: ScheduleRunBatch | null;
  loading: boolean;
  onClose: () => void;
  onInspectRun: (run: TaskSnapshot) => void;
  onRefresh: () => void;
  children?: ReactNode;
}): ReactElement {
  const runs = batch?.entries.flatMap((entry) => entry.state === 'ready' ? [entry.run] : []) ?? [];
  const unavailable = batch?.entries.filter((entry) => entry.state !== 'ready') ?? [];
  const errors = unavailable.filter((entry) => entry.state === 'error').length;
  const counts = Object.entries(TASK_STATUS_META).flatMap(([status, meta]) => {
    const count = runs.filter((run) => run.status === status).length;
    return count > 0 ? [`${meta.label} ${count}`] : [];
  });
  const runAt = batch?.runAt ?? batch?.schedule.lastRunAt;
  return (
    <Dialog onOpenChange={(open) => { if (!open) onClose(); }} open={batch !== null}>
      <DialogContent className="w-200 max-h-[calc(100vh-48px)]">
        <DialogHeader className="border-b border-rule">
          <DialogTitle className="min-w-0 wrap-break-word leading-5">运行结果 · {batch?.schedule.name}</DialogTitle>
          <DialogDescription>
            {runAt ? `触发于 ${formatScheduleDateTime(runAt, batch?.schedule.timezone)}` : '最近一批运行'}
            {batch !== null && <span className="ml-2">{batch.schedule.timezone}</span>}
          </DialogDescription>
          <p className="text-[11px] text-ink-3" role="status" aria-live="polite">
            共 {batch?.taskIds.length ?? 0} 个流程{!loading && counts.length > 0 ? ` · ${counts.join(' · ')}` : ''}
            {!loading && unavailable.length > 0 ? ` · 记录不可用 ${unavailable.length}` : ''}
          </p>
        </DialogHeader>
        <DialogBody className="p-0" aria-busy={loading}>
          {loading ? <SurfaceLoading label="正在读取运行结果…" /> : (
            <>
              {runs.length > 0 && <RunHistoryList
                embedded
                formatTime={(value) => formatScheduleDateTime(value, batch?.schedule.timezone)}
                onInspectRun={onInspectRun}
                onRefresh={onRefresh}
                runs={runs}
                title="本批运行流程"
              />}
              {unavailable.length > 0 && (
                <div className="space-y-3 px-5 py-4">
                  <p className="text-[12px] font-medium text-ink-2">
                    {errors > 0 ? `${errors} 条运行记录读取失败，可重试` : runs.length > 0 ? '部分运行记录已不可用' : '运行记录不可用'}
                  </p>
                  {unavailable.map((entry) => (
                    <div className="min-w-0" key={entry.taskId}>
                      <p className="whitespace-pre-wrap wrap-break-word text-[11px] text-ink-3">{entry.message}</p>
                      <span className="mt-0.5 block break-all font-mono text-[10px] text-ink-3">运行 ID {entry.taskId}</span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </DialogBody>
        <DialogFooter>
          {runs.length > 0 && <p className="mr-auto text-[11px] text-ink-3">点击流程查看日志与产物</p>}
          <Button disabled={loading} onClick={onRefresh} size="sm" variant="ghost">{errors > 0 ? '重试读取' : '刷新结果'}</Button>
          <Button onClick={onClose} size="sm" variant="subtle">关闭</Button>
        </DialogFooter>
      </DialogContent>
      {children}
    </Dialog>
  );
}
