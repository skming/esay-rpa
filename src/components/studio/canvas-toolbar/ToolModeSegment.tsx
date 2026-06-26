import { Hand, MousePointer2 } from 'lucide-react';
import type { ReactElement } from 'react';

import type { CanvasToolMode } from '../../../types/rpa';
import { IconButton } from '../../ui/button';

export function ToolModeSegment({ mode, onModeChange }: { mode: CanvasToolMode; onModeChange: (mode: CanvasToolMode) => void }): ReactElement {
  return (
    <div className="flex shrink-0 overflow-hidden rounded-md border border-slate-200">
      <IconButton active={mode === 'pan'} className="rounded-none border-0" label="平移 (H)" onClick={() => onModeChange('pan')}>
        <Hand className="h-3.5 w-3.5" strokeWidth={1.5} />
      </IconButton>
      <IconButton active={mode === 'select'} className="rounded-none border-0" label="选择 (V)" onClick={() => onModeChange('select')}>
        <MousePointer2 className="h-3.5 w-3.5" strokeWidth={1.5} />
      </IconButton>
    </div>
  );
}
