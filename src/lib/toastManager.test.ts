import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { clearAppToasts, pushAppToast } from './toastManager';

describe('toastManager', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-29T00:00:00Z'));
    clearAppToasts();
  });

  afterEach(() => {
    clearAppToasts();
    vi.useRealTimers();
  });

  it('updates an identical recent toast instead of creating another card', () => {
    const firstId = pushAppToast('success', '流程已保存');

    vi.advanceTimersByTime(1_000);

    expect(pushAppToast('success', '流程已保存')).toBe(firstId);
  });

  it('creates a new toast after the dedupe window', () => {
    const firstId = pushAppToast('success', '流程已保存');

    vi.advanceTimersByTime(2_001);

    expect(pushAppToast('success', '流程已保存')).not.toBe(firstId);
  });
});
