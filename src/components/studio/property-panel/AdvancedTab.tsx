import type { ReactElement } from 'react';

import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import { QueueSection } from './QueueSection';
import { ScheduleSection } from './ScheduleSection';
import { ScriptGeneratorSection } from './ScriptGeneratorSection';

export function AdvancedTab({ electron }: { electron: ElectronBridgeState }): ReactElement {
  return (
    <>
      <ScheduleSection electron={electron} />
      <ScriptGeneratorSection electron={electron} />
      <QueueSection electron={electron} />
    </>
  );
}
