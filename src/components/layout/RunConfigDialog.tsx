import { CheckCircle2, Circle, Layers3, Play, Puzzle, Route, SquareMousePointer, Workflow } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ROUTE_PATHS } from '../../app/routeConfig';
import type { BrowserExecutorKind, RunFailureStrategy, RunScope } from '../../types/electron';
import type { StartRunOptions } from '../../hooks/useElectronBridgeActions';
import type { RpaNodeAction, RuntimeVariable } from '../../types/rpa';
import { buildEffectiveRunConfigSummary, normalizeRunTimeoutMs } from '../../lib/runConfigPresentation';
import { backend } from '../../lib/backendClient';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { Switch } from '../ui/switch';
import { cn } from '../../lib/utils';

type RunConfigDraft = {
  browserExecutor: BrowserExecutorKind;
  concurrency: number;
  failureStrategy: RunFailureStrategy;
  overrides: Record<string, string>;
  scope: RunScope;
  screenshot: boolean;
  timeoutMs: number;
};

const EXTENSION_STATUS_POLL_MS = 3000; // 弹框打开期间轮询，让用户手动开启插件后无需重开弹框即可看到状态更新

type ExtensionAvailability = {
  connected: boolean;
  enabled: boolean;
};

/** 弹框打开时轮询插件桥接状态，供下方开关在插件未连接时提前警示 */
function useExtensionAvailability(open: boolean): ExtensionAvailability {
  const [availability, setAvailability] = useState<ExtensionAvailability>({ connected: false, enabled: true });

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    const poll = async (): Promise<void> => {
      try {
        const data = await backend.getExtensionStatus();
        if (!cancelled) {
          setAvailability({ connected: data.connected === true, enabled: data.enabled !== false });
        }
      } catch {
        if (!cancelled) {
          setAvailability({ connected: false, enabled: true });
        }
      }
    };
    void poll();
    const interval = setInterval(poll, EXTENSION_STATUS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [open]);

  return availability;
}

const scopeOptions = [
  { icon: Workflow, label: '完整运行', value: 'full' },
  { icon: Route, label: '从选中步骤运行', value: 'from-selection' },
  { icon: SquareMousePointer, label: '仅运行选中步骤', value: 'selected-only' }
] satisfies Array<{ icon: typeof Workflow; label: string; value: RunScope }>;

export function RunConfigDialog({
  defaultBrowserExecutor = 'playwright',
  inputVariables,
  onOpenChange,
  onSetDefaultBrowserExecutor,
  onStart,
  open,
  running,
  selectedNodeAction,
  selectedNodeId,
  selectedNodeTitle
}: {
  defaultBrowserExecutor?: BrowserExecutorKind;
  inputVariables: RuntimeVariable[];
  onOpenChange: (open: boolean) => void;
  onSetDefaultBrowserExecutor?: (browserExecutor: BrowserExecutorKind) => void;
  onStart: (options: StartRunOptions) => void;
  open: boolean;
  running: boolean;
  selectedNodeAction?: RpaNodeAction;
  selectedNodeId: string;
  selectedNodeTitle: string;
}): ReactElement {
  const [draft, setDraft] = useState<RunConfigDraft>({
    browserExecutor: defaultBrowserExecutor,
    concurrency: 1,
    failureStrategy: 'stop',
    overrides: {},
    scope: 'full',
    screenshot: true,
    timeoutMs: 30_000
  });
  const navigate = useNavigate();
  // 仅在打开瞬间重置，避免覆盖正在编辑的草稿；放渲染期而非 effect，后者会先绘一帧旧执行器
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setDraft((current) => ({ ...current, browserExecutor: defaultBrowserExecutor }));
    }
  }
  const extensionAvailability = useExtensionAvailability(open);
  const extensionConnected = extensionAvailability.connected;
  const extensionEnabled = extensionAvailability.enabled;
  const selectedScopeNeedsNode = draft.scope !== 'full';
  const selectedNodeRunnable = selectedNodeId !== 'start' && selectedNodeId !== 'end';
  const startDisabled = running || (selectedScopeNeedsNode && !selectedNodeRunnable);
  const overrideVariables = useMemo(
    () =>
      inputVariables
        .map((variable) => ({
          ...variable,
          value: draft.overrides[variable.name] ?? variable.value
        }))
        .filter((variable) => draft.overrides[variable.name] !== undefined),
    [draft.overrides, inputVariables]
  );
  const summary = useMemo(
    () =>
      buildEffectiveRunConfigSummary({
        failureStrategy: draft.failureStrategy,
        overrideCount: overrideVariables.length,
        scope: draft.scope,
        screenshot: draft.screenshot,
        selectedNodeAction,
        selectedNodeId,
        selectedNodeTitle,
        timeoutMs: draft.timeoutMs
      }),
    [draft.failureStrategy, draft.scope, draft.screenshot, draft.timeoutMs, overrideVariables.length, selectedNodeAction, selectedNodeId, selectedNodeTitle]
  );

  const updateDraft = <K extends keyof RunConfigDraft>(key: K, value: RunConfigDraft[K]): void => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const updateOverride = (name: string, value: string): void => {
    setDraft((current) => ({
      ...current,
      overrides: value === '' ? omitKey(current.overrides, name) : { ...current.overrides, [name]: value }
    }));
  };

  const handleStart = (): void => {
    if (startDisabled) {
      return;
    }
    onStart({
      browserExecutor: extensionEnabled ? draft.browserExecutor : 'playwright',
      concurrency: draft.concurrency,
      failureStrategy: draft.failureStrategy,
      mode: 'run',
      overrideVariables,
      scope: draft.scope,
      screenshot: draft.screenshot,
      startNodeId: draft.scope === 'full' ? undefined : selectedNodeId,
      timeoutMs: draft.timeoutMs
    });
    onOpenChange(false);
  };

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>运行配置</DialogTitle>
          <DialogDescription>当前目标：{selectedNodeRunnable ? selectedNodeTitle : '完整流程'}</DialogDescription>
        </DialogHeader>

        <DialogBody className="grid gap-4">
          <div className="grid gap-2">
            {scopeOptions.map((option) => {
              const Icon = option.icon;
              const active = draft.scope === option.value;
              const disabled = option.value !== 'full' && !selectedNodeRunnable;
              return (
                <Button
                  className={cn(
                    'h-10 justify-start gap-2 rounded-lg border px-3 text-left text-[12px]',
                    active ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-slate-200 bg-white text-slate-600',
                    disabled && 'opacity-45'
                  )}
                  disabled={disabled}
                  key={option.value}
                  onClick={() => updateDraft('scope', option.value)}
                  variant="outline"
                >
                  {active ? <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={1.5} /> : <Circle className="h-3.5 w-3.5 text-slate-300" strokeWidth={1.5} />}
                  <Icon className="h-3.5 w-3.5" strokeWidth={1.5} />
                  <span className="flex-1">{option.label}</span>
                  {option.value !== 'full' && <span className="max-w-32 truncate font-mono text-[10px] text-slate-500">{selectedNodeTitle}</span>}
                </Button>
              );
            })}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Label className="block">
              <span className="mb-1 block">并发实例数</span>
              <Input
                className="font-mono"
                max={20}
                min={1}
                onChange={(event) => updateDraft('concurrency', clampConcurrency(Number.parseInt(event.target.value, 10)))}
                type="number"
                value={String(draft.concurrency)}
              />
            </Label>
            <Label className="block">
              <span className="mb-1 block">失败时</span>
              <Select onValueChange={(value) => updateDraft('failureStrategy', value as RunFailureStrategy)} value={draft.failureStrategy}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="stop">停止运行</SelectItem>
                  <SelectItem value="continue">继续执行</SelectItem>
                  <SelectItem value="retry">重试当前步骤</SelectItem>
                </SelectContent>
              </Select>
            </Label>
          </div>

          <Label className="block">
            <span className="mb-1 block">默认超时（毫秒）</span>
            <Input
              className="font-mono"
              max={300000}
              min={1000}
              onChange={(event) => updateDraft('timeoutMs', normalizeRunTimeoutMs(Number.parseInt(event.target.value, 10)))}
              type="number"
              value={String(draft.timeoutMs)}
            />
            <span className="mt-1 block text-[10px] text-slate-500">当节点未单独配置 timeoutMs 时使用该值；节点内已配置超时优先。</span>
          </Label>

          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] font-medium text-slate-700">最终生效配置</span>
              <Badge variant={summary.effectiveTimeoutSource === 'node' ? 'amber' : 'blue'}>
                {summary.effectiveTimeoutSource === 'node' ? '节点优先' : '默认超时'}
              </Badge>
            </div>
            <div className="mt-2 grid gap-1.5 text-[11px] text-slate-600">
              <div className="flex items-center justify-between gap-3">
                <span>运行范围</span>
                <span className="font-medium text-slate-800">{summary.scopeLabel}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span>执行目标</span>
                <span className="truncate font-medium text-slate-800">{summary.targetLabel}</span>
              </div>
              {summary.startNodeLabel !== undefined && (
                <div className="flex items-center justify-between gap-3">
                  <span>运行起点</span>
                  <span className="truncate font-mono text-[10px] text-slate-500">{summary.startNodeLabel}</span>
                </div>
              )}
              <div className="flex items-center justify-between gap-3">
                <span>失败策略</span>
                <span className="font-medium text-slate-800">{summary.failureLabel}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span>截图记录</span>
                <span className="font-medium text-slate-800">{summary.screenshotLabel}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span>变量覆写</span>
                <span className="font-medium text-slate-800">{summary.overrideCount} 项</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span>默认超时</span>
                <span className="font-mono text-[10px] text-slate-500">{summary.defaultTimeoutMs} ms</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span>当前生效超时</span>
                <span className="font-mono text-[10px] text-slate-700">
                  {summary.effectiveTimeoutMs} ms{summary.effectiveTimeoutSource === 'node' ? ' · 节点配置' : ' · 运行配置'}
                </span>
              </div>
            </div>
          </div>

          <div className="flex h-9 items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 text-[12px] text-slate-600">
            <span className="inline-flex items-center gap-2">
              <Layers3 className="h-3.5 w-3.5 text-blue-500" strokeWidth={1.5} />
              截图记录
            </span>
            <Switch aria-label="截图记录" checked={draft.screenshot} onCheckedChange={(checked) => updateDraft('screenshot', checked)} />
          </div>

          <div className="flex h-9 items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 text-[12px] text-slate-600">
            <span className="inline-flex items-center gap-2">
              <Puzzle className="h-3.5 w-3.5 text-blue-500" strokeWidth={1.5} />
              使用浏览器插件执行
              <span
                className={cn(
                  'inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                  !extensionEnabled ? 'bg-slate-200 text-slate-700' : extensionConnected ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-700'
                )}
              >
                <span className={cn('h-1.5 w-1.5 rounded-full', extensionEnabled && extensionConnected ? 'bg-emerald-500' : 'bg-slate-400')} />
                {!extensionEnabled ? '已在设置中关闭' : extensionConnected ? '已连接' : '未连接'}
              </span>
            </span>
            <Switch
              aria-label="使用浏览器插件执行"
              checked={extensionEnabled && draft.browserExecutor === 'extension'}
              disabled={!extensionEnabled || !extensionConnected}
              onCheckedChange={(checked) => {
                const next = checked ? 'extension' : 'playwright';
                updateDraft('browserExecutor', next);
                onSetDefaultBrowserExecutor?.(next);
              }}
            />
          </div>
          {extensionEnabled && draft.browserExecutor === 'extension' && (
            <p className="-mt-2 text-[11px] text-slate-500">
              将借助你当前打开的真实浏览器窗口执行，而不是独立的隐身会话；请保持该窗口打开直至运行结束。
            </p>
          )}
          {onSetDefaultBrowserExecutor !== undefined && (
            <p className="-mt-2 text-[10px] text-slate-500">已作为该流程的默认执行方式保存，定时任务可单独覆盖。</p>
          )}
          {!extensionConnected && (
            <p className="-mt-2 text-[11px] text-slate-500">
              {extensionEnabled ? '插件未连接？' : '插件已在设置中关闭。'}
              <button
                className="ml-1 font-medium text-blue-600 hover:underline"
                onClick={() => {
                  onOpenChange(false);
                  navigate(ROUTE_PATHS.settings, { state: { settingsSection: 'extension' } });
                }}
                type="button"
              >
                去设置里安装/开启
              </button>
            </p>
          )}

          <div className="rounded-lg border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2 text-[11px] font-medium text-slate-600">
              <span>本次运行变量覆写</span>
              <span className="font-mono text-[10px] text-slate-500">{inputVariables.length} 项</span>
            </div>
            <div className="max-h-52 space-y-2 overflow-auto p-3">
              {inputVariables.map((variable) => (
                <Label className="block" key={variable.name}>
                  <span className="mb-1 flex items-center justify-between gap-2 text-[11px]">
                    <span className="truncate font-mono text-blue-700">{variable.name}</span>
                    <span className="text-[10px] text-slate-500">
                      {(variable.category ?? 'flow') === 'credential' ? '凭据' : (variable.category ?? 'flow') === 'environment' ? '环境' : '流程'} · {variable.type} · {variable.scope}
                    </span>
                  </span>
                  <Input
                    className="font-mono text-[11px]"
                    onChange={(event) => updateOverride(variable.name, event.target.value)}
                    placeholder={variable.value}
                    type={variable.sensitive === true ? 'password' : 'text'}
                    value={draft.overrides[variable.name] ?? ''}
                  />
                </Label>
              ))}
            </div>
          </div>
        </DialogBody>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">取消</Button>
          </DialogClose>
          <Button disabled={startDisabled} onClick={handleStart} variant="primary">
            <Play className="h-3.5 w-3.5 fill-current" strokeWidth={1.5} />
            开始运行
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function clampConcurrency(value: number): number {
  if (!Number.isFinite(value)) {
    return 1;
  }
  return Math.min(20, Math.max(1, value));
}

function omitKey(source: Record<string, string>, key: string): Record<string, string> {
  const { [key]: _removed, ...rest } = source;
  return rest;
}
