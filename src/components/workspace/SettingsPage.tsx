import type { ReactElement } from 'react';
import { useState } from 'react';
import { useLocation } from 'react-router-dom';
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
import { settingsPanelId, settingsTabId } from './settings/types';

export function SettingsPage({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const location = useLocation();
  const [activeSection, setActiveSection] = useState<SettingsSection>(() => {
    const requestedSection = (location.state as { settingsSection?: SettingsSection } | null)?.settingsSection;
    return requestedSection ?? 'system';
  });

  return (
    <WorkspaceShell description="本机配置与运行环境" fill title="设置">
      <section className={cn('grid min-h-0 flex-1 overflow-hidden lg:grid-cols-[200px_minmax(0,1fr)]', SURFACE)}>
        <SettingsSideTabs active={activeSection} onChange={setActiveSection} />
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
          {activeSection === 'ai' && <AiModelConfigPanel electron={electron} />}
          {activeSection === 'notifications' && <NotificationConfigPanel electron={electron} />}
          {activeSection === 'extension' && <ExtensionConfigPanel electron={electron} />}
        </div>
      </section>
    </WorkspaceShell>
  );
}
