import type { ReactElement } from 'react';

import { countEnabledSchedules, formatScheduleDateTime } from '../../lib/schedulePresentation';
import type { ScheduleSnapshot } from '../../types/electron';
import { Figure, StatBand } from '../workspace/surfaces';

export function SchedulerMetrics({ schedules }: { schedules: ScheduleSnapshot[] }): ReactElement {
  const enabledCount = countEnabledSchedules(schedules);
  const latestNextRun = schedules
    .filter((s) => s.nextRunAt !== null && s.nextRunAt !== undefined)
    .sort((a, b) => new Date(a.nextRunAt ?? '').getTime() - new Date(b.nextRunAt ?? '').getTime())[0];

  return (
    <StatBand>
      <Figure first label="调度总数" value={schedules.length} note="全部" />
      <Figure label="启用任务" value={enabledCount} note="自动" tone={enabledCount > 0 ? 'live' : 'ink'} />
      <Figure label="停用任务" value={schedules.length - enabledCount} note="暂停" />
      <Figure
        label="最近触发"
        value={latestNextRun === undefined ? '未计算' : formatScheduleDateTime(latestNextRun.nextRunAt)}
        note="下次"
      />
    </StatBand>
  );
}
