import { ArrowDownToLine, Cpu, ExternalLink, Loader2, RefreshCw } from 'lucide-react';
import type { ReactElement, ReactNode } from 'react';
import { useEffect, useState } from 'react';
import { cn } from '../../../lib/utils';
import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import type { AppUpdateStatus } from '../../../types/electron';
import { IconButton } from '../../ui/button';
import { SettingsContent } from './SettingsContent';

const UPDATE_ERROR_LABELS: Record<string, string> = {
  'update-server-not-configured': '更新服务器未配置',
  'update-dev-mode': '开发模式下不检查更新',
};

export function SystemInfoPanel({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const [status, setStatus] = useState<AppUpdateStatus>({ status: 'idle' });
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);
  const [restarting, setRestarting] = useState(false);
  const bridge = typeof window !== 'undefined' ? (window.rpaBridge ?? null) : null;

  useEffect(() => {
    if (!bridge?.onUpdateStatus) return;
    return bridge.onUpdateStatus((s) => {
      setStatus(s);
      if (s.status === 'not-available' || s.status === 'available') {
        setCheckedAt(new Date());
      }
    });
  }, [bridge]);

  const check = (): void => { void bridge?.checkForUpdates(); };
  const download = (): void => { void bridge?.downloadUpdate(); };
  const install = (): void => { void bridge?.quitAndInstall(); };

  const isChecking = status.status === 'checking';
  const isDownloading = status.status === 'downloading';
  const isReady = status.status === 'ready';
  const isAvailable = status.status === 'available';
  const restartBackend = async (): Promise<void> => {
    setRestarting(true);
    try {
      await electron.restartBackend();
    } finally {
      setRestarting(false);
    }
  };

  const statusLabel = (() => {
    switch (status.status) {
      case 'idle': return null;
      case 'checking': return <span className="text-amber-800">检查中…</span>;
      case 'available': return <span className="text-indigo-600">发现新版本 v{status.version}</span>;
      case 'not-available': return <span className="text-emerald-700">已是最新版本</span>;
      case 'downloading': return <span className="text-indigo-600">下载中 {status.percent ?? 0}%</span>;
      case 'ready': return <span className="text-emerald-700">v{status.version} 已就绪，重启后安装</span>;
      case 'error': return (
        <span className="text-red-600">
          {UPDATE_ERROR_LABELS[status.error ?? ''] ?? status.error ?? '未知错误'}
        </span>
      );
    }
  })();

  return (
    <SettingsContent
      icon={<Cpu className="h-3.5 w-3.5" strokeWidth={1.5} />}
      title="系统信息"
    >
      <div className="grid max-w-270 gap-5 xl:grid-cols-2">
        <SystemGroup title="基础">
          <InfoRow label="系统平台" value={electron.appInfo?.platform ?? '--'} />
          <InfoRow label="系统架构" value={electron.appInfo?.arch ?? '--'} />
          <InfoRow label="应用版本" value={electron.appInfo?.version ?? '--'} mono />
          <InfoRow label="主机名称" value={electron.appInfo?.hostname ?? '--'} mono />
          <DataDirRow appDataDir={electron.appInfo?.appDataDir ?? '~/.easy-rpa'} electron={electron} />
        </SystemGroup>

        <SystemGroup
          action={
            <IconButton className="h-6 w-6 text-ink-4 hover:text-ink" disabled={restarting} label={restarting ? '正在重启后端服务' : '重启后端服务'} onClick={() => void restartBackend()}>
              <RefreshCw className={cn('h-3.5 w-3.5', restarting && 'animate-spin')} strokeWidth={1.5} />
            </IconButton>
          }
          title="服务"
        >
          <InfoRow label="状态" value={formatBackendStatus(electron.backendStatus)} valueAccent={backendStatusAccent(electron.backendStatus?.status ?? null)} />
          <InfoRow label="来源" value={formatBackendSource(electron.backendStatus)} />
          <InfoRow label="地址" value={electron.backendStatus?.url ?? '--'} mono />
          {electron.backendStatus?.error && (
            <InfoRow label="错误" value={electron.backendStatus.error} valueAccent="text-red-600" wrap />
          )}
        </SystemGroup>

        <SystemGroup
          action={
            <>
              {!isReady && (
                <IconButton className="h-6 w-6 text-ink-4 hover:text-ink" disabled={isChecking || isDownloading} label={isChecking ? '检查中' : '检查更新'} onClick={check}>
                  {isChecking ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.5} />}
                </IconButton>
              )}
              {isAvailable && (
                <IconButton className="h-6 w-6 text-accent-strong hover:text-accent-press" label="下载更新" onClick={download}>
                  <ArrowDownToLine className="h-3.5 w-3.5" strokeWidth={1.5} />
                </IconButton>
              )}
              {isReady && (
                <IconButton className="h-6 w-6 text-accent-strong hover:text-accent-press" label={`重启并安装 v${status.version}`} onClick={install}>
                  <ArrowDownToLine className="h-3.5 w-3.5" strokeWidth={1.5} />
                </IconButton>
              )}
            </>
          }
          title="更新"
        >
          <InfoRow label="当前版本" value={`v${electron.appInfo?.version ?? '…'}`} mono />
          {checkedAt && (
            <InfoRow
              label="检查时间"
              value={checkedAt.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
              mono
            />
          )}
          {statusLabel && (
            <div className="flex min-w-0 items-center gap-2 text-[11px]">
              {(isChecking || isDownloading) && (
                <Loader2 className="h-3 w-3 shrink-0 animate-spin text-ink-4" />
              )}
              {statusLabel}
            </div>
          )}
          {isDownloading && (
            <div className="h-1 w-full overflow-hidden rounded-full bg-rule">
              <div
                className="h-full rounded-full bg-accent transition-[width] duration-300"
                style={{ width: `${status.percent ?? 0}%` }}
              />
            </div>
          )}
        </SystemGroup>

      </div>
    </SettingsContent>
  );
}

function backendStatusAccent(status: string | null): string | undefined {
  if (status === 'ready') return 'text-emerald-700';
  if (status === 'error') return 'text-red-600';
  if (status === 'starting' || status === 'checking' || status === 'installing-browser') return 'text-amber-800';
  return undefined;
}

function formatBackendStatus(value: ElectronBridgeState['backendStatus']): string {
  if (value === null) return '--';
  if (value.status === 'ready') return '已就绪';
  if (value.status === 'starting') return '启动中';
  if (value.status === 'checking') return '探测中';
  if (value.status === 'installing-browser') {
    if (value.installStepLabel !== null && value.installProgress !== null) {
      const stepSuffix = value.installStepTotal !== null ? `（${value.installStep}/${value.installStepTotal}）` : '';
      return `正在下载 ${value.installStepLabel}${stepSuffix} ${value.installProgress}%`;
    }
    return '准备浏览器组件中';
  }
  if (value.status === 'stopped') return '已停止';
  if (value.status === 'error') return '异常';
  return '空闲';
}

function formatBackendSource(value: ElectronBridgeState['backendStatus']): string {
  if (value === null) return '--';
  if (value.source === 'managed') return 'Electron 托管';
  if (value.source === 'external') return '外部服务';
  if (value.source === 'missing') return 'Python 环境缺失';
  return '未知';
}

function SystemGroup({
  action,
  children,
  title,
}: {
  action?: ReactNode;
  children: ReactNode;
  title: string;
}): ReactElement {
  return (
    <section className="grid content-start gap-2 border-t border-rule pt-4 first:border-t-0 first:pt-0 xl:border-t-0 xl:pt-0">
      <div className="flex min-h-6 items-center justify-between gap-3">
        <div className="flex items-center text-[11px] font-medium text-ink-2">
          {title}
        </div>
        {action !== undefined && <div className="flex items-center gap-1">{action}</div>}
      </div>
      <div className="grid gap-1">{children}</div>
    </section>
  );
}

function InfoRow({
  label,
  mono,
  value,
  valueAccent,
  wrap,
}: {
  label: string;
  mono?: boolean;
  value: string;
  valueAccent?: string;
  wrap?: boolean;
}): ReactElement {
  return (
    <div className={cn('flex min-w-0 gap-3 py-1 text-[11px]', wrap ? 'items-start' : 'items-center justify-between')}>
      <span className="shrink-0 text-ink-3">{label}</span>
      <span className={cn(
        'min-w-0 font-medium text-ink-2',
        wrap ? 'flex-1 whitespace-pre-wrap wrap-break-word text-left leading-relaxed' : 'max-w-[68%] truncate text-right',
        mono && 'font-mono text-[11px]',
        valueAccent,
      )}>
        {value}
      </span>
    </div>
  );
}

function DataDirRow({ appDataDir, electron }: { appDataDir: string; electron: ElectronBridgeState }): ReactElement {
  const openDir = async (): Promise<void> => {
    try {
      if (electron.available) {
        const bridge = window.rpaBridge;
        if (!bridge) throw new Error('桌面桥接服务不可用');
        const result = await bridge.openDataDir();
        if (!result.ok) throw new Error(result.error ?? '打开数据目录失败');
        return;
      }
      await navigator.clipboard.writeText(appDataDir);
      electron.pushToast('success', '数据目录已复制');
    } catch (error) {
      electron.pushToast('error', error instanceof Error ? error.message : '数据目录操作失败');
    }
  };

  return (
    <div className="flex min-w-0 items-center justify-between gap-3 py-1 text-[11px]">
      <span className="inline-flex shrink-0 items-center gap-1.5 text-ink-3">
        数据目录
      </span>
      <div className="flex min-w-0 items-center justify-end gap-1">
        <span className="min-w-0 max-w-40 truncate font-mono text-[11px] font-medium text-ink-2" title={appDataDir}>
          {appDataDir}
        </span>
        <IconButton
          className="h-6 w-6 text-ink-4 hover:text-ink"
          label={electron.available ? '打开目录' : '复制到剪贴板'}
          onClick={() => void openDir()}
          variant="ghost"
        >
          <ExternalLink className="h-3 w-3" strokeWidth={1.5} />
        </IconButton>
      </div>
    </div>
  );
}
