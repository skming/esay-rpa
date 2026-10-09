import { Activity, AlertTriangle, ArrowUpRight, CalendarClock, CheckCircle2 } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ROUTE_PATHS } from '../../app/routeConfig';
import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { buildOperationalHealthSnapshot, type OperationalAttentionItem } from '../../lib/operationalHealth';
import { countEnabledSchedules, formatScheduleDateTime, selectUpcomingSchedules } from '../../lib/schedulePresentation';
import { formatRelativeTime } from '../../lib/taskCenter';
import type { TaskSnapshot } from '../../types/electron';
import { cn } from '../../lib/utils';
import { RefreshIconButton } from '../ui/refresh-button';
import { Button } from '../ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { HealthRail, HealthSignal, LoadingPanel, Panel, SurfaceEmpty, SurfaceLoading } from './surfaces';
import { RunDetailDialog } from './RunDetailDialog';
import { RunHistoryList } from './RunHistoryList';
import { WorkspaceShell } from './WorkspaceShell';

const UPCOMING_LIMIT = 3;
const ATTENTION_LIMIT = 3;
const RUN_PAGE_SIZE = 10;

export function DashboardPage({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const loadedRef = useRef(false);
  const [firstLoad, setFirstLoad] = useState(true);
  const [runLimit, setRunLimit] = useState(RUN_PAGE_SIZE);
  const [loadingMore, setLoadingMore] = useState(false);
  const [detailRun, setDetailRun] = useState<TaskSnapshot | null>(null);
  const [showAllAttention, setShowAllAttention] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    void Promise.all([
      electron.loadSchedules({ silent: true }),
      electron.loadScheduleRunSummaries({ silent: true }),
      electron.loadQueueStats({ silent: true }),
      electron.loadRuns({ limit: RUN_PAGE_SIZE, silent: true }),
    ]).finally(() => setFirstLoad(false));
  }, [electron]);

  const loadMoreRuns = (): void => {
    if (loadingMore) return;
    const next = runLimit + RUN_PAGE_SIZE;
    setRunLimit(next);
    setLoadingMore(true);
    // loadRuns 是整表替换而非追加，所以直接按更大的 limit 重取即可拿到下一页。
    void electron.loadRuns({ limit: next, silent: true }).finally(() => setLoadingMore(false));
  };

  const activeCount = electron.queueStats?.activeCount ?? 0;
  const queuedCount = electron.queueStats?.queuedCount ?? 0;
  const concurrency = electron.queueStats?.concurrency ?? 1;

  const upcoming = useMemo(
    () => selectUpcomingSchedules(electron.schedules, UPCOMING_LIMIT),
    [electron.schedules],
  );
  const health = useMemo(
    () => buildOperationalHealthSnapshot(electron.runs, electron.schedules, electron.scheduleRunSummaries),
    [electron.runs, electron.schedules, electron.scheduleRunSummaries],
  );
  const nextSchedule = upcoming[0];
  const enabledScheduleCount = countEnabledSchedules(electron.schedules);
  const showAttention = firstLoad || health.attention.length > 0;
  const showUpcoming = firstLoad || enabledScheduleCount > 0;

  const inspectRun = (run: TaskSnapshot): void => {
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
            await Promise.all([
              electron.loadQueueStats(),
              electron.loadSchedules(),
              electron.loadScheduleRunSummaries(),
              electron.loadRuns({ limit: runLimit }),
            ]);
          }}
        />
      }
      title="概览"
    >
      <HealthRail>
        <HealthSignal
          loading={firstLoad}
          onClick={() => navigate(`${ROUTE_PATHS.tasks}?view=running`)}
          detail={queuedCount > 0 ? `排队 ${queuedCount} · 并发上限 ${concurrency}` : `并发上限 ${concurrency}`}
          icon={<Activity className="h-3.5 w-3.5" strokeWidth={1.5} />}
          label="当前执行"
          state={activeCount > 0 ? 'live' : 'idle'}
          value={activeCount > 0 ? `${activeCount} 个运行中` : '空闲'}
        />
        <HealthSignal
          loading={firstLoad}
          onClick={health.attention.length > 0 ? () => {
            setShowAllAttention(true);
            document.getElementById('dashboard-attention')?.scrollIntoView({ block: 'start' });
          } : undefined}
          detail={health.attention.length > 0 ? '等待处理' : '没有未恢复异常'}
          icon={<AlertTriangle className="h-3.5 w-3.5" strokeWidth={1.5} />}
          label="需关注"
          state={health.attention.length > 0 ? 'error' : 'success'}
          value={health.attention.length}
        />
        <HealthSignal
          loading={firstLoad}
          detail={`失败 ${health.recentResult.failed} · 样本 ${health.recentResult.sampleSize}`}
          icon={<CheckCircle2 className="h-3.5 w-3.5" strokeWidth={1.5} />}
          label="最近运行成功"
          state={health.recentResult.sampleSize === 0 ? 'idle' : health.recentResult.failed > 0 ? 'warning' : 'success'}
          value={health.recentResult.succeeded}
        />
        <HealthSignal
          loading={firstLoad}
          onClick={() => openSchedule()}
          detail={nextSchedule?.name ?? (enabledScheduleCount > 0 ? '启用调度尚无下一次排期，请检查触发规则' : '没有启用调度')}
          icon={<CalendarClock className="h-3.5 w-3.5" strokeWidth={1.5} />}
          label="下一次触发"
          value={nextSchedule === undefined ? '--' : formatScheduleDateTime(nextSchedule.nextRunAt, nextSchedule.timezone)}
        />
      </HealthRail>

      {(showAttention || showUpcoming) && <div className="@container min-w-0">
        <div className={cn('grid min-w-0 items-start gap-5', showAttention && showUpcoming && '@min-4xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]')}>
          {showAttention && <div id="dashboard-attention" className="min-w-0 scroll-mt-5">
            <AttentionPanel
              items={showAllAttention ? health.attention : health.attention.slice(0, ATTENTION_LIMIT)}
              loading={firstLoad}
              total={health.attention.length}
              showAll={showAllAttention}
              onToggleAll={() => setShowAllAttention((current) => !current)}
              onInspectRun={inspectRun}
              onOpenSchedule={(name) => openSchedule(name, true)}
            />
          </div>}

          {showUpcoming && <Panel
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
            {firstLoad ? <SurfaceLoading label="读取调度计划…" /> : upcoming.length === 0 ? (
              <SurfaceEmpty
                action={<Button onClick={() => openSchedule()} size="sm" variant="secondary">前往调度中心</Button>}
                title="没有待触发的调度"
                hint="在调度中心新建并启用触发器后，这里会显示最近计划。"
              />
            ) : (
              <div>
                {upcoming.map((schedule) => (
                  <button
                    className="flex w-full items-center justify-between gap-4 border-b border-rule px-5 py-3 text-left transition-colors last:border-b-0 hover:bg-paper"
                    aria-label={`${schedule.name}，${formatScheduleDateTime(schedule.nextRunAt, schedule.timezone)}（${schedule.timezone}），查看调度`}
                    key={schedule.scheduleId}
                    onClick={() => openSchedule(schedule.name)}
                    type="button"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-[12px] font-medium text-ink-2">{schedule.name}</span>
                      <span className="mt-0.5 block truncate text-[10px] text-ink-3">{schedule.task.flowName}</span>
                    </span>
                    <span className="shrink-0 text-right font-mono text-[10px] tabular-nums text-ink-3">
                      <span className="block" title={`时区 ${schedule.timezone}`}>{formatScheduleDateTime(schedule.nextRunAt, schedule.timezone)}</span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </Panel>}
        </div>
      </div>}

      {firstLoad && electron.runs.length === 0 ? (
        <LoadingPanel label="加载运行历史…" />
      ) : (
        <RunHistoryList
          hasMore={electron.runs.length >= runLimit}
          loadingMore={loadingMore}
          onInspectRun={(run) => {
            inspectRun(run);
          }}
          onLoadMore={loadMoreRuns}
          onRefresh={() => electron.loadRuns({ limit: runLimit })}
          runs={electron.runs}
        />
      )}

      <RunDetailDialog
        onLoadDetail={electron.getRunDetail}
        onReadArtifact={electron.getArtifactContent}
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
  loading,
  total,
  showAll,
  onToggleAll,
  onInspectRun,
  onOpenSchedule,
}: {
  items: OperationalAttentionItem[];
  loading: boolean;
  total: number;
  showAll: boolean;
  onToggleAll: () => void;
  onInspectRun: (run: TaskSnapshot) => void;
  onOpenSchedule: (scheduleName: string) => void;
}): ReactElement {
  return (
    <Panel
      bodyClassName="p-0"
      icon={<AlertTriangle className="h-3.5 w-3.5" strokeWidth={1.5} />}
      label="需关注"
      action={total > ATTENTION_LIMIT ? <Button onClick={onToggleAll} size="sm" variant="ghost">{showAll ? '收起' : `查看全部 ${total} 项`}</Button> : undefined}
    >
      {loading ? <SurfaceLoading label="读取待处理事项…" /> : (
        <div>
          {items.map((item) => {
            const config = ATTENTION_META[item.kind];
            const inspect = (): void => {
              if (item.run !== undefined) onInspectRun(item.run);
              if (item.schedule !== undefined) onOpenSchedule(item.schedule.name);
            };
            return (
              <div
                className="flex w-full items-center gap-3 border-b border-rule px-5 py-2.5 last:border-b-0"
                key={item.id}
              >
                <button
                  aria-label={`查看 ${item.title} 的${item.schedule === undefined ? '运行' : '调度'}详情`}
                  className="min-w-0 flex-1 truncate rounded-sm text-left text-[12px] font-medium text-ink-2 hover:text-accent-strong hover:underline hover:underline-offset-4"
                  onClick={inspect}
                  type="button"
                >
                  {item.title}
                </button>
                <Tooltip>
                  <TooltipTrigger
                    aria-label={`${config.label}，查看 ${item.title} 的详情`}
                    className={cn('inline-flex min-h-7 shrink-0 items-center rounded-sm text-[11px] font-medium underline decoration-dotted underline-offset-4', config.text)}
                    onClick={inspect}
                    type="button"
                  >
                    {config.label}
                  </TooltipTrigger>
                  <TooltipContent className="max-h-[min(20rem,calc(100vh-2rem))] max-w-[min(24rem,calc(100vw-2rem))] overflow-y-auto whitespace-pre-wrap wrap-break-word leading-5" side="top">
                    {item.detail}
                  </TooltipContent>
                </Tooltip>
                <span className="shrink-0 text-[10px] tabular-nums text-ink-3">
                  {formatRelativeTime(item.updatedAt)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

const ATTENTION_META = {
  confirmation: { label: '等待确认', text: 'text-amber-700' },
  'run-error': { label: '运行失败', text: 'text-red-600' },
  'schedule-error': { label: '调度错误', text: 'text-red-600' },
} satisfies Record<OperationalAttentionItem['kind'], { label: string; text: string }>;
