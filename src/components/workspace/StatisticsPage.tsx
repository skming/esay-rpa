import { CircleDashed, Clock3, DatabaseZap } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useRef } from 'react';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { RefreshButton } from '../ui/refresh-button';
import { Figure, KeyRow, Panel, StatBand } from './ledger';
import { RunHistoryList } from './RunHistoryList';
import { WorkspaceShell } from './WorkspaceShell';

export function StatisticsPage({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const loadedRef = useRef(false);

  useEffect(() => {
    if (loadedRef.current) {
      return;
    }
    loadedRef.current = true;
    void electron.loadQueueStats({ silent: true });
    void electron.loadSchedules({ silent: true });
    void electron.loadRuns({ limit: 20, silent: true });
  }, [electron]);

  const progressPercent = `${electron.progress.percent}%`;
  const averageQueueLoad = electron.queueStats !== null ? `${electron.queueStats.activeCount}/${electron.queueStats.concurrency}` : '--';

  return (
    <WorkspaceShell
      actions={
        <RefreshButton variant="ledger" onClick={async () => { await electron.loadQueueStats(); await electron.loadRuns({ limit: 20 }); }}>
          刷新指标
        </RefreshButton>
      }
      description="运行进度与输出聚合"
      title="运行统计"
    >
      <StatBand>
        <Figure first label="当前进度" value={<span className="figure">{progressPercent}</span>} note="本次" tone={electron.progress.percent > 0 && electron.progress.percent < 100 ? 'live' : 'ink'} />
        <Figure label="队列负载" value={averageQueueLoad} note="活跃/上限" />
        <Figure label="变量数" value={electron.variables.length} note="快照" />
        <Figure label="产物数" value={electron.artifacts.length} note="输出" />
        <Figure label="历史运行" value={electron.runs.length} note="累计" />
      </StatBand>

      <div className="grid gap-5 xl:grid-cols-[1.2fr_0.8fr]">
        <Panel label="执行摘要" icon={<CircleDashed className="h-3.5 w-3.5" strokeWidth={1.5} />}>
          <KeyRow label="当前步骤" value={`${electron.progress.currentStep} / ${electron.progress.totalSteps}`} />
          <KeyRow label="累计耗时" value={formatElapsed(electron.progress.elapsedMs)} mono />
          <KeyRow label="启用调度" value={`${electron.schedules.filter((item) => item.status === 'enabled').length} 个`} last />
        </Panel>

        <Panel label="队列快照" icon={<Clock3 className="h-3.5 w-3.5" strokeWidth={1.5} />}>
          <KeyRow label="并发上限" value={electron.queueStats?.concurrency ?? 0} mono />
          <KeyRow label="运行中" value={electron.queueStats?.activeCount ?? 0} mono />
          <KeyRow label="排队中" value={electron.queueStats?.queuedCount ?? 0} mono last />
        </Panel>
      </div>

      <Panel label="运行日志分布" icon={<DatabaseZap className="h-3.5 w-3.5" strokeWidth={1.5} />} bodyClassName="p-0">
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5">
          <LogCell label="信息" value={countLogs(electron, 'info')} first />
          <LogCell label="运行" value={countLogs(electron, 'running')} tone="live" />
          <LogCell label="成功" value={countLogs(electron, 'success')} />
          <LogCell label="警告" value={countLogs(electron, 'warn')} />
          <LogCell label="错误" value={countLogs(electron, 'error')} />
        </div>
      </Panel>

      <RunHistoryList
        onInspectRun={(run) => {
          void electron.loadTaskVariables(run.taskId);
          void electron.loadArtifacts(run.taskId);
        }}
        onRefresh={() => void electron.loadRuns({ limit: 20 })}
        runs={electron.runs}
      />
    </WorkspaceShell>
  );
}

function LogCell({ label, value, tone = 'ink', first }: { label: string; value: number; tone?: 'ink' | 'live'; first?: boolean }): ReactElement {
  return (
    <div className={first ? 'px-5 py-4' : 'rule-v px-5 py-4'}>
      <div className="text-[11px] font-medium text-ink-3">{label}</div>
      <div className={`figure mt-2.5 text-[26px] font-medium leading-none ${tone === 'live' && value > 0 ? 'text-live' : 'text-ink'}`}>
        {value}
      </div>
    </div>
  );
}

function countLogs(electron: ElectronBridgeState, level: ElectronBridgeState['logs'][number]['level']): number {
  return electron.logs.filter((log) => log.level === level).length;
}

function formatElapsed(elapsedMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}
