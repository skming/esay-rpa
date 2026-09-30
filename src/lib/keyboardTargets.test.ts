import { afterEach, describe, expect, it, vi } from 'vitest';

import { hasOpenOverlay } from './keyboardTargets';

describe('hasOpenOverlay', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('matches Base UI open-state attributes on keyboard-owning overlays', () => {
    const querySelector = vi.fn(() => null);
    vi.stubGlobal('document', { querySelector });

    expect(hasOpenOverlay()).toBe(false);
    expect(querySelector).toHaveBeenCalledWith(
      '[role="dialog"][data-open],[role="alertdialog"][data-open],[role="menu"][data-open],[role="listbox"][data-open]',
    );
  });
});
