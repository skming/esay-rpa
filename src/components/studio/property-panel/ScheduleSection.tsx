import { CalendarClock, PauseCircle, PlayCircle, Trash2, Zap } from 'lucide-react';
import type { ReactElement } from 'react';
import { useState } from 'react';

import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import { Badge } from '../../ui/badge';
import { Button, IconButton } from '../../ui/button';
import { RefreshButton } from '../../ui/refresh-button';
import { PanelSection } from './PanelSection';
import { ScheduleCreateDialog } from './ScheduleCreateDialog';

export function ScheduleSection({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const [createOpen, setCreateOpen] = useState(false);
  const latestSchedules = [...electron.schedules]
    .sort((left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime())
    .slice(0, 4);

  return (
    <PanelSection title="调度运行">
      <div className="grid grid-cols-2 gap-1.5">
        <Button className="h-8" onClick={() => setCreateOpen(true)} variant="outline">
          <CalendarClock className="h-3.5 w-3.5" strokeWidth={1.5} />
          创建调度
        </Button>
        <RefreshButton className="h-8" onClick={() => electron.loadSchedules()}>刷新列表</RefreshButton>
      </div>
      {latestSchedules.length === 0 ? (
        <div className="rounded-md border border-dashed border-slate-200 bg-slate-50 px-2 py-3 text-center text-[10px] leading-4 text-slate-500">暂无调度，创建后可在此启停、触发或删除。</div>
      ) : (
        <div className="space-y-1.5">
          {latestSchedules.map((schedule) => (
            <ScheduleRow electron={electron} key={schedule.scheduleId} schedule={schedule} />
          ))}
        </div>
      )}
      <ScheduleCreateDialog
        onCreate={(options) => {
          void electron.createDefaultSchedule(options);
        }}
        onOpenChange={setCreateOpen}
        open={createOpen}
      />
    </PanelSection>
  );
}

function ScheduleRow({ electron, schedule }: { electron: ElectronBridgeState; schedule: ElectronBridgeState['schedules'][number] }): ReactElement {
  const enabled = schedule.status === 'enabled';
  const lastRunText = schedule.lastRunAt === null || schedule.lastRunAt === undefined ? '尚未运行' : formatDateTime(schedule.lastRunAt);
  const nextRunText = schedule.nextRunAt === null || schedule.nextRunAt === undefined ? '等待计算' : formatDateTime(schedule.nextRunAt);

  return (
    <div className="rounded-md border border-slate-200 bg-white p-2 shadow-xs">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[11px] font-semibold text-slate-700">{schedule.name}</div>
          <div className="mt-0.5 truncate font-mono text-[10px] text-slate-500">{schedule.cronExpression}</div>
        </div>
        <Badge variant={enabled ? 'emerald' : 'default'}>{enabled ? '启用' : '停用'}</Badge>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-1 text-[10px] text-slate-500">
        <ScheduleMeta label="上次" value={lastRunText} />
        <ScheduleMeta label="下次" value={nextRunText} />
      </div>
      <div className="mt-2 flex items-center justify-end gap-1">
        <IconButton label="立即触发" onClick={() => void electron.triggerSchedule(schedule.scheduleId)}>
          <Zap className="h-3.5 w-3.5" strokeWidth={1.5} />
        </IconButton>
        <IconButton label={enabled ? '停用调度' : '启用调度'} onClick={() => void electron.updateScheduleEnabled(schedule.scheduleId, !enabled)}>
          {enabled ? <PauseCircle className="h-3.5 w-3.5" strokeWidth={1.5} /> : <PlayCircle className="h-3.5 w-3.5" strokeWidth={1.5} />}
        </IconButton>
        <IconButton className="text-red-500 hover:text-red-600" label="删除调度" onClick={() => void electron.deleteSchedule(schedule.scheduleId)}>
          <Trash2 className="h-3.5 w-3.5" strokeWidth={1.5} />
        </IconButton>
      </div>
    </div>
  );
}

function ScheduleMeta({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div className="min-w-0 rounded bg-slate-50 px-1.5 py-1">
      <div className="text-slate-500">{label}</div>
      <div className="truncate font-mono text-slate-600">{value}</div>
    </div>
  );
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}
