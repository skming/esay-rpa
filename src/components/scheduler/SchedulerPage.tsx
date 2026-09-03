import { CalendarClock, Plus } from 'lucide-react';
import { EmptyPanel, SearchField } from '../workspace/surfaces';
import { WorkspaceShell } from '../workspace/WorkspaceShell';
import type { ReactElement } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { filterSchedules, hasScheduleError, type ScheduleFilter } from '../../lib/schedulePresentation';
import { cn } from '../../lib/utils';
import { Button } from '../ui/button';
import { RefreshButton } from '../ui/refresh-button';
import { ScheduleCreateDialog } from '../studio/property-panel/ScheduleCreateDialog';
import { SchedulerMetrics } from './SchedulerMetrics';
import { ScheduleListTable } from './ScheduleListTable';

export function SchedulerPage({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const [createOpen, setCreateOpen] = useState(false);
  const loadedRef = useRef(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const filter = normalizeScheduleFilter(searchParams.get('view'));
  const query = searchParams.get('q') ?? '';

  const schedules = useMemo(
    () => filterSchedules(electron.schedules, filter, query),
    [electron.schedules, filter, query],
  );

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    void electron.loadFlows({ silent: true });
    void electron.loadSchedules({ silent: true });
  }, [electron]);

  const updateSearch = (next: { filter?: ScheduleFilter; query?: string }): void => {
    const params = new URLSearchParams(searchParams);
    if (next.filter !== undefined) {
      if (next.filter === 'all') params.delete('view');
      else params.set('view', next.filter);
    }
    if (next.query !== undefined) {
      if (next.query.trim() === '') params.delete('q');
      else params.set('q', next.query);
    }
    setSearchParams(params, { replace: true });
  };

  const counts: Record<ScheduleFilter, number> = {
    all: electron.schedules.length,
    attention: electron.schedules.filter(hasScheduleError).length,
    disabled: electron.schedules.filter((schedule) => schedule.status === 'disabled').length,
    enabled: electron.schedules.filter((schedule) => schedule.status === 'enabled').length,
  };

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

      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-rule bg-surface p-2 shadow-xs">
        <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
          {SCHEDULE_FILTERS.map((item) => (
            <button
              aria-pressed={filter === item.value}
              className={cn(
                'flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-[11px] font-medium transition-colors',
                filter === item.value
                  ? 'bg-accent-soft text-accent-strong'
                  : 'text-ink-3 hover:bg-paper-sunk hover:text-ink-2',
              )}
              key={item.value}
              onClick={() => updateSearch({ filter: item.value })}
              type="button"
            >
              {item.label}
              <span className={cn(
                'font-mono text-[10px] tabular-nums',
                filter === item.value ? 'text-accent-strong' : 'text-ink-4',
              )}>
                {counts[item.value]}
              </span>
            </button>
          ))}
        </div>
        <SearchField
          className="w-64 flex-none"
          label="搜索调度"
          onChange={(nextQuery) => updateSearch({ query: nextQuery })}
          placeholder="搜索调度名称或流程…"
          value={query}
        />
      </div>

      {schedules.length === 0 ? (
        <EmptyPanel
          icon={<CalendarClock className="h-6 w-6" strokeWidth={1.25} />}
          title="暂无匹配调度"
          hint="新建调度后可在这里启停、手动触发与删除。"
        />
      ) : (
        <ScheduleListTable electron={electron} schedules={schedules} />
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

const SCHEDULE_FILTERS: Array<{ label: string; value: ScheduleFilter }> = [
  { label: '全部', value: 'all' },
  { label: '启用', value: 'enabled' },
  { label: '需处理', value: 'attention' },
  { label: '停用', value: 'disabled' },
];

function normalizeScheduleFilter(value: string | null): ScheduleFilter {
  const filters: ScheduleFilter[] = ['all', 'enabled', 'attention', 'disabled'];
  return filters.includes(value as ScheduleFilter) ? value as ScheduleFilter : 'all';
}
