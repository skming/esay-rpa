import type { ReactElement, ReactNode } from 'react';
import { useState } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { cn } from '../../lib/utils';
import { SURFACE } from './surfaces';
import { WorkspaceShell } from './WorkspaceShell';
import { AiModelConfigPanel } from './settings/AiModelConfigPanel';
import { ExtensionConfigPanel } from './settings/ExtensionConfigPanel';
import { NotificationConfigPanel } from './settings/NotificationConfigPanel';
import { SettingsSideTabs } from './settings/SettingsSideTabs';
import { SystemInfoPanel } from './settings/SystemInfoPanel';
import type { SettingsSection } from './settings/types';
import { resolveSettingsSection, settingsPanelId, settingsTabId } from './settings/types';

export function SettingsPage({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeSection = resolveSettingsSection(searchParams.get('section'), location.state);
  const changeSection = (section: SettingsSection): void => {
    if (section === activeSection) return;
    const params = new URLSearchParams(searchParams);
    params.set('section', section);
    setSearchParams(params, { state: null });
  };

  return (
    <WorkspaceShell description="本机配置与运行环境" fill title="设置">
      <section className={cn('grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] overflow-hidden lg:grid-cols-[176px_minmax(0,1fr)] lg:grid-rows-1', SURFACE)}>
        <SettingsSideTabs active={activeSection} onChange={changeSection} />
        <div
          aria-labelledby={settingsTabId(activeSection)}
          className="no-scrollbar min-h-0 min-w-0 overflow-auto border-t border-rule lg:border-l lg:border-t-0"
          id={settingsPanelId(activeSection)}
          role="tabpanel"
          tabIndex={-1}
        >
          {activeSection === 'system' && (
            <SystemInfoPanel electron={electron} />
          )}
          <DraftSettingsPanel active={activeSection === 'ai'}>
            <AiModelConfigPanel active={activeSection === 'ai'} electron={electron} />
          </DraftSettingsPanel>
          <DraftSettingsPanel active={activeSection === 'notifications'}>
            <NotificationConfigPanel electron={electron} />
          </DraftSettingsPanel>
          {activeSection === 'extension' && <ExtensionConfigPanel electron={electron} />}
        </div>
      </section>
    </WorkspaceShell>
  );
}

function DraftSettingsPanel({ active, children }: { active: boolean; children: ReactNode }): ReactElement | null {
  const [opened, setOpened] = useState(active);
  // 首次打开才请求配置；离开分类后保留表单实例，避免丢失尚未保存的草稿。
  if (active && !opened) setOpened(true);
  if (!active && !opened) return null;
  return <div hidden={!active}>{children}</div>;
}
