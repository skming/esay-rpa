import type { ToolCallState } from './aiPanelTypes';

const SENSITIVE_KEY = /password|passwd|passcode|secret|token|cookie|authorization|api.?key|access.?key|private.?key/i;

function readObject(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function arrayLength(value: unknown): number | undefined {
  return Array.isArray(value) ? value.length : undefined;
}

function firstNumber(...values: unknown[]): number | undefined {
  return values.find((value): value is number => typeof value === 'number' && Number.isFinite(value));
}

function shortText(value: unknown, max = 72): string | undefined {
  if (typeof value !== 'string' || !value.trim()) return undefined;
  const text = value.trim().replace(/\s+/g, ' ');
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function pageActionCount(result: Record<string, unknown>): number {
  let count = arrayLength(result.page_actions) ?? 0;
  for (const key of ['inputs', 'selects', 'buttons', 'links', 'visible_options', 'scrollables']) {
    for (const item of Array.isArray(result[key]) ? result[key] : []) {
      const object = readObject(item);
      count += arrayLength(object?.actions) ?? 0;
      count += arrayLength(object?.option_actions) ?? 0;
    }
  }
  return count;
}

export function resultObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

export function toolResultStatus(value: unknown): ToolCallState['status'] {
  const result = resultObject(value);
  const status = typeof result.status === 'string' ? result.status : '';
  if (status.startsWith('blocked_') || status.startsWith('blocking_') || [
    'extension_not_connected', 'extension_disabled', 'empty_credential_variables',
    'missing_run_variables', 'misplaced_call_parameters', 'undefined_variable_refs',
  ].includes(status)) return 'blocked';
  if (status === 'stopped') return 'stopped';
  if (result.error || ['error', 'failed', 'timeout'].includes(status)
    || result.passed === false || resultObject(result.acceptance_audit).passed === false) return 'error';
  return 'done';
}

export function toolDisplayStatus(call: ToolCallState, live = true): ToolCallState['status'] {
  if (call.result !== undefined) return toolResultStatus(call.result);
  return !live && call.status === 'running' ? 'stopped' : call.status;
}

/** Tool rows show evidence, not another generic "done" label. */
export function toolResultSummary(call: ToolCallState): string | undefined {
  const result = readObject(call.result);
  if (!result) return call.status === 'running' ? '执行中' : undefined;

  const displayStatus = toolResultStatus(result);
  if (displayStatus === 'error' || displayStatus === 'blocked') {
    return shortText(result.message) ?? shortText(result.error) ?? (displayStatus === 'blocked' ? '已阻断' : '异常');
  }
  if (displayStatus === 'stopped') return '已中断';

  const findings = Array.isArray(result.lint_findings)
    ? result.lint_findings
    : Array.isArray(result.findings) ? result.findings : undefined;
  const revision = typeof result.revision === 'number' ? `r${result.revision}` : undefined;
  const changedCount = arrayLength(result.changed_nodes);
  const definition = readObject(result.definition);
  const nodeCount = arrayLength(result.nodes) ?? arrayLength(definition?.nodes);

  switch (call.tool) {
    case 'create_flow':
    case 'update_flow':
    case 'apply_node_fix':
    case 'set_acceptance_contract': {
      const parts = [revision];
      if (changedCount !== undefined) parts.push(`${changedCount} 个节点变更`);
      else if (nodeCount !== undefined) parts.push(`${nodeCount} 个节点`);
      return parts.filter(Boolean).join(' · ') || '已写入';
    }
    case 'get_flow':
      return [revision, nodeCount !== undefined ? `${nodeCount} 个节点` : undefined].filter(Boolean).join(' · ') || undefined;
    case 'lint_flow': {
      if (!findings) return result.lint_clean === true ? '未发现阻断项' : undefined;
      const blocked = findings.filter((item) => {
        const finding = readObject(item);
        return finding?.severity === 'error' || finding?.blocks_run === true;
      }).length;
      return blocked > 0 ? `${blocked} 项阻断` : findings.length > 0 ? `${findings.length} 项提示` : '未发现阻断项';
    }
    case 'validate_flow':
      return result.valid === false ? '引用无效' : result.valid === true ? '引用有效' : undefined;
    case 'run_flow': {
      const audit = readObject(result.acceptance_audit);
      if (result.status === 'success' && audit?.passed === true) return '运行成功 · 验收通过';
      if (result.status === 'success' && audit?.passed === false) return '运行成功 · 验收未通过';
      return result.status === 'success' ? '运行成功' : shortText(result.status);
    }
    case 'assert_run_output':
      return result.passed === true ? '验收通过' : result.passed === false ? '验收未通过' : undefined;
    case 'inspect_page': {
      const actionCount = pageActionCount(result);
      const outcome = shortText(result.page_outcome);
      const outcomeLabel = outcome === 'target_content_ready' ? '目标内容已就绪' : outcome;
      return [outcomeLabel, actionCount > 0 ? `${actionCount} 个可操作目标` : undefined].filter(Boolean).join(' · ') || undefined;
    }
    case 'interact_page': {
      const effect = readObject(result.action_effect);
      const status = effect?.status;
      const labels: Record<string, string> = {
        target_reached: '目标状态已达到', already_in_target_state: '已处于目标状态',
        target_not_reached: '未达到目标状态', state_changed: '页面状态已变化',
        focus_only: '仅焦点变化', no_observable_change: '未观察到变化',
      };
      return typeof status === 'string' ? labels[status] ?? status : undefined;
    }
    case 'list_node_types': {
      const count = arrayLength(result.node_types) ?? arrayLength(result.types) ?? arrayLength(result.items);
      return count === undefined ? undefined : `${count} 种节点`;
    }
    case 'list_flows':
    case 'list_schedules': {
      const count = firstNumber(result.count, arrayLength(result.flows), arrayLength(result.schedules), arrayLength(result.items));
      return count === undefined ? undefined : `${count} 项`;
    }
    case 'get_run_logs': {
      const count = firstNumber(result.count, arrayLength(result.logs));
      return count === undefined ? undefined : `${count} 条日志`;
    }
    case 'get_run_output': {
      const variables = readObject(result.variables);
      const artifacts = arrayLength(result.artifacts) ?? 0;
      const outputs = variables ? Object.keys(variables).length : 0;
      if (outputs || artifacts) return [`${outputs} 项输出`, artifacts ? `${artifacts} 个产物` : undefined].filter(Boolean).join(' · ');
      return result.status === 'success' ? '无输出数据' : undefined;
    }
    case 'check_extension_connection':
      return result.connected === true ? '扩展已连接' : result.connected === false ? '扩展未连接' : undefined;
    default:
      return shortText(result.summary);
  }
}

/** Keep diagnostics inspectable without exposing credentials in the panel. */
export function redactToolPayload(value: unknown, parentKey = '', redactVariables = false): unknown {
  if (Array.isArray(value)) return value.map((item) => redactToolPayload(item, parentKey, redactVariables));
  const object = readObject(value);
  if (!object) return value;
  const protectedVariable = object.sensitive === true || object.category === 'credential';
  return Object.fromEntries(Object.entries(object).map(([key, item]) => {
    if (SENSITIVE_KEY.test(key) || (protectedVariable && key === 'value')
      || (redactVariables && parentKey === 'variables')) return [key, '••••'];
    return [key, redactToolPayload(item, key, redactVariables)];
  }));
}
