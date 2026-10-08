import { CalendarClock, Plus } from 'lucide-react';
import { EmptyPanel, FilterBar, LoadingPanel } from '../workspace/surfaces';
import { WorkspaceShell } from '../workspace/WorkspaceShell';
import type { ReactElement } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { filterSchedules, hasScheduleError, type ScheduleFilter } from '../../lib/schedulePresentation';
import { Button } from '../ui/button';
import { RefreshButton } from '../ui/refresh-button';
import { ScheduleCreateDialog } from '../studio/property-panel/ScheduleCreateDialog';
import { SchedulerMetrics } from './SchedulerMetrics';
import { ScheduleListTable } from './ScheduleListTable';

export function SchedulerPage({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const [createOpen, setCreateOpen] = useState(false);
  const [firstLoad, setFirstLoad] = useState(true);
  const loadedRef = useRef(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const filter = normalizeScheduleFilter(searchParams.get('view'));
  const query = searchParams.get('q') ?? '';

  const schedules = useMemo(
    () => filterSchedules(electron.schedules, filter, query, electron.scheduleRunSummaries),
    [electron.schedules, electron.scheduleRunSummaries, filter, query],
  );

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    void Promise.all([
      electron.loadFlows({ silent: true }),
      electron.loadSchedules({ silent: true }),
      electron.loadScheduleRunSummaries({ silent: true }),
    ]).finally(() => setFirstLoad(false));
  }, [electron]);

  const { loadSchedules, loadScheduleRunSummaries } = electron;
  useEffect(() => {
    let refreshing = false;
    const timer = window.setInterval(() => {
      if (refreshing || document.visibilityState === 'hidden') return;
      refreshing = true;
      void Promise.all([
        loadSchedules({ silent: true }),
        loadScheduleRunSummaries({ silent: true }),
      ]).finally(() => { refreshing = false; });
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [loadSchedules, loadScheduleRunSummaries]);

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
    attention: electron.schedules.filter((schedule) => hasScheduleError(schedule, electron.scheduleRunSummaries[schedule.scheduleId])).length,
    disabled: electron.schedules.filter((schedule) => schedule.status === 'disabled').length,
    enabled: electron.schedules.filter((schedule) => schedule.status === 'enabled').length,
  };

  return (
    <WorkspaceShell
      actions={
        <>
          <RefreshButton variant="subtle" onClick={async () => {
            await Promise.all([electron.loadFlows(), electron.loadSchedules(), electron.loadScheduleRunSummaries()]);
          }}>刷新</RefreshButton>
          <Button onClick={() => setCreateOpen(true)} variant="primary" className="h-8 rounded-md px-3.5">
            <Plus className="h-3.5 w-3.5" strokeWidth={1.75} />
            新建调度
          </Button>
        </>
      }
      description="Cron 触发器与手动调度"
      title="调度中心"
    >
      <SchedulerMetrics
        schedules={electron.schedules}
        runSummaries={electron.scheduleRunSummaries}
        loading={firstLoad}
        filter={query === '' ? filter : undefined}
        onFilter={(nextFilter) => updateSearch({ filter: nextFilter, query: '' })}
      />

      <FilterBar
        counts={counts}
        filters={SCHEDULE_FILTERS}
        onQueryChange={(nextQuery) => updateSearch({ query: nextQuery })}
        onValueChange={(nextFilter) => updateSearch({ filter: nextFilter })}
        query={query}
        searchLabel="搜索调度"
        searchPlaceholder="搜索调度名称或流程…"
        value={filter}
      />

      <div className="flex items-center justify-between gap-3 text-[11px] text-ink-3">
        <p aria-live="polite" role="status">{firstLoad ? '正在读取调度…' : `显示 ${schedules.length} / ${electron.schedules.length} 个调度`}</p>
        {(filter !== 'all' || query !== '') && <Button onClick={() => updateSearch({ filter: 'all', query: '' })} size="sm" variant="ghost">清除筛选</Button>}
      </div>

      {firstLoad && electron.schedules.length === 0 ? (
        <LoadingPanel label="加载调度…" />
      ) : schedules.length === 0 ? (
        <EmptyPanel
          action={<Button onClick={() => {
            if (filter !== 'all' || query.trim() !== '') updateSearch({ filter: 'all', query: '' });
            else setCreateOpen(true);
          }} size="sm" variant={filter !== 'all' || query.trim() !== '' ? 'secondary' : 'primary'}>{filter !== 'all' || query.trim() !== '' ? '清除筛选' : '新建调度'}</Button>}
          icon={<CalendarClock className="h-6 w-6" strokeWidth={1.25} />}
          title={filter !== 'all' || query.trim() !== '' ? '没有匹配的调度' : '暂无调度'}
          hint={filter !== 'all' || query.trim() !== '' ? '尝试其他状态或关键词，或清除筛选查看全部调度。' : '为流程设置触发时间，之后可在这里启停、手动触发与查看结果。'}
        />
      ) : (
        <ScheduleListTable electron={electron} runSummaries={electron.scheduleRunSummaries} schedules={schedules} />
      )}

      <ScheduleCreateDialog
        flows={electron.flows}
        onCreate={electron.createDefaultSchedule}
        onOpenChange={setCreateOpen}
        onPreview={electron.previewSchedule}
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
