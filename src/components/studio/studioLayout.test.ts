import { describe, expect, it } from 'vitest';

import { AI_SIDEBAR_WIDTH, MIN_CANVAS_WIDTH, canDockAiSidebar } from './studioLayout';

describe('canDockAiSidebar', () => {
  it('keeps an already docked sidebar when the canvas remains usable', () => {
    expect(canDockAiSidebar(MIN_CANVAS_WIDTH, true)).toBe(true);
  });

  it('moves an already docked sidebar out when it compresses the canvas', () => {
    expect(canDockAiSidebar(MIN_CANVAS_WIDTH - 1, true)).toBe(false);
  });

  it('reserves the sidebar width before allowing a floating panel to dock', () => {
    expect(canDockAiSidebar(MIN_CANVAS_WIDTH + AI_SIDEBAR_WIDTH - 1, false)).toBe(false);
    expect(canDockAiSidebar(MIN_CANVAS_WIDTH + AI_SIDEBAR_WIDTH, false)).toBe(true);
  });
});
