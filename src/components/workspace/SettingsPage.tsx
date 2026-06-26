import {
  ArrowDownToLine, Bot, CheckCircle2, Cpu, ExternalLink, Eye, EyeOff,
  FolderOpen, Link2, Loader2, RefreshCw, ServerCog, Trash2,
  Wifi, WifiOff,
} from 'lucide-react';
import type { ReactElement, ReactNode } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useAiChatStore } from '../../stores/useAiChatStore';
import { useFlowDraftStore } from '../../stores/useFlowDraftStore';
import { useBottomPanelStore } from '../../stores/useBottomPanelStore';
import { useWorkspaceStore } from '../../stores/useWorkspaceStore';
import { useRunConfigStore } from '../../stores/useRunConfigStore';

import { DEFAULT_BROWSER_BACKEND_URL } from '../../lib/backendClient';
import type { AiConfig, AppUpdateStatus } from '../../types/electron';
import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import { Button, IconButton } from '../ui/button';
import { Collapsible } from '../ui/collapsible';
import { cn } from '../../lib/utils';

const API = DEFAULT_BROWSER_BACKEND_URL;
type SettingsSection = 'system' | 'ai';

// ─── 模型服务商分组 ──────────────────────────────────────────────────────────
const PROVIDER_GROUPS: {
  key: string;
  label: string;
  env_key: string;
  placeholder: string;
  docsUrl: string;
}[] = [
    { key: 'anthropic', label: 'Anthropic', env_key: 'ANTHROPIC_API_KEY', placeholder: 'sk-ant-api03-…', docsUrl: 'https://console.anthropic.com/settings/keys' },
    { key: 'openai', label: 'OpenAI', env_key: 'OPENAI_API_KEY', placeholder: 'sk-proj-…', docsUrl: 'https://platform.openai.com/api-keys' },
    { key: 'google', label: 'Google Gemini', env_key: 'GEMINI_API_KEY', placeholder: 'AIza…', docsUrl: 'https://aistudio.google.com/app/apikey' },
    { key: 'deepseek', label: 'DeepSeek', env_key: 'DEEPSEEK_API_KEY', placeholder: 'sk-…', docsUrl: 'https://platform.deepseek.com/api_keys' },
    { key: 'qwen', label: '阿里云 Qwen', env_key: 'DASHSCOPE_API_KEY', placeholder: 'sk-…', docsUrl: 'https://dashscope.console.aliyun.com/apiKey' },
    { key: 'zai', label: '智谱 GLM (Z.ai)', env_key: 'ZAI_API_KEY', placeholder: '…', docsUrl: 'https://z.ai/manage-apikey/apikey-list' },
  ];

export function SettingsPage({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const [clearing, setClearing] = useState<'cache' | 'all' | null>(null);
  const [activeSection, setActiveSection] = useState<SettingsSection>('system');

  const clearData = async (scope: 'cache' | 'all'): Promise<void> => {
    setClearing(scope);
    try {
      // 清理所有会话的 AI 聊天缓存，避免旧上下文影响后续配置验证。
      useAiChatStore.setState({ sessions: {} });

      if (scope === 'all') {
        // 重置仅保存在本机的界面状态，后端持久化流程数据不在这里处理。
        useFlowDraftStore.getState().clearDraft();
        useBottomPanelStore.setState({ activeTab: 'logs', height: 188, open: true });
        useWorkspaceStore.setState({ flowQuery: '', navCollapsed: false, selectedFolder: '全部流程', viewMode: 'card' });
        useRunConfigStore.getState().clearLastRunOverrides();
      }

      await new Promise<void>((resolve) => { setTimeout(resolve, 400); });
    } finally {
      setClearing(null);
    }
  };

  return (
    <main className="flex min-h-0 flex-1 flex-col overflow-hidden bg-paper">
      <div className="no-scrollbar min-h-0 flex-1 overflow-auto">
        <div className="p-4">
          <section className="grid min-h-[calc(100vh-64px)] overflow-hidden rounded-xl border border-rule bg-surface shadow-sm lg:grid-cols-[220px_minmax(0,1fr)]">
            <SettingsSideTabs active={activeSection} onChange={setActiveSection} />
            <div className="min-w-0 border-t border-rule lg:border-l lg:border-t-0" role="tabpanel">
              {activeSection === 'system' ? (
                <SystemInfoPanel clearing={clearing} electron={electron} onClear={clearData} />
              ) : (
                <AiModelConfigCard electron={electron} />
              )}
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}

function SettingsSideTabs({
  active,
  onChange,
}: {
  active: SettingsSection;
  onChange: (section: SettingsSection) => void;
}): ReactElement {
  return (
    <aside
      aria-label="设置分类"
      className="bg-paper-sunk/45 p-2"
      role="tablist"
    >
      <div className="grid gap-1">
        <SettingsTabButton
          active={active === 'system'}
          label="系统信息"
          onClick={() => onChange('system')}
        />
        <SettingsTabButton
          active={active === 'ai'}
          label="AI 模型配置"
          onClick={() => onChange('ai')}
        />
      </div>
    </aside>
  );
}

function SettingsTabButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}): ReactElement {
  return (
    <button
      aria-selected={active}
      className={cn(
        'flex h-9 w-full items-center rounded-md px-3 text-left text-[12px] font-medium transition-colors duration-150',
        active
          ? 'bg-surface text-ink shadow-xs ring-1 ring-rule'
          : 'text-ink-3 hover:bg-surface/70 hover:text-ink-2',
      )}
      onClick={onClick}
      role="tab"
      type="button"
    >
      {label}
    </button>
  );
}

// ─── AI 模型配置卡片 ─────────────────────────────────────────────────────────

type TestStatus = 'idle' | 'testing' | 'ok' | 'fail';
type TestResult = { status: TestStatus; latencyMs?: number; error?: string };

function AiModelConfigCard({ electron }: { electron: ElectronBridgeState }): ReactElement {
  const [config, setConfig] = useState<AiConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [draftKeys, setDraftKeys] = useState<Record<string, string>>({});
  const [draftBaseUrls, setDraftBaseUrls] = useState<Record<string, string>>({});
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const testTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const bridge = typeof window !== 'undefined' ? (window.rpaBridge ?? null) : null;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const cfgRes = electron.available && bridge
        ? await bridge.getAiConfig()
        : await fetch(`${API}/api/ai/config`).then(r => r.json());
      const cfg = (electron.available && bridge ? (cfgRes as Awaited<ReturnType<typeof bridge.getAiConfig>>).data : cfgRes) as AiConfig | undefined;
      if (cfg) {
        setConfig(cfg);
        setDraftBaseUrls(cfg.base_urls ?? {});
      }
    } catch { /* 配置读取失败不阻断设置页渲染，用户仍可通过保存操作重试。 */ } finally {
      setLoading(false);
    }
  }, [bridge, electron.available]);

  useEffect(() => { void load(); }, [load]);

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    try {
      const payload = {
        api_keys: Object.fromEntries(Object.entries(draftKeys).filter(([, v]) => v !== '')),
        base_urls: draftBaseUrls,
      };
      let updated: AiConfig | undefined;
      if (electron.available && bridge) {
        const res = await bridge.setAiConfig(payload);
        updated = res.data;
      } else {
        const res = await fetch(`${API}/api/ai/config`, {
          method: 'PUT',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(payload),
        });
        updated = (await res.json()) as AiConfig;
      }
      if (updated) {
        setConfig(updated);
        setDraftKeys({});
      }
      setSaved(true);
      if (savedTimer.current) clearTimeout(savedTimer.current);
      savedTimer.current = setTimeout(() => setSaved(false), 2500);
    } catch {
      electron.pushToast('error', 'AI 配置保存失败，请检查后端服务');
    } finally {
      setSaving(false);
    }
  };

  const handleTestKey = async (envKey: string): Promise<void> => {
    setTestResults(prev => ({ ...prev, [envKey]: { status: 'testing' } }));
    if (testTimers.current[envKey]) clearTimeout(testTimers.current[envKey]);
    try {
      const res = await fetch(`${API}/api/ai/test-model`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          env_key: envKey,
          api_key: draftKeys[envKey] ?? config?.api_keys[envKey] ?? '',
          base_url: draftBaseUrls[envKey] ?? '',
        }),
      });
      const data = (await res.json()) as { ok: boolean; latency_ms?: number; error?: string };
      const result: TestResult = data.ok
        ? { status: 'ok', latencyMs: data.latency_ms }
        : { status: 'fail', error: data.error };
      setTestResults(prev => ({ ...prev, [envKey]: result }));
      void load();
      testTimers.current[envKey] = setTimeout(
        () => setTestResults(prev => ({ ...prev, [envKey]: { status: 'idle' } })),
        5000
      );
    } catch (err) {
      setTestResults(prev => ({ ...prev, [envKey]: { status: 'fail', error: String(err) } }));
      testTimers.current[envKey] = setTimeout(
        () => setTestResults(prev => ({ ...prev, [envKey]: { status: 'idle' } })),
        5000
      );
    }
  };

  const configuredCount = PROVIDER_GROUPS.filter(g => {
    const draft = draftKeys[g.env_key];
    return draft !== undefined ? draft !== '' : !!(config?.api_keys[g.env_key]);
  }).length;

  const getTestResult = (envKey: string): TestResult =>
    testResults[envKey] ?? { status: 'idle' };

  return (
    <SettingsContent
      action={
        loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-ink-4" />
        ) : (
          <span className="font-mono text-[10px] tabular-nums text-ink-4">
            已配置 {configuredCount}/{PROVIDER_GROUPS.length}
          </span>
        )
      }
      icon={<Bot className="h-3.5 w-3.5" strokeWidth={1.5} />}
      title="AI 模型配置"
    >
      <div className="grid gap-2">
        <div className="grid gap-2">
          {PROVIDER_GROUPS.map(g => {
            const storedValue = config?.api_keys[g.env_key] ?? '';
            const draftValue = draftKeys[g.env_key];
            const displayValue = draftValue ?? storedValue;
            const isConfigured = storedValue !== '' || (draftValue !== undefined && draftValue !== '');
            const isVisible = showKeys[g.env_key] ?? false;
            const tr = getTestResult(g.env_key);
            const ts = tr.status;

            const badge = (
              <span className={cn(
                'flex h-4 items-center gap-1 rounded px-1.5 text-[9px] font-medium',
                isConfigured ? 'bg-emerald-50 text-emerald-700' : 'border border-rule bg-paper-sunk text-ink-4',
              )}>
                {isConfigured
                  ? <><CheckCircle2 className="h-2.5 w-2.5" strokeWidth={2} />已配置</>
                  : '未配置'
                }
              </span>
            );

            return (
              <Collapsible
                badge={badge}
                className="rounded-md border-rule bg-surface"
                defaultOpen={isConfigured}
                key={g.env_key}
                title={g.label}
              >
                <div className="grid gap-2">
                  <div className="flex items-center gap-2">
                    <div className="relative flex-1">
                      <input
                        className="h-7 w-full rounded-md border border-rule-2 bg-paper-sunk px-2.5 pr-9 font-mono text-[10px] text-ink-2 placeholder:text-ink-4 focus:border-ink-3 focus:bg-surface focus:outline-none focus:ring-2 focus:ring-rule"
                        placeholder={g.placeholder}
                        type={isVisible ? 'text' : 'password'}
                        value={displayValue}
                        onChange={e => setDraftKeys(prev => ({ ...prev, [g.env_key]: e.target.value }))}
                      />
                      <div className="absolute right-1 top-0 flex h-full items-center gap-0.5">
                        <button
                          className="flex h-5 w-5 items-center justify-center rounded text-slate-400 hover:text-slate-600"
                          onClick={() => setShowKeys(prev => ({ ...prev, [g.env_key]: !prev[g.env_key] }))}
                          title={isVisible ? '隐藏' : '显示'}
                          type="button"
                        >
                          {isVisible ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                        </button>
                      </div>
                    </div>

                    <button
                      className="flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-rule-2 bg-surface px-2.5 text-[10px] text-ink-3 transition-colors duration-150 hover:border-ink-3 hover:text-ink disabled:opacity-50"
                      disabled={ts === 'testing' || (!isConfigured && !draftValue)}
                      onClick={() => void handleTestKey(g.env_key)}
                      title={tr.error ?? '发送 1-token 请求验证连接'}
                      type="button"
                    >
                      {ts === 'testing' && <Loader2 className="h-3 w-3 animate-spin" />}
                      {ts === 'ok' && <Wifi className="h-3 w-3 text-emerald-500" />}
                      {ts === 'fail' && <WifiOff className="h-3 w-3 text-red-500" />}
                      {ts === 'idle' && <Wifi className="h-3 w-3" />}
                      <span>
                        {ts === 'testing' ? '测试中…' :
                          ts === 'ok' ? `${tr.latencyMs ?? ''}ms` :
                            ts === 'fail' ? '失败' : '测试'}
                      </span>
                    </button>
                  </div>

                  {/* 测试错误内联 */}
                  {ts === 'fail' && tr.error && (
                    <p className="rounded-md bg-red-50/70 px-2.5 py-1.5 text-[10px] leading-snug text-red-600">
                      {tr.error}
                    </p>
                  )}

                  <div className="grid gap-1.5">
                    <label className="flex items-center gap-1.5 text-[10px] text-slate-500">
                      <Link2 className="h-3 w-3 shrink-0" strokeWidth={1.5} />
                      可选
                      <span className="text-slate-400">（留空使用默认地址）</span>
                    </label>
                    <input
                      className="h-7 w-full rounded-md border border-rule-2 bg-paper-sunk px-2.5 font-mono text-[10px] text-ink-2 placeholder:text-ink-4 focus:border-ink-3 focus:bg-surface focus:outline-none focus:ring-2 focus:ring-rule"
                      placeholder="https://your-relay.example.com/v1"
                      type="text"
                      value={draftBaseUrls[g.env_key] ?? ''}
                      onChange={e => setDraftBaseUrls(prev => ({ ...prev, [g.env_key]: e.target.value }))}
                    />
                  </div>

                  <a
                    className="inline-flex items-center gap-1 text-[10px] text-ink-2 underline decoration-rule-2 underline-offset-2 hover:decoration-ink-3"
                    href={g.docsUrl}
                    rel="noreferrer"
                    target="_blank"
                  >
                    <ExternalLink className="h-2.5 w-2.5" />
                    获取 {g.label} API Key
                  </a>
                </div>
              </Collapsible>
            );
          })}
        </div>
      </div>

      <div className="flex items-center justify-end gap-3 pt-3">
        {saved && (
          <span className="flex items-center gap-1.5 text-[11px] text-emerald-700">
            <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={2} />
            已保存
          </span>
        )}
        <Button
          className="h-8 rounded-md px-4 text-[11px]"
          disabled={saving || loading}
          onClick={() => void handleSave()}
          variant="primary"
        >
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {saving ? '保存中…' : '保存配置'}
        </Button>
      </div>
    </SettingsContent>
  );
}

const UPDATE_ERROR_LABELS: Record<string, string> = {
  'update-server-not-configured': '更新服务器未配置',
  'update-dev-mode': '开发模式下不检查更新',
};

function SystemInfoPanel({
  clearing,
  electron,
  onClear,
}: {
  clearing: 'cache' | 'all' | null;
  electron: ElectronBridgeState;
  onClear: (scope: 'cache' | 'all') => Promise<void>;
}): ReactElement {
  const [status, setStatus] = useState<AppUpdateStatus>({ status: 'idle' });
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);
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

  const statusLabel = (() => {
    switch (status.status) {
      case 'idle': return null;
      case 'checking': return <span className="text-amber-600">检查中…</span>;
      case 'available': return <span className="text-indigo-600">发现新版本 v{status.version}</span>;
      case 'not-available': return <span className="text-emerald-600">已是最新版本</span>;
      case 'downloading': return <span className="text-indigo-600">下载中 {status.percent ?? 0}%</span>;
      case 'ready': return <span className="text-emerald-600">v{status.version} 已就绪，重启后安装</span>;
      case 'error': return (
        <span className="text-red-600">
          {UPDATE_ERROR_LABELS[status.error ?? ''] ?? status.error ?? '未知错误'}
        </span>
      );
    }
  })();

  return (
    <SettingsContent
      action={<BackendStatusDot status={electron.backendStatus?.status ?? null} />}
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
            <IconButton className="h-6 w-6 text-ink-4 hover:text-ink" label="重启后端服务" onClick={() => electron.restartBackend()}>
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.5} />
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
                className="h-full rounded-full bg-accent transition-all duration-300"
                style={{ width: `${status.percent ?? 0}%` }}
              />
            </div>
          )}
        </SystemGroup>

        <SystemGroup title="本地数据">
          <div className="grid gap-x-8 gap-y-1 sm:grid-cols-2">
            <LocalDataAction
              action={
                <IconButton
                  className="h-6 w-6 text-ink-4 hover:text-ink"
                  disabled={clearing !== null}
                  label={clearing === 'cache' ? '清除中' : '清除缓存'}
                  onClick={() => void onClear('cache')}
                >
                  <Trash2 className="h-3.5 w-3.5" strokeWidth={1.5} />
                </IconButton>
              }
              description="不影响已保存流程"
              title="缓存"
            />
            <LocalDataAction
              action={
                <IconButton
                  className="h-6 w-6 text-red-500 hover:bg-red-50 hover:text-red-600"
                  disabled={clearing !== null}
                  label={clearing === 'all' ? '重置中' : '重置全部本地数据'}
                  onClick={() => void onClear('all')}
                >
                  <Trash2 className="h-3.5 w-3.5" strokeWidth={1.5} />
                </IconButton>
              }
              description="清除草稿与偏好"
              title="本地状态"
            />
          </div>
        </SystemGroup>
      </div>
    </SettingsContent>
  );
}

// ─── 共享子组件 ──────────────────────────────────────────────────────────────

function SettingsContent({
  action,
  children,
  icon,
  title,
}: {
  action?: ReactNode;
  children: ReactNode;
  icon: ReactElement;
  title: string;
}): ReactElement {
  return (
    <section className="min-h-full">
      <header className="flex h-12 items-center justify-between border-b border-rule px-5">
        <div className="flex items-center gap-2 text-ink-3">
          {icon}
          <span className="text-[12px] font-semibold text-ink-2">{title}</span>
        </div>
        {action}
      </header>
      <div className="p-5">{children}</div>
    </section>
  );
}

function backendStatusAccent(status: string | null): string | undefined {
  if (status === 'ready') return 'text-emerald-600';
  if (status === 'error') return 'text-red-600';
  if (status === 'starting' || status === 'checking') return 'text-amber-600';
  return undefined;
}

function formatBackendStatus(value: ElectronBridgeState['backendStatus']): string {
  if (value === null) return '--';
  if (value.status === 'ready') return '已就绪';
  if (value.status === 'starting') return '启动中';
  if (value.status === 'checking') return '探测中';
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

function BackendStatusDot({ status }: { status: string | null }): ReactElement {
  const map: Record<string, string> = {
    ready: 'bg-emerald-500',
    starting: 'bg-amber-400',
    checking: 'bg-amber-400',
    error: 'bg-red-500',
    stopped: 'bg-slate-300',
  };
  const color = status !== null ? (map[status] ?? 'bg-slate-300') : 'bg-slate-200';
  return (
    <span className="relative flex h-2 w-2 shrink-0">
      {(status === 'starting' || status === 'checking') && (
        <span className={cn('absolute inline-flex h-full w-full animate-ping rounded-full opacity-60', color)} />
      )}
      <span className={cn('relative inline-flex h-2 w-2 rounded-full', color)} />
    </span>
  );
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
        mono && 'font-mono text-[10px]',
        valueAccent,
      )}>
        {value}
      </span>
    </div>
  );
}

function LocalDataAction({
  action,
  description,
  title,
}: {
  action: ReactElement;
  description: string;
  title: string;
}): ReactElement {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 py-1">
      <span className="min-w-0">
        <span className="block text-[11px] font-medium text-ink-2">{title}</span>
        <span className="block truncate text-[10px] text-ink-4">{description}</span>
      </span>
      {action}
    </div>
  );
}

function DataDirRow({ appDataDir, electron }: { appDataDir: string; electron: ElectronBridgeState }): ReactElement {
  const openDir = (): void => {
    void (electron.available
      ? (window.rpaBridge?.openDataDir() ?? Promise.resolve())
      : navigator.clipboard.writeText(appDataDir)
    );
  };

  return (
    <div className="flex min-w-0 items-center justify-between gap-3 py-1 text-[11px]">
      <span className="inline-flex shrink-0 items-center gap-1.5 text-ink-3">
        <FolderOpen className="h-3 w-3 text-ink-4" strokeWidth={1.5} />
        数据目录
      </span>
      <div className="flex min-w-0 items-center justify-end gap-2">
        <span className="min-w-0 max-w-40 truncate font-mono text-[10px] font-medium text-ink-2" title={appDataDir}>
          {appDataDir}
        </span>
        <IconButton
          className="h-6 px-2 text-[10px] text-ink-2 hover:text-ink"
          label={electron.available ? '打开目录' : '复制到剪贴板'}
          onClick={openDir}
          variant="ghost"
        >
          <ExternalLink className="h-3 w-3" strokeWidth={1.5} />
        </IconButton>
      </div>
    </div>
  );
}
