import type { RunFailureStrategy, RunScope, RunStartPayload } from '../types/electron';
import type { RpaNodeAction, RuntimeVariable } from '../types/rpa';

const RUN_SCOPE_LABELS: Record<RunScope, string> = {
  full: '完整运行',
  'from-selection': '从选中步骤运行',
  'selected-only': '仅运行选中步骤'
};

const FAILURE_STRATEGY_LABELS: Record<RunFailureStrategy, string> = {
  stop: '停止运行',
  continue: '继续执行',
  retry: '重试当前步骤'
};

export function getRunScopeLabel(scope: RunScope | undefined): string {
  return RUN_SCOPE_LABELS[scope ?? 'full'];
}

export function getFailureStrategyLabel(strategy: RunFailureStrategy | undefined): string {
  return FAILURE_STRATEGY_LABELS[strategy ?? 'stop'];
}

export function normalizeRunConcurrency(value: number | undefined): number {
  if (value === undefined || !Number.isFinite(value)) {
    return 1;
  }
  return Math.min(20, Math.max(1, Math.round(value)));
}

export function normalizeRunTimeoutMs(value: number | undefined): number {
  if (value === undefined || !Number.isFinite(value)) {
    return 30_000;
  }
  return Math.min(300_000, Math.max(1_000, Math.round(value)));
}

export function buildRunConfigLogMessage(payload: RunStartPayload): string {
  const scopeLabel = getRunScopeLabel(payload.scope);
  const failureLabel = getFailureStrategyLabel(payload.failureStrategy);
  const concurrency = normalizeRunConcurrency(payload.concurrency);
  const screenshotLabel = payload.screenshot === false ? '关闭' : '开启';
  const timeoutMs = normalizeRunTimeoutMs(payload.timeoutMs);
  const startNodeText = payload.startNodeId === undefined ? '' : ` · 起点 ${payload.startNodeId}`;
  const overrideNames = payload.overrideVariables === undefined ? [] : Object.keys(payload.overrideVariables);
  const overrideCount = overrideNames.length;
  const overrideText =
    overrideCount === 0 ? '无覆写' : overrideCount <= 3 ? overrideNames.join(',') : `${overrideNames.slice(0, 3).join(',')} +${overrideCount - 3}`;

  return `运行配置 · 范围 ${scopeLabel} · 并发 ${concurrency} · 失败策略 ${failureLabel} · 截图 ${screenshotLabel} · 默认超时 ${timeoutMs}ms · 覆写 ${overrideCount} (${overrideText})${startNodeText}`;
}

export function buildRunConfigVariables(payload: RunStartPayload): RuntimeVariable[] {
  const variables: RuntimeVariable[] = [
    { category: 'environment', name: 'run_scope', scope: '全局', type: 'String', value: getRunScopeLabel(payload.scope) },
    { category: 'environment', name: 'run_concurrency', scope: '全局', type: 'Integer', value: String(normalizeRunConcurrency(payload.concurrency)) },
    { category: 'environment', name: 'failure_strategy', scope: '全局', type: 'String', value: getFailureStrategyLabel(payload.failureStrategy) },
    { category: 'environment', name: 'screenshot_enabled', scope: '全局', type: 'Boolean', value: payload.screenshot === false ? 'false' : 'true' },
    {
      category: 'environment',
      name: 'run_timeout_ms',
      scope: '全局',
      type: 'Integer',
      value: String(normalizeRunTimeoutMs(payload.timeoutMs))
    }
  ];

  if (payload.startNodeId !== undefined) {
    variables.push({ category: 'environment', name: 'start_node_id', scope: '全局', type: 'String', value: payload.startNodeId });
  }

  return variables;
}

export function buildEffectiveRunConfigSummary({
  failureStrategy,
  overrideCount,
  scope,
  screenshot,
  selectedNodeAction,
  selectedNodeId,
  selectedNodeTitle,
  timeoutMs
}: {
  failureStrategy: RunFailureStrategy | undefined;
  overrideCount: number;
  scope: RunScope | undefined;
  screenshot: boolean | undefined;
  selectedNodeAction?: RpaNodeAction;
  selectedNodeId?: string;
  selectedNodeTitle?: string;
  timeoutMs: number | undefined;
}): {
  defaultTimeoutMs: number;
  effectiveTimeoutMs: number;
  effectiveTimeoutSource: 'default' | 'node';
  failureLabel: string;
  scopeLabel: string;
  screenshotLabel: string;
  startNodeLabel?: string;
  targetLabel: string;
  overrideCount: number;
} {
  const normalizedScope = scope ?? 'full';
  const defaultTimeoutMs = normalizeRunTimeoutMs(timeoutMs);
  const nodeTimeoutMs =
    normalizedScope === 'full' || selectedNodeAction?.timeoutMs === undefined ? undefined : normalizeRunTimeoutMs(selectedNodeAction.timeoutMs);

  return {
    defaultTimeoutMs,
    effectiveTimeoutMs: nodeTimeoutMs ?? defaultTimeoutMs,
    effectiveTimeoutSource: nodeTimeoutMs === undefined ? 'default' : 'node',
    failureLabel: getFailureStrategyLabel(failureStrategy),
    overrideCount,
    scopeLabel: getRunScopeLabel(normalizedScope),
    screenshotLabel: screenshot === false ? '关闭' : '开启',
    startNodeLabel: normalizedScope === 'full' || selectedNodeId === undefined ? undefined : `${selectedNodeTitle ?? selectedNodeId} · ${selectedNodeId}`,
    targetLabel: normalizedScope === 'full' ? '完整流程' : selectedNodeTitle ?? selectedNodeId ?? '未选择步骤'
  };
}
