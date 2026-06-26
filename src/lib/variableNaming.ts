export type VariableNameIssue = 'invalid' | 'missing_reference' | 'overwrite_target';

export type VariableNameValidation = {
  /** The cleaned-up form of the input, shown as a hint when the raw value would be rejected. */
  normalizedValue: string;
  issue: VariableNameIssue | null;
  message: string | null;
};

/**
 * Strips leading non-alpha chars, replaces spaces with underscores, and removes
 * other illegal characters to produce a valid variable name suggestion.
 */
export function normalizeVariableName(value: string): string {
  const trimmed = value.trim();
  const strippedPrefix = trimmed.replace(/^[^A-Za-z_]+/, '');
  const normalized = strippedPrefix.replace(/\s+/g, '_').replace(/[^A-Za-z0-9_.-]/g, '_');
  return trimmed.startsWith('_') ? normalized : normalized.replace(/^_+/, '');
}

export function isSafeVariableName(value: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_.-]{0,119}$/.test(value.trim());
}

/**
 * Validates a variable name as typed in the property panel.
 * - `reference` mode warns when the name doesn't match any known variable.
 * - `target` mode warns when the name already exists (will overwrite).
 */
export function validateVariableNameInput(
  value: string,
  options: {
    existingNames?: string[];
    mode: 'reference' | 'target';
  }
): VariableNameValidation {
  const trimmed = value.trim();
  const existingNames = new Set((options.existingNames ?? []).map((name) => name.trim()).filter((name) => name.length > 0));
  const normalizedValue = normalizeVariableName(trimmed);

  if (trimmed.length === 0) {
    return { issue: null, message: null, normalizedValue };
  }

  if (!isSafeVariableName(trimmed)) {
    return {
      issue: 'invalid',
      message: normalizedValue.length > 0 ? `变量名将被规范化为 ${normalizedValue}` : '变量名必须以字母或下划线开头，仅允许字母、数字、下划线、点和短横线',
      normalizedValue
    };
  }

  if (options.mode === 'reference' && !existingNames.has(trimmed)) {
    return {
      issue: 'missing_reference',
      message: '当前流程和运行态中未找到该变量',
      normalizedValue
    };
  }

  if (options.mode === 'target' && existingNames.has(trimmed)) {
    return {
      issue: 'overwrite_target',
      message: '该变量已存在，运行时会覆盖当前值',
      normalizedValue
    };
  }

  return { issue: null, message: null, normalizedValue };
}
