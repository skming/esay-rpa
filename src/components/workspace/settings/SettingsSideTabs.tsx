import type { ReactElement } from 'react';
import { cn } from '../../../lib/utils';
import type { SettingsSection } from './types';

export function SettingsSideTabs({
  active,
  onChange,
}: {
  active: SettingsSection;
  onChange: (section: SettingsSection) => void;
}): ReactElement {
  return (
    <aside
      aria-label="设置分类"
      className="bg-paper-sunk/45 p-2"
      role="tablist"
    >
      <div className="grid gap-1">
        <SettingsTabButton
          active={active === 'system'}
          label="系统信息"
          onClick={() => onChange('system')}
        />
        <SettingsTabButton
          active={active === 'ai'}
          label="AI 模型配置"
          onClick={() => onChange('ai')}
        />
        <SettingsTabButton
          active={active === 'notifications'}
          label="通知渠道"
          onClick={() => onChange('notifications')}
        />
        <SettingsTabButton
          active={active === 'extension'}
          label="浏览器扩展"
          onClick={() => onChange('extension')}
        />
      </div>
    </aside>
  );
}

function SettingsTabButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}): ReactElement {
  return (
    <button
      aria-selected={active}
      className={cn(
        'flex h-9 w-full items-center gap-2 rounded-md pl-2.5 pr-3 text-left text-[12px] font-medium transition-colors duration-150',
        active
          ? 'bg-surface text-ink shadow-xs ring-1 ring-rule'
          : 'text-ink-2 hover:bg-surface/70 hover:text-ink',
      )}
      onClick={onClick}
      role="tab"
      type="button"
    >
      {/* 选中标记，占位常驻以免文字跳动 */}
      <span className={cn('h-4 w-0.5 shrink-0 rounded-full', active ? 'bg-accent' : 'bg-transparent')} />
      {label}
    </button>
  );
}
