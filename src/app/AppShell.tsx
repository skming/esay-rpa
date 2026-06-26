import type { ReactElement } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { NavRail } from '../components/layout/NavRail';
import { TitleBar } from '../components/layout/TitleBar';
import { ToastStack } from '../components/layout/ToastStack';
import { TopBar } from '../components/layout/TopBar';
import { DeleteNodeDialog } from '../components/studio/DeleteNodeDialog';
import { UserInputDialog } from '../components/studio/UserInputDialog';
import type { AppRuntimeContext } from './appContext';
import { AppRoutes } from './AppRoutes';
import { BackendBootScreen } from './BackendBootScreen';
import { pageForPath, pathForPage } from './routeConfig';

export function AppShell(context: AppRuntimeContext): ReactElement {
  const location = useLocation();
  const navigate = useNavigate();
  const activePage = pageForPath(location.pathname);
  const backendStatus = context.electron.backendStatus;
  const backendReady = backendStatus?.status === 'ready';
  const backendError = backendStatus?.status === 'error';

  return (
    <div className="flex h-screen min-h-180 min-w-5xl flex-col overflow-hidden bg-slate-50 text-slate-900 antialiased">
      <TitleBar electron={context.electron} />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {backendReady && (
          <NavRail
            activePage={activePage}
            onPageChange={(page) => navigate(pathForPage(page))}
          />
        )}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {!backendReady ? (
            <BackendBootScreen
              error={backendError ? (backendStatus?.error ?? '后端启动失败') : null}
              onRetry={() => void context.electron.restartBackend()}
            />
          ) : (
            <>
              <TopBar
                draftAutosave={context.draftAutosave}
                electron={context.electron}
                selectedNodeAction={context.canvas.selectedNode?.data.action}
                selectedNodeId={context.canvas.selectedNodeId}
                selectedNodeTitle={context.canvas.selectedNode?.data.title ?? '未选择步骤'}
                visible={activePage === 'studio'}
              />
              <AppRoutes {...context} />
            </>
          )}
        </div>
      </div>
      <ToastStack onDismiss={context.electron.dismissToast} toasts={context.electron.toasts} />
      <DeleteNodeDialog
        onConfirm={context.canvas.confirmDeleteNode}
        onOpenChange={(open) => { if (!open) context.canvas.setDeleteTarget(null); }}
        target={context.canvas.deleteTarget}
      />
      <UserInputDialog
        prompt={context.electron.inputPrompt}
        onCancel={() => { void context.electron.stopRun(); }}
        onSubmit={(value) => { void context.electron.provideInput(value); }}
      />
    </div>
  );
}
