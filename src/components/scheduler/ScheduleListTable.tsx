import { AlertTriangle, MoreHorizontal, Pencil, Power, PowerOff, Trash2, Zap } from 'lucide-react';
import type { ReactElement } from 'react';
import { useState } from 'react';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { describeCronExpression, describeNextRun, formatScheduleDateTime, hasScheduleError } from '../../lib/schedulePresentation';
import type { ScheduleSnapshot } from '../../types/electron';
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
import { ScheduleCreateDialog } from '../studio/property-panel/ScheduleCreateDialog';
import { StateTag, SURFACE } from '../workspace/surfaces';
import { cn } from '../../lib/utils';

export function ScheduleListTable({
  electron,
  schedules,
}: {
  electron: ElectronBridgeState;
  schedules: ScheduleSnapshot[];
}): ReactElement {
  const [editSchedule, setEditSchedule] = useState<ScheduleSnapshot | null>(null);
  const [deleteSchedule, setDeleteSchedule] = useState<ScheduleSnapshot | null>(null);

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
              <TableHead className="w-[20%] text-[11px] font-medium text-ink-2">状态</TableHead>
              <TableHead className="w-[11%] pr-5 text-right text-[11px] font-medium text-ink-2">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {schedules.map((schedule) => {
              const enabled = schedule.status === 'enabled';
              const attention = hasScheduleError(schedule);
              const cronDescription = describeCronExpression(schedule.cronExpression);
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
                      <span className="mt-1 flex items-center gap-1 text-[10px] text-red-600" title={schedule.lastError ?? undefined}>
                        <AlertTriangle className="h-3 w-3 shrink-0" strokeWidth={1.5} />
                        <span className="truncate">{schedule.lastError}</span>
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
                  <TableCell className="hidden font-mono text-[10px] tabular-nums text-ink-3 xl:table-cell">
                    {schedule.lastRunAt === null || schedule.lastRunAt === undefined
                      ? '尚未运行'
                      : formatScheduleDateTime(schedule.lastRunAt)}
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
        onUpdate={(scheduleId, options) => void electron.updateSchedule(scheduleId, options)}
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
    </>
  );
}
