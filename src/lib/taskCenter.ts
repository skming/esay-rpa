import type { FlowSnapshot, ScheduleSnapshot } from '../types/electron';

export type FlowCardState = 'disabled' | 'draft' | 'failed' | 'paused' | 'published' | 'running' | 'scheduled';
export type FlowFilter = 'all' | 'disabled' | 'failed' | 'paused' | 'running' | 'scheduled';

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

/** Excludes archived flows; `runningFlowId` overrides state to 'running'. */
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

export function filterFlowItems(items: FlowListItem[], query: string, filter: FlowFilter): FlowListItem[] {
  const normalizedQuery = query.trim().toLowerCase();
  return items.filter((item) => {
    if (filter !== 'all' && item.state !== filter) {
      return false;
    }
    if (normalizedQuery === '') {
      return true;
    }
    return `${item.flow.name} ${item.flow.version} ${item.folderPath}`.toLowerCase().includes(normalizedQuery);
  });
}

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
  if (lastRunStatus === 'error') {
    return 'failed';
  }
  if (nextRunAt !== null && nextRunAt !== '') {
    return 'scheduled';
  }
  return 'published';
}

// lastRunStatus 目前未参与计算，仅读取后端预计算的 30 天成功率；保留参数是为未来按最近一次结果做展示微调预留接口。
function estimateSuccessRate(flow: FlowSnapshot, _lastRunStatus: string | null): number | null {
  return flow.successRate30d ?? null;
}

function compareDate(left: string | null | undefined, right: string | null | undefined): number {
  const leftTime = left === null || left === undefined || left === '' ? Number.POSITIVE_INFINITY : new Date(left).getTime();
  const rightTime = right === null || right === undefined || right === '' ? Number.POSITIVE_INFINITY : new Date(right).getTime();
  return leftTime - rightTime;
}
