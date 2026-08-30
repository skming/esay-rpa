import type { ReactElement } from 'react';

import { countEnabledSchedules, formatScheduleDateTime, selectUpcomingSchedules } from '../../lib/schedulePresentation';
import type { ScheduleSnapshot } from '../../types/electron';
import { Figure, StatBand } from '../workspace/surfaces';

export function SchedulerMetrics({ schedules }: { schedules: ScheduleSnapshot[] }): ReactElement {
  const enabledCount = countEnabledSchedules(schedules);
  const nextSchedule = selectUpcomingSchedules(schedules, 1)[0];

  return (
    <StatBand>
      <Figure first label="调度总数" value={schedules.length} note="全部" />
      <Figure label="启用任务" value={enabledCount} note="自动" tone={enabledCount > 0 ? 'live' : 'ink'} />
      <Figure label="停用任务" value={schedules.length - enabledCount} note="暂停" />
      <Figure
        label="下次触发"
        value={nextSchedule === undefined ? '无启用调度' : formatScheduleDateTime(nextSchedule.nextRunAt)}
        note={nextSchedule?.name}
      />
    </StatBand>
  );
}
