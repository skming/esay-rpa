import { CheckCircle2, Circle, Layers3, Play, Route, SquareMousePointer, Workflow } from 'lucide-react';
import type { ReactElement } from 'react';
import { useMemo, useState } from 'react';

import type { RunFailureStrategy, RunScope } from '../../types/electron';
import type { StartRunOptions } from '../../hooks/useElectronBridgeActions';
import type { RpaNodeAction, RuntimeVariable } from '../../types/rpa';
import { buildEffectiveRunConfigSummary, normalizeRunTimeoutMs } from '../../lib/runConfigPresentation';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { Switch } from '../ui/switch';
import { cn } from '../../lib/utils';

type RunConfigDraft = {
  concurrency: number;
  failureStrategy: RunFailureStrategy;
  overrides: Record<string, string>;
  scope: RunScope;
  screenshot: boolean;
  timeoutMs: number;
};

const scopeOptions = [
  { icon: Workflow, label: '完整运行', value: 'full' },
  { icon: Route, label: '从选中步骤运行', value: 'from-selection' },
  { icon: SquareMousePointer, label: '仅运行选中步骤', value: 'selected-only' }
] satisfies Array<{ icon: typeof Workflow; label: string; value: RunScope }>;

export function RunConfigDialog({
  inputVariables,
  onOpenChange,
  onStart,
  open,
  running,
  selectedNodeAction,
  selectedNodeId,
  selectedNodeTitle
}: {
  inputVariables: RuntimeVariable[];
  onOpenChange: (open: boolean) => void;
  onStart: (options: StartRunOptions) => void;
  open: boolean;
  running: boolean;
  selectedNodeAction?: RpaNodeAction;
  selectedNodeId: string;
  selectedNodeTitle: string;
}): ReactElement {
  const [draft, setDraft] = useState<RunConfigDraft>({
    concurrency: 1,
    failureStrategy: 'stop',
    overrides: {},
    scope: 'full',
    screenshot: true,
    timeoutMs: 30_000
  });
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
                  {option.value !== 'full' && <span className="max-w-32 truncate font-mono text-[10px] text-slate-400">{selectedNodeTitle}</span>}
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
            <span className="mt-1 block text-[10px] text-slate-400">当节点未单独配置 timeoutMs 时使用该值；节点内已配置超时优先。</span>
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

          <div className="rounded-lg border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2 text-[11px] font-medium text-slate-600">
              <span>本次运行变量覆写</span>
              <span className="font-mono text-[10px] text-slate-400">{inputVariables.length} 项</span>
            </div>
            <div className="max-h-52 space-y-2 overflow-auto p-3">
              {inputVariables.map((variable) => (
                <Label className="block" key={variable.name}>
                  <span className="mb-1 flex items-center justify-between gap-2 text-[11px]">
                    <span className="truncate font-mono text-blue-700">{variable.name}</span>
                    <span className="text-[10px] text-slate-400">
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
