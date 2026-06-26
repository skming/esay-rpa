import { Clock3, MoreHorizontal, Pencil, PauseCircle, PlayCircle, Trash2, Zap } from 'lucide-react';
import type { ReactElement } from 'react';
import { useState } from 'react';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { describeCronExpression, formatScheduleDateTime } from '../../lib/schedulePresentation';
import type { ScheduleSnapshot } from '../../types/electron';
import { StateTag } from '../workspace/ledger';
import { Button, IconButton } from '../ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import { Switch } from '../ui/switch';
import { ScheduleCreateDialog } from '../studio/property-panel/ScheduleCreateDialog';

export function ScheduleTaskCard({
  electron,
  schedule,
}: {
  electron: ElectronBridgeState;
  schedule: ScheduleSnapshot;
}): ReactElement {
  const enabled = schedule.status === 'enabled';
  const [editOpen, setEditOpen] = useState(false);

  return (
    <article className="grid gap-4 rounded-md border border-rule bg-surface p-5 transition-colors duration-150 hover:border-rule-2">

      {/* 顶部：标题 + 控件 */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-3">
            <h3 className="truncate text-[13.5px] font-semibold text-ink">{schedule.name}</h3>
            <StateTag state={enabled ? 'success' : 'idle'} label={enabled ? '启用' : '停用'} />
          </div>
          <div className="mt-2 flex min-w-0 items-center gap-2 text-[11.5px] text-ink-3">
            <Clock3 className="h-3.5 w-3.5 shrink-0 text-ink-4" strokeWidth={1.5} />
            <span className="truncate">{describeCronExpression(schedule.cronExpression)}</span>
            <span className="font-mono text-ink-4">{schedule.cronExpression}</span>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Switch
            aria-label={`${schedule.name} 启停`}
            checked={enabled}
            onCheckedChange={(checked) => void electron.updateScheduleEnabled(schedule.scheduleId, checked)}
          />
          <Button
            className="h-7 rounded-md text-[11px]"
            onClick={() => void electron.triggerSchedule(schedule.scheduleId)}
            variant="ledger"
          >
            <Zap className="h-3.5 w-3.5" strokeWidth={1.5} />
            立即触发
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <IconButton label="调度操作">
                <MoreHorizontal className="h-3.5 w-3.5" strokeWidth={1.5} />
              </IconButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-36">
              <DropdownMenuItem onSelect={() => setEditOpen(true)}>
                <Pencil className="mr-2 h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />
                编辑
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => void electron.updateScheduleEnabled(schedule.scheduleId, !enabled)}
              >
                {enabled
                  ? <PauseCircle className="mr-2 h-3.5 w-3.5 text-amber-500" strokeWidth={1.5} />
                  : <PlayCircle  className="mr-2 h-3.5 w-3.5 text-emerald-500" strokeWidth={1.5} />}
                {enabled ? '停用' : '启用'}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="text-red-600 focus:text-red-600"
                onSelect={() => void electron.deleteSchedule(schedule.scheduleId)}
              >
                <Trash2 className="mr-2 h-3.5 w-3.5" strokeWidth={1.5} />
                删除
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* 元信息：4 格 */}
      <div className="grid grid-cols-4 gap-3">
        <ScheduleFact label="绑定流程" value={schedule.task.flowName} />
        <ScheduleFact label="时区"     value={schedule.timezone} />
        <ScheduleFact
          label="上次运行"
          value={schedule.lastRunAt === null || schedule.lastRunAt === undefined
            ? '尚未运行'
            : formatScheduleDateTime(schedule.lastRunAt)}
        />
        <ScheduleFact
          label="下次运行"
          value={schedule.nextRunAt === null || schedule.nextRunAt === undefined
            ? '等待计算'
            : formatScheduleDateTime(schedule.nextRunAt)}
        />
      </div>

      <ScheduleCreateDialog
        flows={electron.flows}
        onOpenChange={setEditOpen}
        onUpdate={(scheduleId, options) => void electron.updateSchedule(scheduleId, options)}
        open={editOpen}
        schedule={schedule}
      />
    </article>
  );
}

function ScheduleFact({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div className="min-w-0 rounded-md border border-rule bg-paper-sunk px-3 py-2.5">
      <div className="text-[11px] font-medium text-ink-3">{label}</div>
      <div className="mt-1.5 truncate text-[12px] font-medium text-ink-2">{value}</div>
    </div>
  );
}
