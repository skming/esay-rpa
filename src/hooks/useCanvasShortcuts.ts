import { useEffect, useRef } from 'react';

import { hasOpenOverlay, isEditableTarget, isInteractiveTarget } from '../lib/keyboardTargets';
import type { CanvasToolMode } from '../types/rpa';

function isSpaceKey(event: KeyboardEvent): boolean {
  return event.code === 'Space' || event.key === ' ' || event.key === 'Spacebar';
}

function hasCommandModifier(event: KeyboardEvent): boolean {
  return event.metaKey || event.ctrlKey;
}

function hasPlainKeyPress(event: KeyboardEvent): boolean {
  return !event.altKey && !event.ctrlKey && !event.metaKey;
}

export function useCanvasShortcuts({
  mode,
  onFitView,
  onModeChange,
  onResetZoom,
  onToggleFocusMode,
  onToggleGrid,
  onToggleMiniMap,
  onZoomIn,
  onZoomOut
}: {
  mode: CanvasToolMode;
  onFitView: () => void;
  onModeChange: (mode: CanvasToolMode) => void;
  onResetZoom: () => void;
  onToggleFocusMode: () => void;
  onToggleGrid: () => void;
  onToggleMiniMap: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
}): void {
  // 记录按下空格前的工具模式，松开空格后恢复；非 null 时表示正处于"空格临时平移"状态
  const temporaryPanOriginRef = useRef<CanvasToolMode | null>(null);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent): void => {
      // 组字期间的 Space 是候选翻页，不是临时平移
      if (event.isComposing || isEditableTarget(event.target)) {
        return;
      }

      // 浮层开着时画布被挡住：切工具模式、开关网格都看不到，按键该留给浮层
      if (hasOpenOverlay()) {
        return;
      }

      if (isSpaceKey(event)) {
        // 焦点在按钮上时 Space 属于「激活这个控件」，抢下来会让工具栏整排按钮按不动
        if (isInteractiveTarget(event.target)) {
          return;
        }
        event.preventDefault();
        if (event.repeat || temporaryPanOriginRef.current !== null) {
          return;
        }
        temporaryPanOriginRef.current = mode;
        onModeChange('pan');
        return;
      }

      const key = event.key.toLowerCase();
      if (hasCommandModifier(event)) {
        if (event.key === '+' || event.key === '=') {
          event.preventDefault();
          onZoomIn();
          return;
        }
        if (event.key === '-' || event.key === '_') {
          event.preventDefault();
          onZoomOut();
          return;
        }
        if (event.key === '0') {
          event.preventDefault();
          onResetZoom();
          return;
        }
        if (event.key === '\\') {
          event.preventDefault();
          onToggleFocusMode();
          return;
        }
        return;
      }

      if (!hasPlainKeyPress(event)) {
        return;
      }

      if (key === 'v') {
        event.preventDefault();
        onModeChange('select');
        return;
      }

      if (key === 'h') {
        event.preventDefault();
        onModeChange('pan');
        return;
      }

      if (key === 'f') {
        event.preventDefault();
        onFitView();
        return;
      }

      if (key === 'g') {
        event.preventDefault();
        onToggleGrid();
        return;
      }

      if (key === 'm') {
        event.preventDefault();
        onToggleMiniMap();
      }
    };

    const handleKeyUp = (event: KeyboardEvent): void => {
      if (!isSpaceKey(event) || temporaryPanOriginRef.current === null) {
        return;
      }

      event.preventDefault();
      onModeChange(temporaryPanOriginRef.current);
      temporaryPanOriginRef.current = null;
    };

    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('keyup', handleKeyUp);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('keyup', handleKeyUp);
    };
  }, [mode, onFitView, onModeChange, onResetZoom, onToggleFocusMode, onToggleGrid, onToggleMiniMap, onZoomIn, onZoomOut]);
}
