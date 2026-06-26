import { CircleDashed, Layers, ListTodo, Play } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useRef, useState } from 'react';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import type { TaskSnapshot } from '../../types/electron';
import { Button } from '../ui/button';
import { RefreshButton } from '../ui/refresh-button';
import { Figure, KeyRow, Panel, StatBand, StateTag } from './ledger';
import type { LedgerState } from './ledger';
import { RunDetailDialog } from './RunDetailDialog';
import { RunHistoryList } from './RunHistoryList';
import { WorkspaceShell } from './WorkspaceShell';

export function DashboardPage({
  electron,
  onOpenTaskCenter,
}: {
  electron: ElectronBridgeState;
  onOpenTaskCenter: () => void;
}): ReactElement {
  const loadedRef = useRef(false);
  const [detailRun, setDetailRun] = useState<TaskSnapshot | null>(null);

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    void electron.loadFlows({ silent: true });
    void electron.loadSchedules({ silent: true });
    void electron.loadQueueStats({ silent: true });
    void electron.loadRuns({ limit: 20, silent: true });
  }, [electron]);

  const activeFlows = electron.flows.filter((f) => f.status !== 'archived').length;
  const enabledSched = electron.schedules.filter((s) => s.status === 'enabled').length;
  const activeCount = electron.queueStats?.activeCount ?? 0;
  const queuedCount = electron.queueStats?.queuedCount ?? 0;
  const concurrency = electron.queueStats?.concurrency ?? 1;

  const status = STATUS[electron.runtimeStatus];

  return (
    <WorkspaceShell
      actions={
        <RefreshButton
          variant="ghost"
          onClick={async () => {
            await electron.loadQueueStats();
            await electron.loadRuns({ limit: 20 });
          }}
        />
      }
      description="运行状态 · 调度概况 · 历史"
      title="概览"
    >
      {/* Ledger figure band — typography carries the read, hairlines divide it */}
      <StatBand>
        <Figure
          first
          label="活跃流程"
          value={activeFlows}
          note="已配置"
        />
        <Figure
          label="启用调度"
          value={enabledSched}
          note="自动触发"
        />
        <Figure
          label="运行中"
          value={activeCount}
          note={queuedCount > 0 ? `排队 ${queuedCount}` : '空闲'}
          tone={activeCount > 0 ? 'live' : 'ink'}
        />
        <Figure
          label="运行状态"
          value={<span className="figure text-[27px]">{status.text}</span>}
          state={<StateTag state={status.state} label={status.tag} />}
          note={electron.runtimeStatus === 'running'
            ? `${electron.progress.currentStep}/${electron.progress.totalSteps}`
            : undefined}
        />
      </StatBand>

      {/* Two ledgers: execution summary (golden ratio) + task queue */}
      <div className="grid gap-5 lg:grid-cols-[1.618fr_1fr]">
        <Panel label="执行摘要" icon={<CircleDashed className="h-3.5 w-3.5" strokeWidth={1.5} />}>
          <KeyRow label="当前步骤" value={`${electron.progress.currentStep} / ${electron.progress.totalSteps}`} />
          <KeyRow label="累计耗时" value={fmtElapsed(electron.progress.elapsedMs)} mono />
          <KeyRow label="运行产物" value={`${electron.artifacts.length} 个`} />
          <KeyRow label="变量快照" value={`${electron.variables.length} 个`} last />
        </Panel>

        <Panel label="任务队列" icon={<Play className="h-3.5 w-3.5" strokeWidth={1.5} />}>
          <KeyRow label="并发上限" value={<>{concurrency}<span className="ml-1 font-sans text-[11px] font-normal text-ink-4">并行</span></>} mono />
          <KeyRow label="运行中" value={<span className={activeCount > 0 ? 'text-live' : undefined}>{activeCount}</span>} mono />
          <KeyRow label="排队中" value={<span className={queuedCount > 0 ? 'text-amber-700' : undefined}>{queuedCount}</span>} mono last />
        </Panel>
      </div>

      {/* Primary action — brand gradient */}
      <div className="flex items-center gap-2.5">
        <Button onClick={onOpenTaskCenter} variant="primary" className="h-8 rounded-md px-3.5">
          <ListTodo className="h-3.5 w-3.5" strokeWidth={1.75} />
          任务管理
        </Button>
        <span className="inline-flex items-center gap-1.5 text-[11.5px] text-ink-4">
          <Layers className="h-3.5 w-3.5" strokeWidth={1.5} />
          {activeFlows} 个流程 · {enabledSched} 个调度
        </span>
      </div>

      {/* Run history — editorial ledger table */}
      <RunHistoryList
        onInspectRun={(run) => {
          void electron.loadTaskVariables(run.taskId);
          void electron.loadArtifacts(run.taskId);
          setDetailRun(run);
        }}
        onRefresh={() => void electron.loadRuns({ limit: 20 })}
        runs={electron.runs}
      />

      <RunDetailDialog
        onOpenArtifact={(artifact) => void electron.openArtifactPath(artifact.storageUrl)}
        onOpenChange={(open) => { if (!open) setDetailRun(null); }}
        open={detailRun !== null}
        run={detailRun}
      />
    </WorkspaceShell>
  );
}

const STATUS: Record<
  ElectronBridgeState['runtimeStatus'],
  { text: string; tag: string; state: LedgerState }
> = {
  ready: { text: '待运行', tag: '等待触发', state: 'idle' },
  running: { text: '运行中', tag: '执行中', state: 'live' },
  success: { text: '已完成', tag: '成功', state: 'success' },
  error: { text: '失败', tag: '错误', state: 'error' },
  stopped: { text: '已停止', tag: '手动停止', state: 'warning' },
};

function fmtElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}
