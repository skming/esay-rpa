import type { KeyboardEvent, ReactElement } from 'react';

import { cn } from '../../../lib/utils';
import type { SettingsSection } from './types';
import { SETTINGS_SECTIONS, nextSettingsSection, settingsPanelId, settingsTabId } from './types';

export function SettingsSideTabs({
  active,
  onChange,
}: {
  active: SettingsSection;
  onChange: (section: SettingsSection) => void;
}): ReactElement {
  // tablist 的键盘约定是方向键换页、Tab 键跳出整组，所以只有选中项留在 Tab 序列里。
  const moveFocus = (event: KeyboardEvent<HTMLDivElement>): void => {
    const next = nextSettingsSection(active, event.key);
    if (next === null) return;
    event.preventDefault();
    onChange(next);
    document.getElementById(settingsTabId(next))?.focus();
  };

  return (
    <aside aria-label="设置分类" className="bg-paper-sunk/45 p-2">
      <div className="grid gap-1" onKeyDown={moveFocus} role="tablist" aria-orientation="vertical">
        {SETTINGS_SECTIONS.map(({ section, label }) => (
          <SettingsTabButton
            active={section === active}
            key={section}
            label={label}
            onClick={() => onChange(section)}
            section={section}
          />
        ))}
      </div>
    </aside>
  );
}

function SettingsTabButton({
  active,
  label,
  onClick,
  section,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  section: SettingsSection;
}): ReactElement {
  return (
    <button
      aria-controls={settingsPanelId(section)}
      aria-selected={active}
      className={cn(
        'flex h-9 w-full items-center gap-2 rounded-md pl-2.5 pr-3 text-left text-[12px] font-medium transition-colors duration-150',
        active
          ? 'bg-surface text-ink shadow-xs ring-1 ring-rule'
          : 'text-ink-2 hover:bg-surface/70 hover:text-ink',
      )}
      id={settingsTabId(section)}
      onClick={onClick}
      role="tab"
      tabIndex={active ? 0 : -1}
      type="button"
    >
      {/* 选中标记，占位常驻以免文字跳动 */}
      <span className={cn('h-4 w-0.5 shrink-0 rounded-full', active ? 'bg-accent' : 'bg-transparent')} />
      {label}
    </button>
  );
}
