import type { AppPage } from '../components/layout/NavRail';

export const DEFAULT_ROUTE: AppPage = 'dashboard';

export const ROUTE_PATHS = {
  dashboard: '/',
  permissions: '/permissions',
  scheduler: '/scheduler',
  settings: '/settings',
  statistics: '/statistics',
  studio: '/studio',
  tasks: '/tasks',
} satisfies Record<AppPage, string>;

export const PAGE_BY_PATH = Object.fromEntries(
  Object.entries(ROUTE_PATHS).map(([page, path]) => [path, page])
) as Record<string, AppPage>;

export function pathForPage(page: AppPage): string {
  return ROUTE_PATHS[page] ?? ROUTE_PATHS[DEFAULT_ROUTE];
}

export function pageForPath(pathname: string): AppPage {
  return PAGE_BY_PATH[pathname] ?? DEFAULT_ROUTE;
}
