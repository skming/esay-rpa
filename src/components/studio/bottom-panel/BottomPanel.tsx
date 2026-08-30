import type { Node } from '@xyflow/react';
import { ScrollText, Sparkles } from 'lucide-react';
import type { PointerEvent, ReactElement } from 'react';
import { useMemo } from 'react';

import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import { cn } from '../../../lib/utils';
import {
  BOTTOM_PANEL_MAX_HEIGHT,
  BOTTOM_PANEL_MIN_HEIGHT,
  useBottomPanelStore
} from '../../../stores/useBottomPanelStore';
import type { RpaNodeData, RunLogLevel } from '../../../types/rpa';
import { Button } from '../../ui/button';
import { ArtifactRows } from './ArtifactRows';
import { BreakpointRows } from './BreakpointRows';
import { BottomPanelTabs } from './BottomPanelTabs';
import { ErrorRows } from './ErrorRows';
import { LogRows } from './LogRows';
import { PanelEmptyState } from './PanelEmptyState';
import { ResizeHandle } from './ResizeHandle';
import { VariableRows } from './VariableRows';
import { buildErrorSummary, getLogTone } from './bottomPanelUtils';

const ALL_LOG_LEVELS: RunLogLevel[] = ['error', 'warn', 'input', 'success', 'running', 'info'];
const LOG_LEVEL_LABELS: Record<RunLogLevel, string> = {
  error: '错误', warn: '警告', input: '输入', success: '成功', running: '执行', info: '信息'
};

export function BottomPanel({
  electron,
  flowNodes,
  onBreakpointChange,
  onClose,
  onSelectedNodeChange,
  onAiAnalyze
}: {
  electron: ElectronBridgeState;
  flowNodes: Node<RpaNodeData>[];
  onBreakpointChange: (nodeId: string, enabled: boolean) => void;
  onClose: () => void;
  onSelectedNodeChange: (nodeId: string) => void;
  onAiAnalyze?: (taskId: string, errorSummary: string) => void;
}): ReactElement {
  const activeTab = useBottomPanelStore((state: ReturnType<typeof useBottomPanelStore.getState>) => state.activeTab);
  const height = useBottomPanelStore((state: ReturnType<typeof useBottomPanelStore.getState>) => state.height);
  const hiddenLogLevels = useBottomPanelStore((state: ReturnType<typeof useBottomPanelStore.getState>) => state.hiddenLogLevels);
  const setActiveTab = useBottomPanelStore((state: ReturnType<typeof useBottomPanelStore.getState>) => state.setActiveTab);
  const setHeight = useBottomPanelStore((state: ReturnType<typeof useBottomPanelStore.getState>) => state.setHeight);
  const toggleLogLevel = useBottomPanelStore((state: ReturnType<typeof useBottomPanelStore.getState>) => state.toggleLogLevel);
  const logs = electron.logs;
  // 每来一条日志都要重算，所以按等级分桶只走一遍，而不是每个等级各扫一次 logs
  const { errorRows, levelCounts } = useMemo(() => {
    const counts = Object.fromEntries(ALL_LOG_LEVELS.map((level) => [level, 0])) as Record<RunLogLevel, number>;
    const errors: typeof logs = [];
    for (const log of logs) {
      counts[log.level] += 1;
      if (log.level === 'error') errors.push(log);
    }
    return { errorRows: errors, levelCounts: counts };
  }, [logs]);
  const filteredLogs = useMemo(
    () => logs.filter((log) => !hiddenLogLevels.includes(log.level)),
    [logs, hiddenLogLevels]
  );
  const nodeTitleById = useMemo(
    () =>
      flowNodes.reduce<Record<string, string>>((result, node) => {
        result[node.id] = node.data.title;
        return result;
      }, {}),
    [flowNodes]
  );
  // 只在点导出时拼接，不必每来一条日志就重建整段文本
  const buildLogContent = (): string =>
    logs.map((log) => `${log.time} [${log.level}] ${log.message}${log.detail !== undefined ? ` ${log.detail}` : ''}`).join('\n');
  const lastRunId = electron.lastRunId;
  const refreshArtifacts =
    lastRunId === null
      ? undefined
      : (): void => {
        void electron.loadArtifacts(lastRunId);
      };

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>): void => {
    const startY = event.clientY;
    const startHeight = height;
    const target = event.currentTarget;

    target.setPointerCapture(event.pointerId);

    const handlePointerMove = (moveEvent: globalThis.PointerEvent): void => {
      const nextHeight = Math.min(BOTTOM_PANEL_MAX_HEIGHT, Math.max(BOTTOM_PANEL_MIN_HEIGHT, startHeight + startY - moveEvent.clientY));
      setHeight(nextHeight);
    };

    const handlePointerUp = (upEvent: globalThis.PointerEvent): void => {
      target.releasePointerCapture(upEvent.pointerId);
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);
  };

  return (
    <section className="relative flex shrink-0 flex-col border-t border-slate-200 bg-white" style={{ height }}>
      <ResizeHandle onPointerDown={handlePointerDown} />
      <BottomPanelTabs
        activeTab={activeTab}
        artifactCount={electron.artifacts.length}
        errorCount={errorRows.length}
        onActiveTabChange={setActiveTab}
        onClose={onClose}
        onExportLogs={() => void electron.exportLogs(buildLogContent())}
        onRefresh={refreshArtifacts}
      />
      {activeTab === 'logs' && logs.length > 0 && (
        <div className="flex items-center gap-0.5 border-b border-slate-100 px-2 py-1">
          {ALL_LOG_LEVELS.map((level) => {
            const tone = getLogTone(level);
            const hidden = hiddenLogLevels.includes(level);
            const count = levelCounts[level];
            return (
              <button
                aria-label={hidden ? `显示${LOG_LEVEL_LABELS[level]}日志` : `隐藏${LOG_LEVEL_LABELS[level]}日志`}
                aria-pressed={!hidden}
                className={cn(
                  'flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] transition-all hover:bg-slate-100',
                  hidden ? 'text-slate-500' : tone.text
                )}
                key={level}
                onClick={() => toggleLogLevel(level)}
                title={hidden ? `显示${LOG_LEVEL_LABELS[level]}` : `隐藏${LOG_LEVEL_LABELS[level]}`}
                type="button"
              >
                <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', hidden ? 'bg-slate-300' : tone.dot)} />
                {LOG_LEVEL_LABELS[level]}
                {count > 0 && <span className="font-mono tabular-nums">{count}</span>}
              </button>
            );
          })}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
        {activeTab === 'logs' && (
          filteredLogs.length === 0
            ? <PanelEmptyState icon={ScrollText} text={logs.length === 0 ? '暂无日志' : '当前筛选条件无匹配日志'} />
            : <LogRows nodeTitleById={nodeTitleById} onJumpToNode={onSelectedNodeChange} rows={filteredLogs} />
        )}
        {activeTab === 'variables' && <VariableRows rows={electron.variableViews} />}
        {activeTab === 'breakpoints' && (
          <BreakpointRows
            nodes={flowNodes}
            onBreakpointChange={onBreakpointChange}
            onDebugControl={electron.debugControl}
            onJumpToNode={onSelectedNodeChange}
            onStopDebug={() => void electron.stopRun()}
            runtimeStatus={electron.runtimeStatus}
          />
        )}
        {activeTab === 'errors' && (
          <div className="flex flex-col gap-2">
            {onAiAnalyze && electron.lastRunId !== null && errorRows.length > 0 && (
              <Button
                className="self-start"
                onClick={() => onAiAnalyze(electron.lastRunId!, buildErrorSummary(errorRows, nodeTitleById))}
                size="sm"
                variant="outline"
              >
                <Sparkles className="h-3.5 w-3.5" strokeWidth={1.5} />
                AI 分析错误
              </Button>
            )}
            <ErrorRows onJumpToNode={onSelectedNodeChange} rows={errorRows} />
          </div>
        )}
        {activeTab === 'artifacts' && (
          <ArtifactRows
            artifactContent={electron.artifactContent}
            // 'run-' 前缀是本地模拟运行 id 的约定，用来和后端真实 task_id 区分
            isMockRun={electron.lastRunId !== null && electron.lastRunId.startsWith('run-')}
            lastRunId={electron.lastRunId}
            onReadArtifact={electron.readArtifact}
            rows={electron.artifacts}
          />
        )}
      </div>
    </section>
  );
}
