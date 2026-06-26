import type { FlowSnapshot, ScheduleSnapshot } from '../types/electron';

/** Derived display state for a flow card, combining flow status, run history, and schedule presence. */
export type FlowCardState = 'disabled' | 'draft' | 'failed' | 'paused' | 'published' | 'running' | 'scheduled';

/** Enriched list item that combines a flow with its schedule and run-history metadata. */
export type FlowListItem = {
  flow: FlowSnapshot;
  folderPath: string;
  lastRunStatus: string | null;
  /** Earliest next scheduled run among all enabled schedules targeting this flow. */
  nextRunAt: string | null;
  successRate: number | null;
  state: FlowCardState;
};

type FlowCompat = FlowSnapshot & {
  folderPath?: string | null;
  folder_path?: string | null;
  lastRunStatus?: string | null;
  last_run_status?: string | null;
  thumbnailUrl?: string | null;
  thumbnail_url?: string | null;
};

/**
 * Merges flows with schedule/run data into display items, sorted by last updated.
 * Archived flows are excluded. `runningFlowId` overrides state to 'running'.
 */
export function buildFlowListItems(flows: FlowSnapshot[], schedules: ScheduleSnapshot[], runningFlowId: string | null): FlowListItem[] {
  const nextRunByFlowId = new Map<string, string>();
  for (const schedule of schedules) {
    const flowId = schedule.task.flowId;
    if (typeof flowId !== 'string' || flowId.trim() === '' || schedule.status !== 'enabled') {
      continue;
    }
    const current = nextRunByFlowId.get(flowId);
    if (current === undefined || compareDate(schedule.nextRunAt, current) < 0) {
      nextRunByFlowId.set(flowId, schedule.nextRunAt ?? '');
    }
  }

  return flows
    .filter((flow) => flow.status !== 'archived')
    .map((flow) => {
      const compat = flow as FlowCompat;
      const nextRunAt = nextRunByFlowId.get(flow.flowId) ?? null;
      const lastRunStatus = flow.lastRunStatus ?? compat.lastRunStatus ?? compat.last_run_status ?? null;
      const successRate = estimateSuccessRate(flow, lastRunStatus);
      return {
        flow,
        folderPath: compat.folderPath ?? compat.folder_path ?? '默认目录',
        lastRunStatus,
        nextRunAt,
        successRate,
        state: resolveFlowState(flow, nextRunAt, lastRunStatus, runningFlowId)
      };
    })
    .sort((left, right) => compareDate(right.flow.updatedAt, left.flow.updatedAt));
}

export function filterFlowItems(items: FlowListItem[], query: string, folder: string): FlowListItem[] {
  const normalizedQuery = query.trim().toLowerCase();
  return items.filter((item) => {
    const folderMatched = folder === '全部流程' || item.folderPath === folder;
    if (!folderMatched) {
      return false;
    }
    if (normalizedQuery === '') {
      return true;
    }
    return `${item.flow.name} ${item.flow.version} ${item.folderPath}`.toLowerCase().includes(normalizedQuery);
  });
}

export function listFolders(items: FlowListItem[]): string[] {
  return ['全部流程', ...Array.from(new Set(items.map((item) => item.folderPath))).sort((left, right) => left.localeCompare(right, 'zh-Hans-CN'))];
}

/** Formats an ISO timestamp as a human-readable relative time (e.g. "5 分钟前"). */
export function formatRelativeTime(value: string | null | undefined): string {
  if (value === null || value === undefined || value.trim() === '') {
    return '--';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const deltaMs = Date.now() - date.getTime();
  const absMs = Math.abs(deltaMs);
  const suffix = deltaMs >= 0 ? '前' : '后';
  const minutes = Math.round(absMs / 60_000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟${suffix}`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时${suffix}`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} 天${suffix}`;
  return `${date.getMonth() + 1}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

export function formatScheduleHint(value: string | null): string {
  if (value === null || value.trim() === '') {
    return '未调度';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return `下次 ${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function resolveFlowState(flow: FlowSnapshot, nextRunAt: string | null, lastRunStatus: string | null, runningFlowId: string | null): FlowCardState {
  if (runningFlowId === flow.flowId) {
    return 'running';
  }
  if (flow.status === 'draft') {
    return 'draft';
  }
  if (flow.status === 'paused') {
    return 'paused';
  }
  if (flow.status === 'disabled') {
    return 'disabled';
  }
  // flow.status === 'active' from here
  if (lastRunStatus === 'error') {
    return 'failed';
  }
  if (nextRunAt !== null && nextRunAt !== '') {
    return 'scheduled';
  }
  return 'published';
}

function estimateSuccessRate(flow: FlowSnapshot, _lastRunStatus: string | null): number | null {
  return flow.successRate30d ?? null;
}

function compareDate(left: string | null | undefined, right: string | null | undefined): number {
  const leftTime = left === null || left === undefined || left === '' ? Number.POSITIVE_INFINITY : new Date(left).getTime();
  const rightTime = right === null || right === undefined || right === '' ? Number.POSITIVE_INFINITY : new Date(right).getTime();
  return leftTime - rightTime;
}
