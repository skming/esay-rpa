import type { BackendTaskLogEntry, RunDetail, TaskSnapshot } from '../types/electron';
import type { NodeRuntimeState, RunLogEntry, RunLogLevel, RuntimeStatus } from '../types/rpa';

const RUN_LOG_LEVELS = new Set<RunLogLevel>(['info', 'success', 'running', 'warn', 'error', 'input']);
const TERMINAL_RUN_STATUSES = new Set<TaskSnapshot['status']>(['success', 'error', 'stopped']);

export function selectLatestTerminalRun(runs: TaskSnapshot[]): TaskSnapshot | null {
  return runs.find((run) => TERMINAL_RUN_STATUSES.has(run.status)) ?? null;
}

export function historicalRuntimeStatus(run: TaskSnapshot): RuntimeStatus | null {
  return TERMINAL_RUN_STATUSES.has(run.status) ? run.status as RuntimeStatus : null;
}

export function toRuntimeLogs(logs: BackendTaskLogEntry[]): RunLogEntry[] {
  return logs.map((log) => ({
    id: log.id,
    level: RUN_LOG_LEVELS.has(log.level as RunLogLevel) ? log.level as RunLogLevel : 'info',
    message: log.message,
    ...(log.detail == null ? {} : { detail: log.detail }),
    ...(log.nodeId == null ? {} : { nodeId: log.nodeId }),
    time: log.time,
  }));
}

export function historicalNodeStates(run: TaskSnapshot): Record<string, NodeRuntimeState> {
  return Object.fromEntries(
    (run.executionEvidence ?? []).map((evidence) => [
      evidence.nodeId,
      { status: evidence.status === 'error' ? 'error' : 'done' },
    ])
  );
}

export function failedNodeId(detail: RunDetail): string | null {
  const errorLog = findLastMatching(detail.logs, (log) => log.level === 'error' && log.nodeId != null);
  if (errorLog?.nodeId) return errorLog.nodeId;
  return findLastMatching(detail.run.executionEvidence ?? [], (evidence) => evidence.status === 'error')?.nodeId ?? null;
}

export function recentRunSummary(detail: RunDetail): string {
  if (detail.run.error?.trim()) return detail.run.error.trim();
  const errorLog = findLastMatching(detail.logs, (log) => log.level === 'error');
  return errorLog?.message.trim() || '运行失败，查看错误日志了解详情。';
}

function findLastMatching<T>(items: readonly T[], predicate: (item: T) => boolean): T | undefined {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item !== undefined && predicate(item)) return item;
  }
  return undefined;
}
