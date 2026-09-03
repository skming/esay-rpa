import { BarChart2, Calendar, ChevronLeft, ChevronRight, ListTodo, Settings } from 'lucide-react';
import type { ReactElement } from 'react';

import appIcon from '../../assets/app-icon.png';
import { cn } from '../../lib/utils';
import { useWorkspaceStore } from '../../stores/useWorkspaceStore';

export type AppPage = 'dashboard' | 'permissions' | 'scheduler' | 'settings' | 'studio' | 'tasks';

const NAV_ITEMS = [
  { label: '概览', icon: BarChart2, page: 'dashboard' as AppPage },
  { label: '任务中心', icon: ListTodo, page: 'tasks' as AppPage },
  { label: '调度中心', icon: Calendar, page: 'scheduler' as AppPage },
];

export function NavRail({
  activePage,
  onPageChange,
}: {
  activePage: AppPage;
  onPageChange: (page: AppPage) => void;
}): ReactElement {
  const collapsed = useWorkspaceStore((s) => s.navCollapsed);
  const setCollapsed = useWorkspaceStore((s) => s.setNavCollapsed);

  return (
    <nav
      className={cn(
        'nav-glass relative z-(--z-sticky) flex shrink-0 flex-col transition-[width] duration-250 ease-out',
        collapsed ? 'w-12' : 'w-44',
      )}
    >
      {/* 高度与内容区页头(h-12)一致，让跨栏横向节奏对齐 */}
      <div className={cn('flex h-12 shrink-0 items-center border-b border-rule', collapsed ? 'justify-center' : 'justify-between px-3')}>
        {collapsed ? (
          <img alt="Easy RPA" className="h-7 w-7 rounded-md object-contain shadow-sm" src={appIcon} />
        ) : (
          <>
            <div className="flex min-w-0 flex-1 items-center gap-2.5">
              <img alt="" className="h-7 w-7 shrink-0 rounded-md object-contain shadow-sm" src={appIcon} />
              <span className="truncate text-[14px] font-semibold tracking-[-0.01em] text-ink">Easy RPA</span>
            </div>
            <button
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-500 transition-colors duration-150 hover:bg-slate-100 hover:text-slate-600"
              onClick={() => setCollapsed(true)}
              title="收起侧边栏"
              type="button"
            >
              <ChevronLeft className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
          </>
        )}
      </div>

      <div className={cn('flex flex-1 flex-col gap-0.5 overflow-hidden py-2', collapsed ? 'px-1.5' : 'px-2')}>
        {NAV_ITEMS.map(({ label, icon: Icon, page }) => {
          const active = page === activePage;
          return (
            <button
              aria-current={active ? 'page' : undefined}
              className={cn(
                'relative flex h-9 w-full items-center rounded-md text-[12px] font-medium transition-colors duration-150',
                collapsed ? 'justify-center' : 'gap-2.5 px-2.5',
                active
                  ? 'bg-accent-soft text-accent-strong'
                  : 'text-slate-500 hover:bg-slate-100 hover:text-slate-800',
              )}
              key={page}
              onClick={() => onPageChange(page)}
              title={label}
              type="button"
            >
              {active && !collapsed && (
                <span className="pointer-events-none absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-r-full bg-accent" />
              )}
              <Icon
                className={cn(
                  'h-3.5 w-3.5 shrink-0 transition-colors duration-150',
                  active ? 'text-accent-strong' : '',
                )}
                strokeWidth={active ? 2 : 1.75}
              />
              {!collapsed && <span className="truncate">{label}</span>}
            </button>
          );
        })}
      </div>

      <div className={cn('shrink-0 py-2', collapsed ? 'px-1.5' : 'px-2')}>
        <button
          className={cn(
            'relative flex h-9 w-full items-center rounded-md text-[12px] font-medium transition-colors duration-150',
            collapsed ? 'justify-center' : 'gap-2.5 px-2.5',
            activePage === 'settings'
              ? 'bg-accent-soft text-accent-strong'
              : 'text-slate-500 hover:bg-slate-100 hover:text-slate-800',
          )}
          onClick={() => onPageChange('settings')}
          title="设置"
          type="button"
        >
          {activePage === 'settings' && !collapsed && (
            <span className="pointer-events-none absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-r-full bg-accent" />
          )}
          <Settings
            className={cn(
              'h-3.5 w-3.5 shrink-0',
              activePage === 'settings' ? 'text-accent-strong' : '',
            )}
            strokeWidth={activePage === 'settings' ? 2 : 1.75}
          />
          {!collapsed && <span>设置</span>}
        </button>
      </div>

      {collapsed && (
        <button
          className="absolute right-0 top-12 z-(--z-raised) flex h-6 w-6 -translate-y-1/2 translate-x-1/2 items-center justify-center rounded-full border border-rule bg-white text-slate-500 shadow-sm transition-colors duration-150 hover:border-slate-300 hover:bg-slate-50 hover:text-accent-strong"
          onClick={() => setCollapsed(false)}
          title="展开侧边栏"
          type="button"
        >
          <ChevronRight className="h-3 w-3" strokeWidth={2.5} />
        </button>
      )}
    </nav>
  );
}
