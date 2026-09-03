import { AlertTriangle, CalendarCheck2, CalendarClock, ListChecks } from 'lucide-react';
import type { ReactElement } from 'react';

import { countEnabledSchedules, formatScheduleDateTime, hasScheduleError, selectUpcomingSchedules } from '../../lib/schedulePresentation';
import type { ScheduleSnapshot } from '../../types/electron';
import { HealthRail, HealthSignal } from '../workspace/surfaces';

export function SchedulerMetrics({ schedules }: { schedules: ScheduleSnapshot[] }): ReactElement {
  const enabledCount = countEnabledSchedules(schedules);
  const attentionCount = schedules.filter(hasScheduleError).length;
  const nextSchedule = selectUpcomingSchedules(schedules, 1)[0];

  return (
    <HealthRail>
      <HealthSignal
        detail="全部触发器"
        icon={<ListChecks className="h-3.5 w-3.5" strokeWidth={1.5} />}
        label="调度总数"
        value={schedules.length}
      />
      <HealthSignal
        detail={`停用 ${schedules.length - enabledCount}`}
        icon={<CalendarCheck2 className="h-3.5 w-3.5" strokeWidth={1.5} />}
        label="启用任务"
        state={enabledCount > 0 ? 'live' : 'idle'}
        value={enabledCount}
      />
      <HealthSignal
        detail={attentionCount > 0 ? '排期或触发需要检查' : '没有调度错误'}
        icon={<AlertTriangle className="h-3.5 w-3.5" strokeWidth={1.5} />}
        label="需处理"
        state={attentionCount > 0 ? 'error' : 'success'}
        value={attentionCount}
      />
      <HealthSignal
        detail={nextSchedule?.name ?? '没有启用调度'}
        icon={<CalendarClock className="h-3.5 w-3.5" strokeWidth={1.5} />}
        label="下次触发"
        value={nextSchedule === undefined ? '无启用调度' : formatScheduleDateTime(nextSchedule.nextRunAt)}
      />
    </HealthRail>
  );
}
