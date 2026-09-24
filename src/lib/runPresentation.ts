import type { TaskSnapshot } from '../types/electron';

export function runOutputSummary(run: TaskSnapshot): string {
  if (run.result !== null && run.result !== undefined) return `${run.result.count} 条`;
  const artifacts = run.artifacts?.length ?? 0;
  return artifacts > 0 ? `${artifacts} 个产物` : '—';
}
