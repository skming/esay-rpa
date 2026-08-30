export type SettingsSection = 'system' | 'ai' | 'notifications' | 'extension';

export const SETTINGS_SECTIONS: { section: SettingsSection; label: string }[] = [
  { section: 'system', label: '系统信息' },
  { section: 'ai', label: 'AI 模型配置' },
  { section: 'notifications', label: '通知渠道' },
  { section: 'extension', label: '浏览器扩展' },
];

export function settingsTabId(section: SettingsSection): string {
  return `settings-tab-${section}`;
}

export function settingsPanelId(section: SettingsSection): string {
  return `settings-panel-${section}`;
}

/** 返回 null 表示该按键不由 tablist 处理，交回浏览器默认行为。 */
export function nextSettingsSection(active: SettingsSection, key: string): SettingsSection | null {
  const index = SETTINGS_SECTIONS.findIndex((item) => item.section === active);
  const last = SETTINGS_SECTIONS.length - 1;
  const target = key === 'ArrowDown'
    ? (index + 1) % SETTINGS_SECTIONS.length
    : key === 'ArrowUp'
      ? (index + last) % SETTINGS_SECTIONS.length
      : key === 'Home'
        ? 0
        : key === 'End'
          ? last
          : null;
  return target === null ? null : SETTINGS_SECTIONS[target].section;
}
