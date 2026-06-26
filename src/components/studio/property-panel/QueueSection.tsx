import type { ReactElement } from 'react';

import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import { RefreshButton } from '../../ui/refresh-button';
import { PanelSection } from './PanelSection';

export function QueueSection({ electron }: { electron: ElectronBridgeState }): ReactElement {
  return (
    <PanelSection title="任务队列">
      <RefreshButton className="h-7 w-full" onClick={() => electron.loadQueueStats()}>
        刷新队列状态
      </RefreshButton>
      {electron.queueStats !== null && (
        <div className="grid grid-cols-3 gap-1.5 text-center text-[10px] leading-none">
          <QueueMetric label="并发" value={String(electron.queueStats.concurrency)} />
          <QueueMetric label="运行中" value={String(electron.queueStats.activeCount)} />
          <QueueMetric label="排队" value={String(electron.queueStats.queuedCount)} />
        </div>
      )}
    </PanelSection>
  );
}

function QueueMetric({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-1.5 py-1.5">
      <div className="font-mono text-[11px] font-semibold text-slate-700">{value}</div>
      <div className="mt-0.5 text-slate-400">{label}</div>
    </div>
  );
}
