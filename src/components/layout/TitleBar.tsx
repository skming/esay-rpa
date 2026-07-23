import { AlertCircle, Bell, CheckCircle2, Loader2, CirclePause, Square } from 'lucide-react';
import type { ReactElement } from 'react';

import { useClock } from '../../hooks/useClock';
import { cn } from '../../lib/utils';
import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { formatElapsedTime } from '../../lib/time';
import { IconButton } from '../ui/button';
import { NotificationDropdown } from './NotificationPanel';
import { useNotificationStore } from '../../stores/useNotificationStore';

export function TitleBar({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const clock = useClock();
  const { runtimeStatus } = electron;
  const running = runtimeStatus === 'running';
  const showBadge = runtimeStatus !== 'ready';

  return (
    <header className="drag-region flex h-9 shrink-0 items-center border-b border-slate-200 bg-white px-3 text-[11px]">
      {/* macOS traffic lights 留空 */}
      <div className="w-17.5 shrink-0" />

      <div className="flex flex-1 items-center justify-end gap-1.5">
        {showBadge && (
          <span className={cn(
            'inline-flex h-5 items-center gap-1 rounded-md border px-2 text-[10px] font-medium tabular-nums',
            STATUS_TONE[runtimeStatus]
          )}>
            <StatusIcon status={runtimeStatus} />
            {STATUS_LABEL[runtimeStatus]}
            {running && <span className="opacity-60">· {formatElapsedTime(electron.progress.elapsedMs)}</span>}
          </span>
        )}
        <NotificationBell />
        {electron.available && electron.windowId !== null && (
          <span className="font-mono text-[9px] text-slate-300">
            W{electron.windowId}
          </span>
        )}
        <span className="ml-1 w-12 text-right font-mono text-[11px] text-slate-500">{clock}</span>
      </div>
    </header>
  );
}

const STATUS_LABEL: Record<ElectronBridgeState['runtimeStatus'], string> = {
  error: '失败', ready: '', running: '运行中', stopped: '已停止', success: '已完成', paused_for_human: '等待接管'
};

const STATUS_TONE: Record<ElectronBridgeState['runtimeStatus'], string> = {
  running: 'border-blue-200 bg-blue-50 text-blue-700',
  success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  error: 'border-red-200 bg-red-50 text-red-700',
  stopped: 'border-slate-200 bg-slate-50 text-slate-500',
  paused_for_human: 'border-amber-200 bg-amber-50 text-amber-700',
  ready: ''
};

function StatusIcon({ status }: { status: ElectronBridgeState['runtimeStatus'] }): ReactElement {
  const cls = 'h-3 w-3';
  const sw = 1.5;
  if (status === 'running') return <Loader2 className={`${cls} animate-spin`} strokeWidth={sw} />;
  if (status === 'success') return <CheckCircle2 className={cls} strokeWidth={sw} />;
  if (status === 'error') return <AlertCircle className={cls} strokeWidth={sw} />;
  if (status === 'stopped') return <CirclePause className={cls} strokeWidth={sw} />;
  if (status === 'paused_for_human') return <Square className={cls} strokeWidth={sw} />;
  return <CheckCircle2 className={cls} strokeWidth={sw} />;
}

function NotificationBell(): ReactElement {
  const unreadCount = useNotificationStore((s) => s.unreadCount());
  return (
    <NotificationDropdown side="bottom" align="end">
      <div className="relative">
        <IconButton className="h-6 w-6 rounded text-slate-400 hover:bg-slate-100 hover:text-slate-700" label="通知">
          <Bell className="h-3.5 w-3.5" strokeWidth={1.5} />
        </IconButton>
        {unreadCount > 0 && (
          <span className="pointer-events-none absolute -right-0.5 -top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-red-500 px-0.5 font-sans text-[8px] font-semibold leading-none text-white">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </div>
    </NotificationDropdown>
  );
}
