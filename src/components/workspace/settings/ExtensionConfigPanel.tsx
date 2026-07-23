import { Check, Copy, ExternalLink, FolderOpen, Loader2, Puzzle } from 'lucide-react';
import type { ReactElement } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { backend, type ExtensionStatus } from '../../../lib/backendClient';
import type { ElectronBridgeState } from '../../../hooks/useElectronBridge';
import type { ExtensionInstallInfo } from '../../../types/electron';
import { cn } from '../../../lib/utils';
import { Button } from '../../ui/button';
import { Switch } from '../../ui/switch';
import { SettingsContent } from './SettingsContent';

const POLL_INTERVAL_MS = 3000;

function formatConnectedDuration(connectedSince: string | null): string | null {
  if (connectedSince === null) {
    return null;
  }
  const startedAt = new Date(connectedSince).getTime();
  if (Number.isNaN(startedAt)) {
    return null;
  }
  const elapsedMinutes = Math.max(0, Math.floor((Date.now() - startedAt) / 60_000));
  if (elapsedMinutes < 1) {
    return '刚刚连接';
  }
  if (elapsedMinutes < 60) {
    return `已连接 ${elapsedMinutes} 分钟`;
  }
  const hours = Math.floor(elapsedMinutes / 60);
  return `已连接 ${hours} 小时${elapsedMinutes % 60}分钟`;
}

export function ExtensionConfigPanel({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const [status, setStatus] = useState<ExtensionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingEnabled, setSavingEnabled] = useState(false);
  const [installInfo, setInstallInfo] = useState<ExtensionInstallInfo | null>(null);
  const [openingFolder, setOpeningFolder] = useState(false);
  const [openingChromePage, setOpeningChromePage] = useState(false);
  const [pathCopied, setPathCopied] = useState(false);
  const wasConnectedRef = useRef(false);

  const bridge = typeof window !== 'undefined' ? (window.rpaBridge ?? null) : null;

  const load = useCallback(async () => {
    try {
      setStatus(await backend.getExtensionStatus());
    } catch {
      // 拉取失败时保留上一次的状态展示，用户仍可重试保存操作触发下一次拉取。
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // 误报：load 里 setState 全在 await 之后，规则只看回调体内有无 setState，不区分 await 边界
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    const interval = setInterval(() => { void load(); }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [load]);

  useEffect(() => {
    const connectedNow = status?.connected ?? false;
    if (connectedNow && !wasConnectedRef.current) {
      electron.pushToast('success', '浏览器扩展已连接');
    }
    wasConnectedRef.current = connectedNow;
  }, [status?.connected, electron]);

  useEffect(() => {
    if (bridge === null) return;
    bridge.getExtensionInstallInfo()
      .then((result) => {
        if (result.ok && result.data) setInstallInfo(result.data);
      })
      .catch(() => {
        // 找不到安装信息时保持安装引导区展示，让用户手动排查。
      });
  }, [bridge]);

  const handleToggleEnabled = async (checked: boolean): Promise<void> => {
    setSavingEnabled(true);
    try {
      await backend.setExtensionConfig({ enabled: checked });
      await load();
      electron.pushToast('success', checked ? '已启用浏览器插件执行器' : '已关闭浏览器插件执行器');
    } catch {
      electron.pushToast('error', '保存失败，请检查后端服务');
    } finally {
      setSavingEnabled(false);
    }
  };

  const handleOpenFolder = async (): Promise<void> => {
    if (bridge === null) return;
    setOpeningFolder(true);
    try {
      const result = await bridge.openExtensionFolder();
      if (!result.ok) {
        electron.pushToast('error', result.error ?? '打开文件夹失败');
      }
    } finally {
      setOpeningFolder(false);
    }
  };

  const handleCopyPath = async (): Promise<void> => {
    if (installInfo?.unpackedDir == null) return;
    try {
      await navigator.clipboard.writeText(installInfo.unpackedDir);
      setPathCopied(true);
      setTimeout(() => setPathCopied(false), 2000);
    } catch {
      electron.pushToast('error', '复制失败，请手动选中路径');
    }
  };

  const handleOpenChromeExtensionsPage = async (): Promise<void> => {
    if (bridge === null) return;
    setOpeningChromePage(true);
    try {
      const result = await bridge.openChromeExtensionsPage();
      if (result.ok && !result.data?.opened) {
        electron.pushToast('error', '未能自动打开，请手动在 Chrome 地址栏输入 chrome://extensions/');
      } else if (!result.ok) {
        electron.pushToast('error', result.error ?? '打开扩展管理页面失败');
      }
    } finally {
      setOpeningChromePage(false);
    }
  };

  const connected = status?.connected ?? false;
  const enabled = status?.enabled ?? true;
  const connectedDuration = formatConnectedDuration(status?.connectedSince ?? null);
  const showInstallAssist = !connected || !enabled;

  return (
    <SettingsContent
      action={loading ? <Loader2 className="h-3.5 w-3.5 animate-spin text-ink-4" /> : null}
      icon={<Puzzle className="h-3.5 w-3.5" strokeWidth={1.5} />}
      title="浏览器扩展"
    >
      <div className="grid w-full max-w-300 gap-4">
        <div className="rounded-lg border border-rule-2 bg-paper-sunk/60 p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className={cn('h-2 w-2 shrink-0 rounded-full', connected ? 'bg-live' : 'bg-ink-4')} />
              <span className="text-[12px] font-medium text-ink-2">
                {connected ? '已连接到浏览器扩展' : '未连接'}
              </span>
            </div>
            <Switch
              aria-label="启用浏览器插件执行器"
              checked={enabled}
              disabled={savingEnabled}
              onCheckedChange={(checked) => void handleToggleEnabled(checked)}
            />
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-ink-3">
            {enabled
              ? '流程运行时可选择浏览器插件执行。'
              : '已关闭：运行配置中将无法选择「使用浏览器插件执行」，即使有扩展连接也不会被使用。'}
          </p>
          {connected && connectedDuration !== null && (
            <p className="mt-1.5 text-[11px] text-ink-3">{connectedDuration}</p>
          )}
        </div>

        {showInstallAssist && (
          <div className="rounded-lg border border-rule-2 p-4">
            <p className="text-[11px] font-medium text-ink-2">
              {installInfo?.found === false ? '尚未找到扩展安装包' : '还没有连接？帮你安装'}
            </p>

            {installInfo?.found !== false && installInfo?.unpackedDir != null && (
              <div className="mt-2 flex items-center gap-1.5 rounded-md bg-paper-sunk px-2 py-1.5">
                <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink-3">{installInfo.unpackedDir}</code>
                <button
                  className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-ink-3 hover:bg-paper hover:text-ink-2"
                  onClick={() => void handleCopyPath()}
                  type="button"
                >
                  {pathCopied ? <Check className="h-3 w-3 text-live" /> : <Copy className="h-3 w-3" />}
                  {pathCopied ? '已复制' : '复制路径'}
                </button>
              </div>
            )}

            <ol className="mt-3 grid gap-1.5 text-[11px] leading-relaxed text-ink-3">
              <li>1. 打开 Chrome 扩展管理页面，开启右上角「开发者模式」。</li>
              <li>2. 点击「加载已解压的扩展程序」，粘贴上方路径（或用「在文件夹中显示」定位）。</li>
              <li>3. 点击工具栏上的插件图标，确认弹窗显示"已连接到 Easy RPA"，并保持该浏览器窗口打开。</li>
            </ol>
            {bridge !== null ? (
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  disabled={openingChromePage}
                  onClick={() => void handleOpenChromeExtensionsPage()}
                  size="sm"
                  variant="secondary"
                >
                  {openingChromePage ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ExternalLink className="h-3.5 w-3.5" />}
                  打开 Chrome 扩展管理页面
                </Button>
                <Button
                  disabled={openingFolder || installInfo?.found === false}
                  onClick={() => void handleOpenFolder()}
                  size="sm"
                  variant="ghost"
                >
                  {openingFolder ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FolderOpen className="h-3.5 w-3.5" />}
                  在文件夹中显示
                </Button>
              </div>
            ) : (
              <p className="mt-3 text-[11px] text-ink-3">在浏览器网页模式下无法自动打开文件夹，请在桌面客户端中操作，或手动在 Chrome 地址栏输入 chrome://extensions/。</p>
            )}
            {installInfo?.found === false && (
              <p className="mt-2 text-[11px] text-ink-3">未在应用目录中找到扩展构建产物，请先在 extension/ 目录执行构建。</p>
            )}
          </div>
        )}
      </div>
    </SettingsContent>
  );
}
