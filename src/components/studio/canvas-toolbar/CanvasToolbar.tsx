import { CirclePlay, Grid3X3, LocateFixed, Maximize2, Minimize2, PanelBottom, PanelBottomOpen, Sparkles } from 'lucide-react';
import { type ReactElement, useCallback, useEffect, useRef, useState } from 'react';

import type { CanvasToolMode, CanvasToolbarStats, RuntimeProgress } from '../../../types/rpa';
import { IconButton } from '../../ui/button';
import { CanvasStats } from './CanvasStats';
import { ProgressMeter } from './ProgressMeter';
import { ToolModeSegment } from './ToolModeSegment';
import { ZoomControls } from './ZoomControls';

// 断点按工具栏自身像素宽度（非视口）：>=740 显示统计+进度，>=560 仅统计，<560 都隐藏
const BP_PROGRESS = 740;
const BP_STATS = 560;

export function CanvasToolbar({
  aiPanelOpen,
  bottomPanelOpen,
  gridVisible,
  hasMissingStartEnd,
  mode,
  onFitView,
  onModeChange,
  onRestoreStartEnd,
  onToggleAiPanel,
  onToggleBottomPanel,
  onToggleGrid,
  onZoomIn,
  onZoomOut,
  progress,
  stats,
  zoom,
}: {
  aiPanelOpen: boolean;
  bottomPanelOpen: boolean;
  gridVisible: boolean;
  hasMissingStartEnd: boolean;
  mode: CanvasToolMode;
  onFitView: () => void;
  onModeChange: (mode: CanvasToolMode) => void;
  onRestoreStartEnd: () => void;
  onToggleAiPanel: () => void;
  onToggleBottomPanel: () => void;
  onToggleGrid: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  progress: RuntimeProgress;
  stats: CanvasToolbarStats;
  zoom: number;
}): ReactElement {
  const [isFullscreen, setIsFullscreen] = useState(false);
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

  useEffect(() => {
    const onFullscreenChange = (): void => {
      setIsFullscreen(document.fullscreenElement !== null);
    };
    document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange);
  }, []);

  const handleToggleFullscreen = useCallback((): void => {
    if (document.fullscreenElement) {
      void document.exitFullscreen();
    } else {
      void document.documentElement.requestFullscreen();
    }
  }, []);

  const showStats = toolbarWidth >= BP_STATS;
  const showProgress = toolbarWidth >= BP_PROGRESS;

  return (
    <div
      className="flex h-10 shrink-0 items-center gap-1.5 border-b border-slate-200/70 bg-white/98 px-3"
      ref={rootRef}
    >
      <ToolModeSegment mode={mode} onModeChange={onModeChange} />
      <div className="mx-0.5 h-4 w-px shrink-0 bg-slate-200" />
      <ZoomControls onFitView={onFitView} onZoomIn={onZoomIn} onZoomOut={onZoomOut} zoom={zoom} />
      <IconButton label="适应视图" onClick={onFitView}>
        <LocateFixed className="h-3.5 w-3.5" strokeWidth={1.5} />
      </IconButton>
      <IconButton active={gridVisible} label={gridVisible ? '隐藏网格' : '显示网格'} onClick={onToggleGrid}>
        <Grid3X3 className="h-3.5 w-3.5" strokeWidth={1.5} />
      </IconButton>

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

      <div className="ml-auto flex shrink-0 items-center gap-1.5">
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
          active={aiPanelOpen}
          label={aiPanelOpen ? '关闭 RPA 助手' : '打开 RPA 助手'}
          onClick={onToggleAiPanel}
        >
          <Sparkles className="h-3.5 w-3.5" strokeWidth={1.5} />
        </IconButton>
        <IconButton
          active={isFullscreen}
          label={isFullscreen ? '退出全屏' : '全屏'}
          onClick={handleToggleFullscreen}
        >
          {isFullscreen
            ? <Minimize2 className="h-3.5 w-3.5" strokeWidth={1.5} />
            : <Maximize2 className="h-3.5 w-3.5" strokeWidth={1.5} />}
        </IconButton>
      </div>
    </div>
  );
}
