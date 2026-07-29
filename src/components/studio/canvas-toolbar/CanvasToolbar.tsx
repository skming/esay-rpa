import { CirclePlay, Grid3X3, Map, Maximize2, Minimize2, PanelBottom, PanelBottomOpen } from 'lucide-react';
import { type ReactElement, useEffect, useRef, useState } from 'react';

import type { CanvasToolMode, CanvasToolbarStats, RuntimeProgress } from '../../../types/rpa';
import { IconButton } from '../../ui/button';
import { CanvasStats } from './CanvasStats';
import { ProgressMeter } from './ProgressMeter';
import { ShortcutsDialog } from './ShortcutsDialog';
import { ToolModeSegment } from './ToolModeSegment';
import { ZoomControls } from './ZoomControls';

// 断点按工具栏自身像素宽度（非视口）：>=740 显示统计+进度，>=560 仅统计，<560 都隐藏
const BP_PROGRESS = 740;
const BP_STATS = 560;

export function CanvasToolbar({
  bottomPanelOpen,
  focusMode,
  gridVisible,
  hasMissingStartEnd,
  miniMapVisible,
  mode,
  onFitView,
  onModeChange,
  onResetZoom,
  onRestoreStartEnd,
  onToggleBottomPanel,
  onToggleFocusMode,
  onToggleGrid,
  onToggleMiniMap,
  onZoomIn,
  onZoomOut,
  progress,
  stats,
  zoom,
}: {
  bottomPanelOpen: boolean;
  focusMode: boolean;
  gridVisible: boolean;
  hasMissingStartEnd: boolean;
  miniMapVisible: boolean;
  mode: CanvasToolMode;
  onFitView: () => void;
  onModeChange: (mode: CanvasToolMode) => void;
  onResetZoom: () => void;
  onRestoreStartEnd: () => void;
  onToggleBottomPanel: () => void;
  onToggleFocusMode: () => void;
  onToggleGrid: () => void;
  onToggleMiniMap: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  progress: RuntimeProgress;
  stats: CanvasToolbarStats;
  zoom: number;
}): ReactElement {
  const [toolbarWidth, setToolbarWidth] = useState(9999);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      setToolbarWidth(entry.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const showStats = toolbarWidth >= BP_STATS;
  // 没跑过流程时进度条恒为"0% 00:00"，等真有运行数据再出现
  const hasRunProgress = progress.percent > 0 || progress.elapsedMs > 0;
  const showProgress = hasRunProgress && toolbarWidth >= BP_PROGRESS;

  return (
    <div
      className="flex h-10 shrink-0 items-center gap-1.5 border-b border-slate-200/70 bg-white/98 px-3"
      ref={rootRef}
    >
      <ToolModeSegment mode={mode} onModeChange={onModeChange} />
      <div className="mx-0.5 h-4 w-px shrink-0 bg-slate-200" />
      <ZoomControls
        onFitView={onFitView}
        onResetZoom={onResetZoom}
        onZoomIn={onZoomIn}
        onZoomOut={onZoomOut}
        zoom={zoom}
      />
      {/* "显示什么"的开关单独成组，与上面"看哪里"的视图动作区分开 */}
      <div className="flex shrink-0 overflow-hidden rounded-md border border-slate-200">
        <IconButton
          active={gridVisible}
          className="rounded-none border-0"
          label={gridVisible ? '隐藏网格 (G)' : '显示网格 (G)'}
          onClick={onToggleGrid}
        >
          <Grid3X3 className="h-3.5 w-3.5" strokeWidth={1.5} />
        </IconButton>
        <IconButton
          active={miniMapVisible}
          className="rounded-none border-0 border-l border-slate-200"
          label={miniMapVisible ? '隐藏缩略图 (M)' : '显示缩略图 (M)'}
          onClick={onToggleMiniMap}
        >
          <Map className="h-3.5 w-3.5" strokeWidth={1.5} />
        </IconButton>
      </div>

      {hasMissingStartEnd && (
        <>
          <div className="mx-0.5 h-4 w-px shrink-0 bg-slate-200" />
          <IconButton
            className="text-amber-600 hover:bg-amber-50"
            label="恢复开始/结束节点"
            onClick={onRestoreStartEnd}
          >
            <CirclePlay className="h-3.5 w-3.5" strokeWidth={1.5} />
          </IconButton>
        </>
      )}

      {showStats && (
        <>
          <div className="mx-0.5 h-4 w-px shrink-0 bg-slate-200" />
          <CanvasStats stats={stats} />
        </>
      )}

      {showProgress && (
        <>
          <div className="mx-0.5 h-4 w-px shrink-0 bg-slate-200" />
          <ProgressMeter progress={progress} />
        </>
      )}

      <div className="ml-auto flex shrink-0 items-center gap-1.5 border-l border-slate-200 pl-2.5">
        <ShortcutsDialog />
        <IconButton
          active={bottomPanelOpen}
          label={bottomPanelOpen ? '关闭日志面板' : '打开日志面板'}
          onClick={onToggleBottomPanel}
        >
          {bottomPanelOpen
            ? <PanelBottom className="h-3.5 w-3.5" strokeWidth={1.5} />
            : <PanelBottomOpen className="h-3.5 w-3.5" strokeWidth={1.5} />}
        </IconButton>
        <IconButton
          active={focusMode}
          label={focusMode ? '退出专注模式 (⌘\\)' : '专注模式 · 收起全部面板 (⌘\\)'}
          onClick={onToggleFocusMode}
        >
          {focusMode
            ? <Minimize2 className="h-3.5 w-3.5" strokeWidth={1.5} />
            : <Maximize2 className="h-3.5 w-3.5" strokeWidth={1.5} />}
        </IconButton>
      </div>
    </div>
  );
}
