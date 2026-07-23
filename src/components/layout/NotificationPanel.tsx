import { AlertTriangle, Bell, CheckCheck, CheckCircle2, Info, Trash2, XCircle } from 'lucide-react';
import type { ReactElement, ReactNode } from 'react';

import { cn } from '../../lib/utils';
import { useNotificationStore, type AppNotification, type NotificationKind } from '../../stores/useNotificationStore';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';

const KIND_ICON: Record<NotificationKind, ReactElement> = {
  success: <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.75} />,
  error: <XCircle className="h-3.5 w-3.5 text-red-500" strokeWidth={1.75} />,
  warning: <AlertTriangle className="h-3.5 w-3.5 text-amber-500" strokeWidth={1.75} />,
  info: <Info className="h-3.5 w-3.5 text-blue-500" strokeWidth={1.75} />,
};

function NotificationContent(): ReactElement {
  const notifications = useNotificationStore((s) => s.notifications);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const markRead = useNotificationStore((s) => s.markRead);
  const clear = useNotificationStore((s) => s.clear);
  const unreadCount = notifications.filter((n) => !n.read).length;

  return (
    <>
      <div className="flex h-11 items-center justify-between border-b border-rule px-3.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-[12px] font-semibold text-ink">通知</span>
          {notifications.length > 0 && (
            <span className="rounded-full bg-paper-sunk px-1.5 py-0.5 text-[10px] font-medium text-ink-3 tabular-nums">
              {unreadCount > 0 ? `${unreadCount} 未读` : `${notifications.length} 条`}
            </span>
          )}
        </div>
        {notifications.length > 0 && (
          <div className="flex items-center gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  className="grid h-6 w-6 place-items-center rounded-md text-ink-4 transition-colors hover:bg-paper-sunk hover:text-ink-2 disabled:pointer-events-none disabled:opacity-40"
                  disabled={unreadCount === 0}
                  onClick={markAllRead}
                  type="button"
                >
                  <CheckCheck className="h-3.5 w-3.5" strokeWidth={1.5} />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">全部标为已读</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  className="grid h-6 w-6 place-items-center rounded-md text-ink-4 transition-colors hover:bg-red-50 hover:text-red-500"
                  onClick={clear}
                  type="button"
                >
                  <Trash2 className="h-3.5 w-3.5" strokeWidth={1.5} />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">清空通知</TooltipContent>
            </Tooltip>
          </div>
        )}
      </div>
      <div className="max-h-90 overflow-y-auto py-1">
        {notifications.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-10 text-ink-4">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-paper-sunk text-ink-4">
              <Bell className="h-4 w-4" strokeWidth={1.5} />
            </span>
            <span className="text-[11px]">暂无通知</span>
          </div>
        ) : (
          notifications.map((n) => (
            <NotificationRow key={n.id} notification={n} onRead={markRead} />
          ))
        )}
      </div>
    </>
  );
}

export function NotificationDropdown({
  align = 'end',
  side = 'bottom',
  children,
}: {
  align?: 'start' | 'center' | 'end';
  side?: 'top' | 'right' | 'bottom' | 'left';
  children: ReactNode;
}): ReactElement {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      <DropdownMenuContent align={align} side={side} sideOffset={8} className="w-92 overflow-hidden p-0 shadow-panel">
        <NotificationContent />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function NotificationPanel({ collapsed }: { collapsed: boolean }): ReactElement {
  const unreadCount = useNotificationStore((s) => s.unreadCount());

  const btn = (
    <button
      className={cn(
        'relative flex h-9 w-full items-center rounded-md text-[12px] font-medium transition-all duration-150',
        collapsed ? 'justify-center' : 'gap-2.5 px-2.5',
        'text-slate-500 hover:bg-slate-100 hover:text-slate-800',
      )}
      title="通知"
      type="button"
    >
      <Bell className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
      {!collapsed && <span className="flex-1 truncate text-left">通知</span>}
      {unreadCount > 0 && (
        <span className={cn(
          'flex items-center justify-center rounded-full bg-red-500 font-sans text-[9px] font-semibold leading-none text-white',
          collapsed ? 'absolute right-1.5 top-1.5 h-3.5 min-w-3.5 px-0.5' : 'h-4 min-w-4 px-1',
        )}>
          {unreadCount > 99 ? '99+' : unreadCount}
        </span>
      )}
    </button>
  );

  if (collapsed) {
    return (
      <NotificationDropdown side="right">
        <Tooltip>
          <TooltipTrigger asChild>{btn}</TooltipTrigger>
          <TooltipContent side="right">
            通知{unreadCount > 0 ? `（${unreadCount} 条未读）` : ''}
          </TooltipContent>
        </Tooltip>
      </NotificationDropdown>
    );
  }

  return <NotificationDropdown side="right">{btn}</NotificationDropdown>;
}

function NotificationRow({
  notification: n,
  onRead,
}: {
  notification: AppNotification;
  onRead: (id: string) => void;
}): ReactElement {
  const hasBody = n.body !== undefined && n.body.trim() !== '';
  return (
    <button
      className={cn(
        'group relative grid w-full grid-cols-[26px_minmax(0,1fr)] gap-2.5 px-3.5 py-2.5 text-left transition-colors hover:bg-paper-sunk',
        !n.read && 'bg-accent-wash',
      )}
      onClick={() => onRead(n.id)}
      type="button"
    >
      {!n.read && <span className="absolute left-0 top-2.5 h-8 w-0.5 rounded-r-full bg-accent" />}
      <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md bg-white ring-1 ring-rule">
        {KIND_ICON[n.kind]}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center">
          <span className={cn('min-w-0 flex-1 truncate text-[12px] font-medium text-ink', !n.read && 'font-semibold')}>
            {n.title}
          </span>
        </div>
        {hasBody && (
          <p className="mt-1 line-clamp-2 break-all text-[11px] leading-4 text-ink-3">
            {n.body}
          </p>
        )}
        <span className="mt-1.5 block font-mono text-[10px] text-ink-3">{formatNotificationTime(n.at)}</span>
      </div>
    </button>
  );
}

function formatNotificationTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffMs = Date.now() - d.getTime();
  if (diffMs < 60_000) return '刚刚';
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)} 分钟前`;
  if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)} 小时前`;
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
