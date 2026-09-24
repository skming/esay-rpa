import { AlertTriangle, Eye, MoreHorizontal, Pencil, Power, PowerOff, Trash2, Zap } from 'lucide-react';
import type { ReactElement } from 'react';
import { useRef, useState } from 'react';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { describeCronExpression, describeNextRun, formatScheduleDateTime, hasScheduleError } from '../../lib/schedulePresentation';
import type { ScheduleRunSummary, ScheduleSnapshot, TaskSnapshot } from '../../types/electron';
import { IconButton } from '../ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../ui/alert-dialog';
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from '../ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '../ui/dropdown-menu';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { ScheduleCreateDialog } from '../studio/property-panel/ScheduleCreateDialog';
import { RunDetailDialog } from '../workspace/RunDetailDialog';
import { StateTag, SURFACE, type StatusTone } from '../workspace/surfaces';
import { cn } from '../../lib/utils';

// 最近一批的终态 → 文案与语义色；「所有流程」批次的 total 可 >1，故失败与部分成功要分开呈现。
const RUN_RESULT_META: Record<ScheduleRunSummary['status'], { label: string; tone: StatusTone }> = {
  running: { label: '运行中', tone: 'live' },
  success: { label: '成功', tone: 'success' },
  failed: { label: '失败', tone: 'error' },
  partial: { label: '部分成功', tone: 'warning' },
  stopped: { label: '已停止', tone: 'idle' },
  empty: { label: '无结果', tone: 'idle' },
};

// 批次列表里单个任务的终态；与 RunDetailDialog 顶部状态语义保持一致。
const TASK_STATUS_META: Record<TaskSnapshot['status'], { label: string; tone: StatusTone }> = {
  queued: { label: '排队', tone: 'idle' },
  running: { label: '运行中', tone: 'live' },
  success: { label: '成功', tone: 'success' },
  stopped: { label: '已停止', tone: 'idle' },
  error: { label: '失败', tone: 'error' },
  awaiting_confirmation: { label: '等待操作', tone: 'warning' },
};

export function ScheduleListTable({
  electron,
  runSummaries,
  schedules,
}: {
  electron: ElectronBridgeState;
  runSummaries: Record<string, ScheduleRunSummary>;
  schedules: ScheduleSnapshot[];
}): ReactElement {
  const [editSchedule, setEditSchedule] = useState<ScheduleSnapshot | null>(null);
  const [deleteSchedule, setDeleteSchedule] = useState<ScheduleSnapshot | null>(null);
  const [detailRun, setDetailRun] = useState<TaskSnapshot | null>(null);
  const [batch, setBatch] = useState<{ schedule: ScheduleSnapshot; tasks: TaskSnapshot[] } | null>(null);
  const [batchLoading, setBatchLoading] = useState(false);
  // 关闭批次弹窗或再次触发查看时作废在途请求，避免旧请求回来把已关闭的弹窗重新撑开。
  const batchReqRef = useRef(0);
  const detailReqRef = useRef(0);

  // 调度只落 lastTaskId 与聚合摘要，没有直挂的任务快照。优先用摘要里有序的 taskIds
  //（「所有流程」批次是多条），拿不到再退到 lastTaskId。
  const resolveTaskIds = (schedule: ScheduleSnapshot): string[] => {
    const summary = runSummaries[schedule.scheduleId];
    if (summary !== undefined && summary.taskIds.length > 0) return summary.taskIds;
    return schedule.lastTaskId !== null && schedule.lastTaskId !== undefined ? [schedule.lastTaskId] : [];
  };

  const openDetail = (taskId: string): void => {
    const req = ++detailReqRef.current;
    void electron.getRunDetail(taskId).then(({ run }) => {
      if (detailReqRef.current === req) setDetailRun(run);
    });
  };

  const openBatch = (schedule: ScheduleSnapshot, ids: string[]): void => {
    const req = ++batchReqRef.current;
    setBatch({ schedule, tasks: [] });
    setBatchLoading(true);
    // 后端没有「按 taskId 批量取快照」的轻接口，只能逐个 getRunDetail；批次即「所有流程」的任务数，量小可接受。
    void Promise.all(ids.map((id) => electron.getRunDetail(id).then(({ run }) => run).catch(() => null)))
      .then((runs) => {
        if (batchReqRef.current !== req) return;
        setBatch({ schedule, tasks: runs.filter((run): run is TaskSnapshot => run !== null) });
      })
      .finally(() => { if (batchReqRef.current === req) setBatchLoading(false); });
  };

  const viewResult = (schedule: ScheduleSnapshot): void => {
    const ids = resolveTaskIds(schedule);
    if (ids.length === 0) return;
    if (ids.length === 1) { openDetail(ids[0]); return; }
    openBatch(schedule, ids);
  };

  return (
    <>
      <div className={cn('overflow-hidden', SURFACE)}>
        <Table className="w-full min-w-0 table-fixed">
          <TableHeader className="bg-paper-sunk">
            <TableRow className="border-rule-2 hover:bg-transparent">
              <TableHead className="w-[48%] pl-5 text-[11px] font-medium text-ink-2">调度</TableHead>
              <TableHead className="w-[20%] text-[11px] font-medium text-ink-2">绑定流程</TableHead>
              <TableHead className="w-[20%] text-[11px] font-medium text-ink-2">触发规则</TableHead>
              <TableHead className="w-[20%] text-[11px] font-medium text-ink-2">下次触发</TableHead>
              <TableHead className="hidden w-[22%] text-[11px] font-medium text-ink-2 xl:table-cell">最近运行</TableHead>
              <TableHead className="w-[20%] text-[11px] font-medium text-ink-2">状态</TableHead>
              <TableHead className="w-[11%] pr-5 text-right text-[11px] font-medium text-ink-2">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {schedules.map((schedule) => {
              const enabled = schedule.status === 'enabled';
              const attention = hasScheduleError(schedule, runSummaries[schedule.scheduleId]);
              const cronDescription = describeCronExpression(schedule.cronExpression);
              const runResult = runSummaries[schedule.scheduleId];
              const attentionText = schedule.lastError ?? (runResult ? RUN_RESULT_META[runResult.status].label : '');
              return (
                <TableRow
                  className={cn(
                    'border-rule hover:bg-paper',
                    !enabled && 'opacity-60',
                    attention && 'bg-red-50/40 hover:bg-red-50/60',
                  )}
                  key={schedule.scheduleId}
                >
                  <TableCell className="pl-5">
                    <button className="block w-full min-w-0 text-left" onClick={() => setEditSchedule(schedule)} type="button">
                      <span className="block truncate text-[12px] font-medium text-ink">{schedule.name}</span>
                      <span className="mt-0.5 block max-w-full truncate font-mono text-[10px] text-ink-3" title={schedule.scheduleId}>
                        {schedule.scheduleId}
                      </span>
                    </button>
                    {attention && (
                      <span className="mt-1 flex items-center gap-1 text-[10px] text-red-600" title={attentionText}>
                        <AlertTriangle className="h-3 w-3 shrink-0" strokeWidth={1.5} />
                        <span className="truncate">{attentionText}</span>
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <span className="block truncate text-[11px] font-medium text-ink-2">{schedule.task.flowName}</span>
                    <span className="mt-0.5 block truncate text-[10px] text-ink-3">{schedule.timezone}</span>
                  </TableCell>
                  <TableCell>
                    <span className={cn(
                      'block truncate text-[11px] text-ink-2',
                      cronDescription === schedule.cronExpression && 'font-mono text-[10px]',
                    )}>
                      {cronDescription}
                    </span>
                    {cronDescription !== schedule.cronExpression && (
                      <span className="mt-0.5 block truncate font-mono text-[10px] text-ink-3">{schedule.cronExpression}</span>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-[10px] tabular-nums text-ink-3">
                    {describeNextRun(schedule)}
                  </TableCell>
                  <TableCell className="hidden xl:table-cell">
                    <span className="block font-mono text-[10px] tabular-nums text-ink-3">
                      {schedule.lastRunAt === null || schedule.lastRunAt === undefined
                        ? '尚未运行'
                        : formatScheduleDateTime(schedule.lastRunAt)}
                    </span>
                    {runResult !== undefined && (
                      <button
                        className="mt-1 flex items-center gap-1.5 rounded transition-opacity hover:opacity-75 disabled:cursor-default disabled:hover:opacity-100"
                        disabled={resolveTaskIds(schedule).length === 0}
                        onClick={() => viewResult(schedule)}
                        title="查看运行详情"
                        type="button"
                      >
                        <StateTag label={RUN_RESULT_META[runResult.status].label} state={RUN_RESULT_META[runResult.status].tone} />
                        {runResult.total > 1 && (
                          <span className="font-mono text-[10px] tabular-nums text-ink-4">{runResult.success}/{runResult.total}</span>
                        )}
                      </button>
                    )}
                  </TableCell>
                  <TableCell>
                    <StateTag
                      label={attention ? '需处理' : enabled ? '启用' : '停用'}
                      state={attention ? 'error' : enabled ? 'success' : 'idle'}
                    />
                  </TableCell>
                  <TableCell className="pr-5">
                    <div className="flex justify-end">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <IconButton label="更多操作">
                            <MoreHorizontal className="h-3.5 w-3.5" strokeWidth={1.5} />
                          </IconButton>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-40">
                          <DropdownMenuItem
                            disabled={resolveTaskIds(schedule).length === 0}
                            onSelect={() => viewResult(schedule)}
                          >
                            <Eye className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />
                            查看运行详情
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            onSelect={() => void electron.updateScheduleEnabled(schedule.scheduleId, !enabled)}
                          >
                            {enabled
                              ? <PowerOff className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />
                              : <Power className="mr-2 h-3.5 w-3.5 text-emerald-600" strokeWidth={1.5} />}
                            {enabled ? '停用调度' : '启用调度'}
                          </DropdownMenuItem>
                          <DropdownMenuItem onSelect={() => void electron.triggerSchedule(schedule.scheduleId)}>
                            <Zap className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />
                            立即触发
                          </DropdownMenuItem>
                          <DropdownMenuItem onSelect={() => setEditSchedule(schedule)}>
                            <Pencil className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />
                            编辑调度
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            className="text-red-600 focus:text-red-600"
                            onSelect={() => setDeleteSchedule(schedule)}
                          >
                            <Trash2 className="mr-2 h-3.5 w-3.5" strokeWidth={1.5} />
                            删除调度
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      <ScheduleCreateDialog
        flows={electron.flows}
        onOpenChange={(open) => { if (!open) setEditSchedule(null); }}
        onPreview={electron.previewSchedule}
        onUpdate={electron.updateSchedule}
        open={editSchedule !== null}
        schedule={editSchedule ?? undefined}
      />

      <AlertDialog onOpenChange={(open) => { if (!open) setDeleteSchedule(null); }} open={deleteSchedule !== null}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除调度</AlertDialogTitle>
            <AlertDialogDescription>
              将删除「{deleteSchedule?.name ?? '当前调度'}」。绑定流程不会被删除，此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleteSchedule !== null) void electron.deleteSchedule(deleteSchedule.scheduleId);
                setDeleteSchedule(null);
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog onOpenChange={(open) => { if (!open) { batchReqRef.current += 1; setBatch(null); } }} open={batch !== null}>
        <DialogContent className="flex max-h-[min(70vh,560px)] w-140 max-w-[calc(100vw-32px)] flex-col overflow-hidden">
          <DialogHeader>
            <DialogTitle>本批运行结果 · {batch?.schedule.name}</DialogTitle>
          </DialogHeader>
          <DialogBody className="min-h-0 flex-1 space-y-1.5 overflow-y-auto">
            {batchLoading && <p className="text-[11px] text-ink-3">正在读取本批任务…</p>}
            {!batchLoading && batch?.tasks.length === 0 && (
              <p className="rounded-md bg-paper-sunk px-3 py-3 text-[11px] text-ink-3">没有可查看的任务记录</p>
            )}
            {batch?.tasks.map((task) => (
              <button
                className="flex w-full items-center justify-between gap-3 rounded-md border border-rule bg-paper px-3 py-2 text-left transition-colors hover:bg-paper-sunk"
                key={task.taskId}
                onClick={() => setDetailRun(task)}
                type="button"
              >
                <span className="min-w-0">
                  <span className="block truncate text-[12px] font-medium text-ink">{task.flowName}</span>
                  <span className="block truncate font-mono text-[10px] text-ink-3">{task.taskId}</span>
                </span>
                <StateTag label={TASK_STATUS_META[task.status].label} state={TASK_STATUS_META[task.status].tone} />
              </button>
            ))}
          </DialogBody>
        </DialogContent>
      </Dialog>

      <RunDetailDialog
        onLoadDetail={electron.getRunDetail}
        onReadArtifact={electron.getArtifactContent}
        onOpenArtifact={(artifact) => void electron.openArtifactPath(artifact.storageUrl)}
        onOpenChange={(open) => { if (!open) setDetailRun(null); }}
        open={detailRun !== null}
        run={detailRun}
      />
    </>
  );
}
