import type { ReactElement } from 'react';
import { Suspense, lazy } from 'react';

import type { AppRuntimeContext } from './appContext';

const StudioWorkspace = lazy(() =>
  import('../components/studio/StudioWorkspace').then((module) => ({ default: module.StudioWorkspace }))
);

export function StudioRoute({
  ai,
  bottomPanelOpen,
  canvas,
  electron,
  handleContextAction,
  inputVariables,
  setBottomPanelOpen,
}: AppRuntimeContext): ReactElement {
  return (
    <Suspense fallback={<StudioWorkspaceFallback />}>
      <StudioWorkspace
        ai={ai}
        bottomPanelOpen={bottomPanelOpen}
        canvas={canvas}
        electron={electron}
        handleContextAction={handleContextAction}
        inputVariables={inputVariables}
        setBottomPanelOpen={setBottomPanelOpen}
      />
    </Suspense>
  );
}

function StudioWorkspaceFallback(): ReactElement {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center bg-slate-50 text-xs text-slate-500">
      加载工作台...
    </div>
  );
}
