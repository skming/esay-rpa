import type { ReactElement } from 'react';

import { formatElapsedTime } from '../../../lib/time';
import type { RuntimeProgress } from '../../../types/rpa';

export function ProgressMeter({ progress }: { progress: RuntimeProgress }): ReactElement {
  const percent = Math.min(100, Math.max(0, progress.percent));

  return (
    <div className="flex shrink-0 items-center gap-1.5 text-[11px] text-slate-500">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
        <div className="h-full rounded-full bg-brand-gradient transition-[width] duration-700" style={{ width: `${percent}%` }} />
      </div>
      <span className="w-7 text-right font-mono font-semibold text-accent-strong">{percent}%</span>
      <span className="font-mono text-slate-400">{formatElapsedTime(progress.elapsedMs)}</span>
    </div>
  );
}
