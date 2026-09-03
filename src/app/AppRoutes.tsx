import type { ReactElement } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';

import { SchedulerPage } from '../components/scheduler/SchedulerPage';
import { DashboardPage } from '../components/workspace/DashboardPage';
import { PermissionsPage } from '../components/workspace/PermissionsPage';
import { SettingsPage } from '../components/workspace/SettingsPage';
import { TaskCenterPage } from '../components/workspace/TaskCenterPage';
import type { AppRuntimeContext } from './appContext';
import { ROUTE_PATHS } from './routeConfig';
import { StudioRoute } from './StudioRoute';

export function AppRoutes(context: AppRuntimeContext): ReactElement {
  const navigate = useNavigate();

  return (
    <Routes>
      <Route element={<DashboardPage electron={context.electron} />} path={ROUTE_PATHS.dashboard} />
      <Route element={<StudioRoute {...context} />} path={ROUTE_PATHS.studio} />
      <Route element={<TaskCenterPage electron={context.electron} onOpenStudio={() => navigate(ROUTE_PATHS.studio)} />} path={ROUTE_PATHS.tasks} />
      <Route element={<SchedulerPage electron={context.electron} />} path={ROUTE_PATHS.scheduler} />
      <Route element={<SettingsPage electron={context.electron} />} path={ROUTE_PATHS.settings} />
      <Route element={<PermissionsPage electron={context.electron} />} path={ROUTE_PATHS.permissions} />
      <Route element={<Navigate replace to={ROUTE_PATHS.dashboard} />} path="*" />
    </Routes>
  );
}
