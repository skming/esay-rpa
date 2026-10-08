import type { RuntimeVariable } from '../types/rpa';
import { isSafeVariableName } from './variableNaming';

export function parseInputVariableValue(variable: RuntimeVariable): unknown {
  const { type, value } = variable;
  if (type === 'String') return value;
  if (type === 'Integer' && /^[+-]?[0-9]+$/.test(value.trim())) {
    const parsed = Number(value.trim());
    if (Number.isSafeInteger(parsed)) return parsed;
  }
  if (type === 'Boolean' && /^(true|false)$/i.test(value.trim())) {
    return value.trim().toLowerCase() === 'true';
  }
  if (type === 'List' || type === 'Dict') {
    try {
      const parsed: unknown = JSON.parse(value);
      if (type === 'List' && Array.isArray(parsed)) return parsed;
      if (type === 'Dict' && parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
    } catch { /* 下方统一返回不包含输入值的类型错误。 */ }
  }
  throw new Error(`输入变量 ${variable.name} 必须为有效的 ${type}`);
}

export function buildRuntimeVariablePayload(variables: RuntimeVariable[]): Record<string, unknown> {
  return Object.fromEntries(variables.filter((variable) => isSafeVariableName(variable.name))
    .map((variable) => [variable.name, parseInputVariableValue(variable)]));
}
