import { describe, expect, it } from 'vitest';

import { adjustPanelRect, clampPanelRect, defaultPanelRect } from './panelGeometry';

describe('panel geometry', () => {
  it('keeps an oversized or off-screen panel inside the viewport', () => {
    expect(clampPanelRect({ x: 900, y: -20, w: 1400, h: 1000 }, 800, 600)).toEqual({
      x: 0,
      y: 40,
      w: 800,
      h: 560,
    });
  });

  it('fits the default panel into a viewport smaller than the normal minimum', () => {
    expect(defaultPanelRect(280, 320)).toEqual({ x: 0, y: 40, w: 280, h: 280 });
  });

  it('supports independent keyboard move and resize operations', () => {
    const rect = { x: 20, y: 60, w: 440, h: 600 };
    expect(adjustPanelRect(rect, 'ArrowLeft', false, 12)).toEqual({ ...rect, x: 8 });
    expect(adjustPanelRect(rect, 'ArrowDown', true, 12)).toEqual({ ...rect, h: 612 });
  });
});
