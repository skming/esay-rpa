import { useEffect, useRef } from 'react';

import type { CanvasToolMode } from '../types/rpa';

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tagName = target.tagName.toLowerCase();
  return tagName === 'input' || tagName === 'textarea' || tagName === 'select' || target.isContentEditable;
}

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
  onZoomIn,
  onZoomOut
}: {
  mode: CanvasToolMode;
  onFitView: () => void;
  onModeChange: (mode: CanvasToolMode) => void;
  onResetZoom: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
}): void {
  const temporaryPanOriginRef = useRef<CanvasToolMode | null>(null);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (isEditableTarget(event.target)) {
        return;
      }

      if (isSpaceKey(event)) {
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
  }, [mode, onFitView, onModeChange, onResetZoom, onZoomIn, onZoomOut]);
}
