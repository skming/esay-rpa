import type { StatusTone } from '../components/workspace/surfaces';
import type { TaskSnapshot } from '../types/electron';

/** 运行状态 → 展示色调与文案的唯一映射；列表、抽屉、调度表都用它，勿再各写一份。 */
export const TASK_STATUS_META: Record<TaskSnapshot['status'], { tone: StatusTone; label: string }> = {
  success: { tone: 'success', label: '成功' },
  error: { tone: 'error', label: '失败' },
  running: { tone: 'live', label: '运行中' },
  queued: { tone: 'warning', label: '排队' },
  stopped: { tone: 'idle', label: '已停止' },
  awaiting_confirmation: { tone: 'warning', label: '等待操作' },
};

export function runOutputSummary(run: TaskSnapshot): string {
  if (run.result !== null && run.result !== undefined) return `${run.result.count} 条`;
  const artifacts = run.artifacts?.length ?? 0;
  return artifacts > 0 ? `${artifacts} 个产物` : '—';
}
