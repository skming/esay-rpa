import { Eye, Loader2, MoreHorizontal, Pencil, Power, PowerOff, Trash2, Zap } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useRef, useState } from 'react';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { describeCronExpression, describeNextRun, formatScheduleDateTime, hasScheduleError } from '../../lib/schedulePresentation';
import { loadScheduleRunDetails } from '../../lib/scheduleRunDetails';
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
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '../ui/dropdown-menu';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { ScheduleCreateDialog } from '../studio/property-panel/ScheduleCreateDialog';
import { ScheduleRunResultsDialog, type ScheduleRunBatch } from './ScheduleRunResultsDialog';
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
  const [batch, setBatch] = useState<ScheduleRunBatch | null>(null);
  const [batchLoading, setBatchLoading] = useState(false);
  const [pendingScheduleIds, setPendingScheduleIds] = useState(new Set<string>());
  const pendingRef = useRef(new Set<string>());
  // 关闭批次弹窗或再次触发查看时作废在途请求，避免旧请求回来把已关闭的弹窗重新撑开。
  const batchReqRef = useRef(0);
  useEffect(() => () => { batchReqRef.current += 1; }, []);

  const runAction = async (scheduleId: string, action: () => Promise<void>): Promise<void> => {
    if (pendingRef.current.has(scheduleId)) return;
    pendingRef.current.add(scheduleId);
    setPendingScheduleIds((current) => new Set(current).add(scheduleId));
    try {
      await action();
    } catch (error) {
      electron.pushToast('error', error instanceof Error ? error.message : '调度操作失败，请重试');
    } finally {
      pendingRef.current.delete(scheduleId);
      setPendingScheduleIds((current) => {
        const next = new Set(current);
        next.delete(scheduleId);
        return next;
      });
    }
  };

  // 调度只落 lastTaskId 与聚合摘要，没有直挂的任务快照。优先用摘要里有序的 taskIds
  //（「所有流程」批次是多条），拿不到再退到 lastTaskId。
  const resolveTaskIds = (schedule: ScheduleSnapshot): string[] => {
    const summary = runSummaries[schedule.scheduleId];
    if (summary !== undefined && summary.taskIds.length > 0) return summary.taskIds;
    return schedule.lastTaskId !== null && schedule.lastTaskId !== undefined ? [schedule.lastTaskId] : [];
  };

  const openBatch = (schedule: ScheduleSnapshot, ids: string[], runAt?: string | null): void => {
    const req = ++batchReqRef.current;
    setDetailRun(null);
    setBatch({ schedule, taskIds: ids, entries: [], runAt });
    setBatchLoading(true);
    // 后端没有「按 taskId 批量取快照」的轻接口，只能逐个 getRunDetail；批次即「所有流程」的任务数，量小可接受。
    void loadScheduleRunDetails(ids, electron.getRunDetail)
      .then((entries) => {
        if (batchReqRef.current !== req) return;
        if (entries.length === 1 && entries[0].state === 'ready') {
          setBatch(null);
          setDetailRun(entries[0].run);
        } else {
          setBatch({ schedule, taskIds: ids, entries, runAt });
        }
      })
      .finally(() => { if (batchReqRef.current === req) setBatchLoading(false); });
  };

  const closeBatch = (): void => {
    batchReqRef.current += 1;
    setBatch(null);
    setDetailRun(null);
  };

  const viewResult = (schedule: ScheduleSnapshot): void => {
    const ids = resolveTaskIds(schedule);
    if (ids.length === 0) return;
    openBatch(schedule, ids, runSummaries[schedule.scheduleId]?.runAt);
  };

  const runDetail = (
    <RunDetailDialog
      onLoadDetail={electron.getRunDetail}
      onReadArtifact={electron.getArtifactContent}
      onOpenArtifact={(artifact) => void electron.openArtifactPath(artifact.storageUrl)}
      onOpenChange={(open) => { if (!open) setDetailRun(null); }}
      open={detailRun !== null}
      run={detailRun}
    />
  );

  return (
    <>
      <div className={cn('overflow-hidden', SURFACE)}>
        <Table aria-label="调度列表" className="w-full min-w-[760px] table-fixed">
          <TableHeader className="bg-paper-sunk">
            <TableRow className="border-rule-2 hover:bg-transparent">
              <TableHead className="w-[20%] pl-5 text-[11px] font-medium text-ink-2 xl:w-[22%]">调度</TableHead>
              <TableHead className="w-[16%] text-[11px] font-medium text-ink-2 xl:w-[14%]">绑定流程</TableHead>
              <TableHead className="w-[18%] text-[11px] font-medium text-ink-2 xl:w-[14%]">触发规则</TableHead>
              <TableHead className="w-[20%] text-[11px] font-medium text-ink-2 xl:w-[16%]">下次触发</TableHead>
              <TableHead className="hidden w-[14%] text-[11px] font-medium text-ink-2 xl:table-cell">最近运行</TableHead>
              <TableHead className="w-[12%] text-[11px] font-medium text-ink-2 xl:w-[10%]">状态</TableHead>
              <TableHead className="w-[14%] pr-5 text-right text-[11px] font-medium text-ink-2 xl:w-[10%]">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {schedules.map((schedule) => {
              const enabled = schedule.status === 'enabled';
              const pending = pendingScheduleIds.has(schedule.scheduleId);
              const attention = hasScheduleError(schedule, runSummaries[schedule.scheduleId]);
              const cronDescription = describeCronExpression(schedule.cronExpression);
              const runResult = runSummaries[schedule.scheduleId];
              const attentionText = schedule.lastError?.trim() || (runResult
                ? `最近一批${RUN_RESULT_META[runResult.status].label}：成功 ${runResult.success}，失败 ${runResult.failed}，停止 ${runResult.stopped}，共 ${runResult.total} 个流程`
                : '调度存在异常，请检查触发规则');
              return (
                <TableRow
                  className="border-rule hover:bg-paper"
                  key={schedule.scheduleId}
                >
                  <TableCell className="pl-5">
                    <button
                      aria-label={`编辑 ${schedule.name}，调度 ID ${schedule.scheduleId}`}
                      className="block w-full min-w-0 rounded-sm text-left hover:text-accent-strong"
                      onClick={() => setEditSchedule(schedule)}
                      title={schedule.name}
                      type="button"
                    >
                      <span className="block truncate text-[12px] font-medium text-ink-2">{schedule.name}</span>
                    </button>
                  </TableCell>
                  <TableCell>
                    <span className="block truncate text-[11px] font-medium text-ink-2">{schedule.task.flowName}</span>
                  </TableCell>
                  <TableCell>
                    <Tooltip>
                      <TooltipTrigger
                        aria-label={`${cronDescription}，时区 ${schedule.timezone}，编辑触发规则`}
                        className={cn('block min-h-7 w-full truncate rounded-sm text-left text-[11px] text-ink-2', cronDescription === schedule.cronExpression && 'font-mono text-[10px]')}
                        onClick={() => setEditSchedule(schedule)}
                        type="button"
                      >
                        {cronDescription}
                      </TooltipTrigger>
                      <TooltipContent side="top">
                        <p className="font-mono">{schedule.cronExpression}</p>
                        <p className="mt-1">时区 {schedule.timezone}</p>
                      </TooltipContent>
                    </Tooltip>
                  </TableCell>
                  <TableCell className="font-mono text-[10px] tabular-nums text-ink-3" title={`时区 ${schedule.timezone}`}>
                    {describeNextRun(schedule)}
                  </TableCell>
                  <TableCell className="hidden xl:table-cell">
                    <span className="block font-mono text-[10px] tabular-nums text-ink-3">
                      {schedule.lastRunAt === null || schedule.lastRunAt === undefined
                        ? '尚未运行'
                        : formatScheduleDateTime(schedule.lastRunAt, schedule.timezone)}
                    </span>
                    {runResult !== undefined && (
                      <button
                        aria-label={`查看 ${schedule.name} 的运行详情`}
                        className="mt-1 flex items-center gap-1.5 rounded transition-opacity hover:opacity-75 disabled:cursor-default disabled:hover:opacity-100"
                        disabled={resolveTaskIds(schedule).length === 0}
                        onClick={() => viewResult(schedule)}
                        title="查看运行详情"
                        type="button"
                      >
                        <StateTag label={RUN_RESULT_META[runResult.status].label} state={RUN_RESULT_META[runResult.status].tone} />
                        {runResult.total > 1 && (
                          <span className="font-mono text-[10px] tabular-nums text-ink-3">{runResult.success}/{runResult.total}</span>
                        )}
                      </button>
                    )}
                  </TableCell>
                  <TableCell>
                    {attention ? (
                      <Tooltip>
                        <TooltipTrigger
                          aria-label={`需处理，查看 ${schedule.name} 的${resolveTaskIds(schedule).length > 0 ? '运行结果' : '调度配置'}`}
                          className="inline-flex min-h-7 items-center rounded-sm text-left underline decoration-dotted decoration-ink-4 underline-offset-4"
                          onClick={() => resolveTaskIds(schedule).length > 0 ? viewResult(schedule) : setEditSchedule(schedule)}
                          type="button"
                        >
                          <StateTag label="需处理" state="error" />
                        </TooltipTrigger>
                        <TooltipContent className="max-h-[min(20rem,calc(100vh-2rem))] max-w-[min(24rem,calc(100vw-2rem))] overflow-y-auto whitespace-pre-wrap wrap-break-word leading-5" side="top">
                          {attentionText}
                        </TooltipContent>
                      </Tooltip>
                    ) : <StateTag label={enabled ? '启用' : '停用'} state={enabled ? 'success' : 'idle'} />}
                  </TableCell>
                  <TableCell className="pr-5">
                    <div className="flex justify-end gap-1" aria-busy={pending}>
                      <IconButton
                        disabled={pending}
                        label={pending ? `正在更新 ${schedule.name}` : `${enabled ? '停用' : '启用'} ${schedule.name}`}
                        onClick={() => void runAction(schedule.scheduleId, () => electron.updateScheduleEnabled(schedule.scheduleId, !enabled))}
                      >
                        {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : enabled ? <PowerOff className="h-3.5 w-3.5" strokeWidth={1.5} /> : <Power className="h-3.5 w-3.5" strokeWidth={1.5} />}
                      </IconButton>
                      <DropdownMenu>
                        <DropdownMenuTrigger disabled={pending} render={<IconButton label={`${schedule.name} 的更多操作`} />}>
                          <MoreHorizontal className="h-3.5 w-3.5" strokeWidth={1.5} />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-40">
                          <DropdownMenuItem
                            disabled={resolveTaskIds(schedule).length === 0}
                            onClick={() => viewResult(schedule)}
                          >
                            <Eye className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />
                            查看运行详情
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            disabled={pending}
                            onClick={() => void runAction(schedule.scheduleId, () => electron.updateScheduleEnabled(schedule.scheduleId, !enabled))}
                          >
                            {enabled
                              ? <PowerOff className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />
                              : <Power className="mr-2 h-3.5 w-3.5 text-emerald-600" strokeWidth={1.5} />}
                            {enabled ? '停用调度' : '启用调度'}
                          </DropdownMenuItem>
                          <DropdownMenuItem disabled={pending} onClick={() => void runAction(schedule.scheduleId, () => electron.triggerSchedule(schedule.scheduleId))}>
                            <Zap className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />
                            立即触发
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => setEditSchedule(schedule)}>
                            <Pencil className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />
                            编辑调度
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            className="text-red-600 focus:text-red-600"
                            onClick={() => setDeleteSchedule(schedule)}
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

      <ScheduleRunResultsDialog
        batch={batch}
        loading={batchLoading}
        onClose={closeBatch}
        onInspectRun={setDetailRun}
        onRefresh={() => { if (batch !== null) openBatch(batch.schedule, batch.taskIds, batch.runAt); }}
      >
        {batch !== null && runDetail}
      </ScheduleRunResultsDialog>
      {batch === null && runDetail}
    </>
  );
}
