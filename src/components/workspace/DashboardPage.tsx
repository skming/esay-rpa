import { CalendarClock } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { formatScheduleDateTime, selectUpcomingSchedules } from '../../lib/schedulePresentation';
import type { TaskSnapshot } from '../../types/electron';
import { RefreshButton } from '../ui/refresh-button';
import { Figure, KeyRow, Panel, StatBand, StateTag, SurfaceEmpty } from './surfaces';
import type { StatusTone } from './surfaces';
import { RunDetailDialog } from './RunDetailDialog';
import { RunHistoryList } from './RunHistoryList';
import { WorkspaceShell } from './WorkspaceShell';

const UPCOMING_LIMIT = 5;

export function DashboardPage({ electron }: { electron: ElectronBridgeState }): ReactElement {
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

  const upcoming = useMemo(
    () => selectUpcomingSchedules(electron.schedules, UPCOMING_LIMIT),
    [electron.schedules],
  );

  const status = STATUS[electron.runtimeStatus];

  return (
    <WorkspaceShell
      actions={
        <RefreshButton
          variant="ghost"
          onClick={async () => {
            await electron.loadQueueStats();
            await electron.loadSchedules();
            await electron.loadRuns({ limit: 20 });
          }}
        />
      }
      description="运行状态 · 调度概况 · 历史"
      title="概览"
    >
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
          note={queuedCount > 0 ? `排队 ${queuedCount} · 上限 ${concurrency}` : `上限 ${concurrency}`}
          tone={activeCount > 0 ? 'live' : 'ink'}
        />
        <Figure
          label="运行状态"
          value={status.text}
          state={<StateTag state={status.state} label={status.tag} />}
          note={electron.runtimeStatus === 'running'
            ? `${electron.progress.currentStep}/${electron.progress.totalSteps}`
            : undefined}
        />
      </StatBand>

      <Panel
        bodyClassName={upcoming.length === 0 ? 'p-0' : undefined}
        icon={<CalendarClock className="h-3.5 w-3.5" strokeWidth={1.5} />}
        label="即将触发"
      >
        {upcoming.length === 0 ? (
          <SurfaceEmpty
            title="没有待触发的调度"
            hint="在调度中心新建 Cron 触发器后，最近的几次触发会出现在这里。"
          />
        ) : (
          upcoming.map((schedule, index) => (
            <KeyRow
              key={schedule.scheduleId}
              label={schedule.name}
              last={index === upcoming.length - 1}
              mono
              value={formatScheduleDateTime(schedule.nextRunAt)}
            />
          ))
        )}
      </Panel>

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
  { text: string; tag: string; state: StatusTone }
> = {
  ready: { text: '待运行', tag: '等待触发', state: 'idle' },
  running: { text: '运行中', tag: '执行中', state: 'live' },
  success: { text: '已完成', tag: '成功', state: 'success' },
  error: { text: '失败', tag: '错误', state: 'error' },
  stopped: { text: '已停止', tag: '手动停止', state: 'warning' },
  paused_for_human: { text: '等待接管', tag: '人工接管', state: 'warning' },
};
