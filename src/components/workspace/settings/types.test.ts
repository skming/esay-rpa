import { describe, expect, it } from 'vitest';

import { SETTINGS_SECTIONS, nextSettingsSection } from './types';

describe('nextSettingsSection', () => {
  const first = SETTINGS_SECTIONS[0].section;
  const last = SETTINGS_SECTIONS[SETTINGS_SECTIONS.length - 1].section;

  it('方向键在首尾之间环绕', () => {
    expect(nextSettingsSection(first, 'ArrowUp')).toBe(last);
    expect(nextSettingsSection(last, 'ArrowDown')).toBe(first);
    expect(nextSettingsSection(first, 'ArrowDown')).toBe(SETTINGS_SECTIONS[1].section);
  });

  it('Home/End 跳到首尾', () => {
    expect(nextSettingsSection(last, 'Home')).toBe(first);
    expect(nextSettingsSection(first, 'End')).toBe(last);
  });

  it('其他按键不由 tablist 处理', () => {
    expect(nextSettingsSection(first, 'Tab')).toBeNull();
    expect(nextSettingsSection(first, 'a')).toBeNull();
  });
});
