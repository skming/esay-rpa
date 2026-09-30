export type PanelRect = { x: number; y: number; w: number; h: number };
export type ResizeDir = 'e' | 's' | 'se' | 'sw' | 'w' | 'n' | 'ne' | 'nw';

export const MIN_PANEL_WIDTH = 320;
export const MAX_PANEL_WIDTH = 1100;
export const MIN_PANEL_HEIGHT = 360;
export const PANEL_TOP_INSET = 40;

export function clampPanelRect(rect: PanelRect, viewportWidth: number, viewportHeight: number): PanelRect {
  const viewportW = Math.max(1, viewportWidth);
  const viewportH = Math.max(PANEL_TOP_INSET + 1, viewportHeight);
  const minW = Math.min(MIN_PANEL_WIDTH, viewportW);
  const minH = Math.min(MIN_PANEL_HEIGHT, viewportH - PANEL_TOP_INSET);
  const w = Math.max(minW, Math.min(MAX_PANEL_WIDTH, viewportW, rect.w));
  const h = Math.max(minH, Math.min(viewportH - PANEL_TOP_INSET, rect.h));
  return {
    x: Math.max(0, Math.min(viewportW - w, rect.x)),
    y: Math.max(PANEL_TOP_INSET, Math.min(viewportH - h, rect.y)),
    w,
    h,
  };
}

export function defaultPanelRect(viewportWidth: number, viewportHeight: number): PanelRect {
  const w = Math.min(440, viewportWidth - 32);
  const h = Math.min(viewportHeight - 80, 720);
  return clampPanelRect(
    { x: viewportWidth - w - 16, y: 44, w, h },
    viewportWidth,
    viewportHeight,
  );
}

export function adjustPanelRect(
  rect: PanelRect,
  key: 'ArrowLeft' | 'ArrowRight' | 'ArrowUp' | 'ArrowDown',
  resize: boolean,
  step: number,
): PanelRect {
  const next = { ...rect };
  if (resize) {
    if (key === 'ArrowLeft') next.w -= step;
    if (key === 'ArrowRight') next.w += step;
    if (key === 'ArrowUp') next.h -= step;
    if (key === 'ArrowDown') next.h += step;
  } else {
    if (key === 'ArrowLeft') next.x -= step;
    if (key === 'ArrowRight') next.x += step;
    if (key === 'ArrowUp') next.y -= step;
    if (key === 'ArrowDown') next.y += step;
  }
  return next;
}
