import { KeyRound, Shield, ShieldCheck, UserCog } from 'lucide-react';
import type { ReactElement } from 'react';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { RefreshButton } from '../ui/refresh-button';
import { Switch } from '../ui/switch';
import { KeyRow, Panel, StatBand, Figure } from './ledger';
import { WorkspaceShell } from './WorkspaceShell';

const permissionRows = [
  { id: 'picker', description: '允许 Electron 拾取器读取当前页面 DOM 与元素坐标。', enabled: true, label: '元素拾取器' },
  { id: 'run', description: '允许从桌面端启动、停止和调试本地运行任务。', enabled: true, label: '运行控制' },
  { id: 'schedule', description: '允许创建与触发调度任务，并访问任务队列。', enabled: true, label: '调度控制' },
  { id: 'artifact', description: '允许读取截图、日志和脚本等产物内容。', enabled: true, label: '产物访问' }
] as const;

export function PermissionsPage({ electron }: { electron: ElectronBridgeState }): ReactElement {
  return (
    <WorkspaceShell
      actions={
        <RefreshButton variant="ledger" onClick={() => electron.loadQueueStats()}>同步状态</RefreshButton>
      }
      description="桌面能力与产物访问边界"
      title="权限中心"
    >
      <StatBand>
        <Figure
          first
          label="桥接状态"
          value={<span className="figure text-[22px]">{electron.available ? '已连接' : '未连接'}</span>}
          note={electron.available ? 'ONLINE' : 'OFFLINE'}
          tone={electron.available ? 'live' : 'ink'}
        />
        <Figure label="主机" value={<span className="figure text-[18px] text-ink-2">{electron.appInfo?.hostname ?? '--'}</span>} note="HOST" />
        <Figure label="平台" value={<span className="figure text-[18px] text-ink-2">{electron.appInfo?.platform ?? '--'}</span>} note="OS" />
      </StatBand>

      <Panel label="权限矩阵" icon={<ShieldCheck className="h-3.5 w-3.5" strokeWidth={1.5} />} bodyClassName="grid gap-2">
        {permissionRows.map((row) => (
          <div className="flex items-center justify-between gap-3 rounded-md border border-rule bg-paper-sunk px-3.5 py-3" key={row.id}>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-ink-4"><ShieldKey id={row.id} /></span>
                <span className="text-[12px] font-medium text-ink">{row.label}</span>
              </div>
              <div className="mt-1 text-[11px] leading-relaxed text-ink-3">{row.description}</div>
            </div>
            <Switch aria-label={row.label} checked={row.enabled} disabled />
          </div>
        ))}
      </Panel>

      <Panel label="执行边界" icon={<KeyRound className="h-3.5 w-3.5" strokeWidth={1.5} />}>
        <KeyRow label="窗口 ID" value={electron.windowId !== null ? String(electron.windowId) : '--'} mono />
        <KeyRow label="应用版本" value={electron.appInfo?.version ?? '--'} mono />
        <KeyRow label="运行中任务" value={electron.queueStats?.activeCount ?? 0} mono />
        <KeyRow label="最后运行 ID" value={electron.lastRunId ?? '--'} mono last />
      </Panel>
    </WorkspaceShell>
  );
}

function ShieldKey({ id }: { id: string }): ReactElement {
  if (id === 'picker') return <UserCog className="h-3.5 w-3.5" strokeWidth={1.5} />;
  if (id === 'run') return <Shield className="h-3.5 w-3.5" strokeWidth={1.5} />;
  if (id === 'schedule') return <ShieldCheck className="h-3.5 w-3.5" strokeWidth={1.5} />;
  return <KeyRound className="h-3.5 w-3.5" strokeWidth={1.5} />;
}
