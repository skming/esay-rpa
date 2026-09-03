import type { ScheduleSnapshot, TaskSnapshot } from '../types/electron';

export type OperationalAttentionItem = {
  detail: string;
  id: string;
  kind: 'human' | 'run-error' | 'schedule-error';
  run?: TaskSnapshot;
  schedule?: ScheduleSnapshot;
  title: string;
  updatedAt: string;
};

export type OperationalHealthSnapshot = {
  attention: OperationalAttentionItem[];
  recentResult: {
    failed: number;
    sampleSize: number;
    succeeded: number;
  };
};

export function buildOperationalHealthSnapshot(
  runs: TaskSnapshot[],
  schedules: ScheduleSnapshot[],
): OperationalHealthSnapshot {
  const latestRuns = selectLatestRunPerFlow(runs);
  const humanItems = latestRuns
    .filter((run) => run.status === 'paused_for_human')
    .map((run): OperationalAttentionItem => ({
      detail: run.inputPrompt?.trim() || '流程正在等待人工操作后继续',
      id: `human:${run.taskId}`,
      kind: 'human',
      run,
      title: run.flowName,
      updatedAt: run.updatedAt,
    }));
  const failedRunItems = latestRuns
    .filter((run) => run.status === 'error')
    .map((run): OperationalAttentionItem => ({
      detail: run.error?.trim() || '运行失败，打开详情查看执行证据',
      id: `run-error:${run.taskId}`,
      kind: 'run-error',
      run,
      title: run.flowName,
      updatedAt: run.updatedAt,
    }));
  const scheduleItems = schedules
    .filter((schedule) => schedule.status === 'enabled' && schedule.lastError?.trim())
    .map((schedule): OperationalAttentionItem => ({
      detail: schedule.lastError?.trim() || '调度异常',
      id: `schedule-error:${schedule.scheduleId}`,
      kind: 'schedule-error',
      schedule,
      title: schedule.name,
      updatedAt: schedule.updatedAt,
    }));

  const terminalRuns = runs.filter((run) => run.status === 'success' || run.status === 'error');
  return {
    attention: [
      ...sortNewest(humanItems),
      ...sortNewest(failedRunItems),
      ...sortNewest(scheduleItems),
    ],
    recentResult: {
      failed: terminalRuns.filter((run) => run.status === 'error').length,
      sampleSize: terminalRuns.length,
      succeeded: terminalRuns.filter((run) => run.status === 'success').length,
    },
  };
}

function sortNewest(items: OperationalAttentionItem[]): OperationalAttentionItem[] {
  return items.sort((left, right) => toTime(right.updatedAt) - toTime(left.updatedAt));
}

function selectLatestRunPerFlow(runs: TaskSnapshot[]): TaskSnapshot[] {
  const latestByFlow = new Map<string, TaskSnapshot>();
  for (const run of runs) {
    const key = run.flowId?.trim() || run.flowName;
    const current = latestByFlow.get(key);
    if (current === undefined || toTime(run.updatedAt) > toTime(current.updatedAt)) {
      latestByFlow.set(key, run);
    }
  }
  return [...latestByFlow.values()];
}

function toTime(value: string): number {
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? 0 : time;
}
