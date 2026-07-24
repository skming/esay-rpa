import { Layers } from 'lucide-react';
import type { ReactElement } from 'react';

import type { CanvasToolbarStats } from '../../../types/rpa';

export function CanvasStats({ stats }: { stats: CanvasToolbarStats }): ReactElement {
  return (
    <div className="flex min-w-0 items-center gap-1.5 text-[11px] text-slate-500">
      <Layers className="h-3.5 w-3.5 shrink-0 text-slate-400" strokeWidth={1.5} />
      <span className="whitespace-nowrap">{stats.totalSteps} 步骤</span>
      {/* 未运行时"0 完成 · 0 运行中"是噪声，有数字才占位 */}
      {stats.doneSteps > 0 && (
        <>
          <span className="text-slate-300">·</span>
          <strong className="whitespace-nowrap text-emerald-600">{stats.doneSteps} 完成</strong>
        </>
      )}
      {stats.runningSteps > 0 && (
        <>
          <span className="text-slate-300">·</span>
          <strong className="whitespace-nowrap text-accent-strong">{stats.runningSteps} 运行中</strong>
        </>
      )}
    </div>
  );
}
