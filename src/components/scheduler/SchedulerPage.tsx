import { CalendarClock, ListFilter, Plus } from 'lucide-react';
import { EmptyPanel, SearchField } from '../workspace/surfaces';
import { WorkspaceShell } from '../workspace/WorkspaceShell';
import type { ReactElement } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { filterSchedules, type ScheduleFilter } from '../../lib/schedulePresentation';
import { Button } from '../ui/button';
import { RefreshButton } from '../ui/refresh-button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { ScheduleCreateDialog } from '../studio/property-panel/ScheduleCreateDialog';
import { SchedulerMetrics } from './SchedulerMetrics';
import { ScheduleTaskCard } from './ScheduleTaskCard';

export function SchedulerPage({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const [createOpen, setCreateOpen] = useState(false);
  const [filter, setFilter] = useState<ScheduleFilter>('all');
  const [query, setQuery] = useState('');
  const loadedRef = useRef(false);

  const schedules = useMemo(
    () => filterSchedules(electron.schedules, filter, query),
    [electron.schedules, filter, query],
  );

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    void electron.loadSchedules({ silent: true });
  }, [electron]);

  return (
    <WorkspaceShell
      actions={
        <>
          <RefreshButton variant="subtle" onClick={() => electron.loadSchedules()}>刷新</RefreshButton>
          <Button onClick={() => setCreateOpen(true)} variant="primary" className="h-8 rounded-md px-3.5">
            <Plus className="h-3.5 w-3.5" strokeWidth={1.75} />
            新建调度
          </Button>
        </>
      }
      description="Cron 触发器与手动调度"
      title="调度中心"
    >
      <SchedulerMetrics schedules={electron.schedules} />

      <div className="flex items-center gap-3">
        <SearchField
          label="搜索调度"
          onChange={setQuery}
          placeholder="搜索调度名称或流程…"
          value={query}
        />

        <div className="flex w-24 items-center gap-2">
          <Select onValueChange={(v) => setFilter(v as ScheduleFilter)} value={filter}>
            <SelectTrigger className="h-9 rounded-md border-rule-2 text-[12px]">
              <ListFilter className="size-3 text-ink-4" strokeWidth={2} />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              <SelectItem value="enabled">启用</SelectItem>
              <SelectItem value="disabled">停用</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {schedules.length === 0 ? (
        <EmptyPanel
          icon={<CalendarClock className="h-6 w-6" strokeWidth={1.25} />}
          title="暂无匹配调度"
          hint="新建调度后可在这里启停、手动触发与删除。"
        />
      ) : (
        <div className="grid gap-3">
          {schedules.map((schedule) => (
            <ScheduleTaskCard
              electron={electron}
              key={schedule.scheduleId}
              schedule={schedule}
            />
          ))}
        </div>
      )}

      <ScheduleCreateDialog
        flows={electron.flows}
        onCreate={(options) => { void electron.createDefaultSchedule(options); }}
        onOpenChange={setCreateOpen}
        open={createOpen}
      />
    </WorkspaceShell>
  );
}
