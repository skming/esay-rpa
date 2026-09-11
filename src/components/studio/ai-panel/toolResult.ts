import type { ToolCallState } from './aiPanelTypes';

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
