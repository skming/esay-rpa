import { describe, expect, it } from 'vitest';

import { SETTINGS_SECTIONS, nextSettingsSection, resolveSettingsSection } from './types';

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

  it('水平分类支持左右键并在首尾环绕', () => {
    expect(nextSettingsSection(first, 'ArrowLeft')).toBe(last);
    expect(nextSettingsSection(last, 'ArrowRight')).toBe(first);
    expect(nextSettingsSection(first, 'ArrowRight')).toBe('ai');
  });

  it('其他按键不由 tablist 处理', () => {
    expect(nextSettingsSection(first, 'Tab')).toBeNull();
    expect(nextSettingsSection(first, 'a')).toBeNull();
  });
});

describe('resolveSettingsSection', () => {
  it('URL 分类优先于导航 state，支持直接打开与历史导航', () => {
    expect(resolveSettingsSection('ai', { settingsSection: 'extension' })).toBe('ai');
    expect(resolveSettingsSection('system', { settingsSection: 'extension' })).toBe('system');
    expect(resolveSettingsSection('notifications', null)).toBe('notifications');
  });

  it('保留现有扩展安装入口使用的导航 state', () => {
    expect(resolveSettingsSection(null, { settingsSection: 'extension' })).toBe('extension');
  });

  it.each([undefined, null, 'extension', 1, {}, { settingsSection: 'unknown' }])('未知 state %j 回退系统信息', (state) => {
    expect(resolveSettingsSection(null, state)).toBe('system');
  });

  it('无效 URL 分类不会渲染空面板', () => {
    expect(resolveSettingsSection('unknown', { settingsSection: 'ai' })).toBe('system');
  });
});
