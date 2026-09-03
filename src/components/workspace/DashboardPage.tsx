import { Activity, AlertTriangle, ArrowUpRight, CalendarClock, CheckCircle2, Clock3, Hand } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ROUTE_PATHS } from '../../app/routeConfig';
import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { buildOperationalHealthSnapshot, type OperationalAttentionItem } from '../../lib/operationalHealth';
import { formatScheduleDateTime, selectUpcomingSchedules } from '../../lib/schedulePresentation';
import { formatRelativeTime } from '../../lib/taskCenter';
import type { TaskSnapshot } from '../../types/electron';
import { cn } from '../../lib/utils';
import { RefreshIconButton } from '../ui/refresh-button';
import { HealthRail, HealthSignal, Panel, SurfaceEmpty } from './surfaces';
import { RunDetailDialog } from './RunDetailDialog';
import { RunHistoryList } from './RunHistoryList';
import { WorkspaceShell } from './WorkspaceShell';

const UPCOMING_LIMIT = 5;

export function DashboardPage({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const loadedRef = useRef(false);
  const [detailRun, setDetailRun] = useState<TaskSnapshot | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    void electron.loadSchedules({ silent: true });
    void electron.loadQueueStats({ silent: true });
    void electron.loadRuns({ limit: 20, silent: true });
  }, [electron]);

  const activeCount = electron.queueStats?.activeCount ?? 0;
  const queuedCount = electron.queueStats?.queuedCount ?? 0;
  const concurrency = electron.queueStats?.concurrency ?? 1;

  const upcoming = useMemo(
    () => selectUpcomingSchedules(electron.schedules, UPCOMING_LIMIT),
    [electron.schedules],
  );
  const health = useMemo(
    () => buildOperationalHealthSnapshot(electron.runs, electron.schedules),
    [electron.runs, electron.schedules],
  );
  const nextSchedule = upcoming[0];

  const inspectRun = (run: TaskSnapshot): void => {
    void electron.loadTaskVariables(run.taskId);
    void electron.loadArtifacts(run.taskId);
    setDetailRun(run);
  };

  const openSchedule = (scheduleName?: string, attention = false): void => {
    const params = new URLSearchParams();
    if (attention) params.set('view', 'attention');
    if (scheduleName !== undefined) params.set('q', scheduleName);
    navigate(`${ROUTE_PATHS.scheduler}?${params.toString()}`);
  };

  return (
    <WorkspaceShell
      actions={
        <RefreshIconButton
          label="刷新概览"
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
      <HealthRail>
        <HealthSignal
          detail={queuedCount > 0 ? `排队 ${queuedCount} · 并发上限 ${concurrency}` : `并发上限 ${concurrency}`}
          icon={<Activity className="h-3.5 w-3.5" strokeWidth={1.5} />}
          label="当前执行"
          state={activeCount > 0 ? 'live' : 'idle'}
          value={activeCount > 0 ? `${activeCount} 个运行中` : '空闲'}
        />
        <HealthSignal
          detail={health.attention.length > 0 ? '等待处理或查看证据' : '没有未恢复异常'}
          icon={<AlertTriangle className="h-3.5 w-3.5" strokeWidth={1.5} />}
          label="需关注"
          state={health.attention.length > 0 ? 'error' : 'success'}
          value={health.attention.length}
        />
        <HealthSignal
          detail={`失败 ${health.recentResult.failed} · 样本 ${health.recentResult.sampleSize}`}
          icon={<CheckCircle2 className="h-3.5 w-3.5" strokeWidth={1.5} />}
          label="最近运行成功"
          state={health.recentResult.failed > 0 ? 'warning' : 'success'}
          value={health.recentResult.succeeded}
        />
        <HealthSignal
          detail={nextSchedule?.name ?? '没有启用调度'}
          icon={<CalendarClock className="h-3.5 w-3.5" strokeWidth={1.5} />}
          label="下一次触发"
          value={nextSchedule === undefined ? '--' : formatScheduleDateTime(nextSchedule.nextRunAt)}
        />
      </HealthRail>

      <div className="grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
        <AttentionPanel
          items={health.attention.slice(0, 6)}
          onInspectRun={inspectRun}
          onOpenSchedule={(name) => openSchedule(name, true)}
        />

        <Panel
          action={upcoming.length > 0 ? (
            <button className="inline-flex items-center gap-1 text-[11px] font-medium text-accent-strong hover:text-accent-press" onClick={() => openSchedule()} type="button">
              查看全部
              <ArrowUpRight className="h-3 w-3" strokeWidth={1.5} />
            </button>
          ) : undefined}
          bodyClassName="p-0"
          icon={<CalendarClock className="h-3.5 w-3.5" strokeWidth={1.5} />}
          label="即将触发"
        >
          {upcoming.length === 0 ? (
            <SurfaceEmpty
              title="没有待触发的调度"
              hint="在调度中心新建并启用触发器后，这里会显示最近计划。"
            />
          ) : (
            <div>
              {upcoming.map((schedule) => (
                <button
                  className="flex w-full items-center justify-between gap-4 border-b border-rule px-5 py-3 text-left transition-colors last:border-b-0 hover:bg-paper"
                  key={schedule.scheduleId}
                  onClick={() => openSchedule(schedule.name)}
                  type="button"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[12px] font-medium text-ink-2">{schedule.name}</span>
                    <span className="mt-0.5 block truncate text-[10px] text-ink-3">{schedule.task.flowName}</span>
                  </span>
                  <span className="shrink-0 font-mono text-[10px] tabular-nums text-ink-3">
                    {formatScheduleDateTime(schedule.nextRunAt)}
                  </span>
                </button>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <RunHistoryList
        onInspectRun={(run) => {
          inspectRun(run);
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

function AttentionPanel({
  items,
  onInspectRun,
  onOpenSchedule,
}: {
  items: OperationalAttentionItem[];
  onInspectRun: (run: TaskSnapshot) => void;
  onOpenSchedule: (scheduleName: string) => void;
}): ReactElement {
  return (
    <Panel
      bodyClassName="p-0"
      icon={<AlertTriangle className="h-3.5 w-3.5" strokeWidth={1.5} />}
      label="需要关注"
    >
      {items.length === 0 ? (
        <SurfaceEmpty
          icon={<CheckCircle2 className="h-5 w-5 text-emerald-500" strokeWidth={1.5} />}
          title="当前没有未恢复异常"
          hint="最新运行和启用调度均未报告需要处理的问题。"
        />
      ) : (
        <div>
          {items.map((item) => {
            const config = ATTENTION_META[item.kind];
            const Icon = config.icon;
            return (
              <button
                className="group flex w-full items-center gap-3 border-b border-rule px-5 py-3 text-left transition-colors last:border-b-0 hover:bg-paper"
                key={item.id}
                onClick={() => {
                  if (item.run !== undefined) onInspectRun(item.run);
                  if (item.schedule !== undefined) onOpenSchedule(item.schedule.name);
                }}
                type="button"
              >
                <span className={cn('grid h-7 w-7 shrink-0 place-items-center rounded-md', config.surface, config.text)}>
                  <Icon className="h-3.5 w-3.5" strokeWidth={1.5} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-[12px] font-medium text-ink-2">{item.title}</span>
                    <span className={cn('shrink-0 text-[10px] font-medium', config.text)}>{config.label}</span>
                  </span>
                  <span className="mt-0.5 block truncate text-[10px] text-ink-3">{item.detail}</span>
                </span>
                <span className="flex shrink-0 items-center gap-1.5 font-mono text-[10px] tabular-nums text-ink-3">
                  <Clock3 className="h-3 w-3 text-ink-4" strokeWidth={1.5} />
                  {formatRelativeTime(item.updatedAt)}
                  <ArrowUpRight className="h-3 w-3 text-ink-4 transition-colors group-hover:text-accent-strong" strokeWidth={1.5} />
                </span>
              </button>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

const ATTENTION_META = {
  human: { icon: Hand, label: '等待接管', surface: 'bg-amber-50', text: 'text-amber-700' },
  'run-error': { icon: AlertTriangle, label: '运行失败', surface: 'bg-red-50', text: 'text-red-600' },
  'schedule-error': { icon: CalendarClock, label: '调度错误', surface: 'bg-red-50', text: 'text-red-600' },
} satisfies Record<OperationalAttentionItem['kind'], { icon: typeof AlertTriangle; label: string; surface: string; text: string }>;
