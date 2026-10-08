import { AlertTriangle, CalendarCheck2, CalendarClock, ListChecks } from 'lucide-react';
import type { ReactElement } from 'react';

import { countEnabledSchedules, formatScheduleDateTime, hasScheduleError, selectUpcomingSchedules, type ScheduleFilter } from '../../lib/schedulePresentation';
import type { ScheduleRunSummary, ScheduleSnapshot } from '../../types/electron';
import { HealthRail, HealthSignal } from '../workspace/surfaces';

export function SchedulerMetrics({ schedules, runSummaries, loading, filter, onFilter }: {
  schedules: ScheduleSnapshot[];
  runSummaries: Record<string, ScheduleRunSummary>;
  loading: boolean;
  filter?: ScheduleFilter;
  onFilter: (filter: ScheduleFilter) => void;
}): ReactElement {
  const enabledCount = countEnabledSchedules(schedules);
  const attentionCount = schedules.filter((schedule) => hasScheduleError(schedule, runSummaries[schedule.scheduleId])).length;
  const nextSchedule = selectUpcomingSchedules(schedules, 1)[0];

  return (
    <HealthRail>
      <HealthSignal
        loading={loading}
        onClick={() => onFilter('all')}
        selected={filter === 'all'}
        detail="全部触发器"
        icon={<ListChecks className="h-3.5 w-3.5" strokeWidth={1.5} />}
        label="调度总数"
        value={schedules.length}
      />
      <HealthSignal
        loading={loading}
        onClick={() => onFilter('enabled')}
        selected={filter === 'enabled'}
        detail={`停用 ${schedules.length - enabledCount}`}
        icon={<CalendarCheck2 className="h-3.5 w-3.5" strokeWidth={1.5} />}
        label="启用调度"
        state={enabledCount > 0 ? 'success' : 'idle'}
        value={enabledCount}
      />
      <HealthSignal
        loading={loading}
        onClick={() => onFilter('attention')}
        selected={filter === 'attention'}
        detail={attentionCount > 0 ? '排期或运行需要检查' : '没有调度错误'}
        icon={<AlertTriangle className="h-3.5 w-3.5" strokeWidth={1.5} />}
        label="需处理"
        state={attentionCount > 0 ? 'error' : 'success'}
        value={attentionCount}
      />
      <HealthSignal
        loading={loading}
        onClick={() => onFilter('enabled')}
        detail={nextSchedule?.name ?? (enabledCount > 0 ? '启用调度尚无下一次排期' : '没有启用调度')}
        icon={<CalendarClock className="h-3.5 w-3.5" strokeWidth={1.5} />}
        label="下次触发"
        value={nextSchedule === undefined ? (enabledCount > 0 ? '等待排期' : '无启用调度') : formatScheduleDateTime(nextSchedule.nextRunAt, nextSchedule.timezone)}
      />
    </HealthRail>
  );
}
