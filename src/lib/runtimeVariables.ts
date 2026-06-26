import type { RuntimeVariable, RuntimeVariableSource, RuntimeVariableView } from '../types/rpa';

/**
 * Merges flow-level input variables with live runtime variables emitted by the
 * backend. Runtime values take precedence; the result is used when starting a
 * new run so the latest in-flight values are carried forward.
 */
export function mergeRuntimeVariables(inputVariables: RuntimeVariable[], runtimeVariables: RuntimeVariable[]): RuntimeVariable[] {
  const merged = new Map<string, RuntimeVariable>();
  for (const variable of inputVariables) {
    merged.set(variable.name, variable);
  }
  for (const variable of runtimeVariables) {
    merged.set(variable.name, variable);
  }
  return [...merged.values()];
}

/**
 * Builds the enriched variable rows shown in the Variables panel by merging
 * three layers — flow defaults, run-config overrides, and live backend values —
 * and computing a `source` discriminator for each entry.
 */
export function buildRuntimeVariableViews(
  inputVariables: RuntimeVariable[],
  overrideVariables: RuntimeVariable[],
  runtimeVariables: RuntimeVariable[]
): RuntimeVariableView[] {
  const inputByName = new Map(inputVariables.map((variable) => [variable.name, variable] as const));
  const overrideByName = new Map(overrideVariables.map((variable) => [variable.name, variable] as const));
  const runtimeByName = new Map(runtimeVariables.map((variable) => [variable.name, variable] as const));
  const orderedNames = new Set<string>();

  for (const variable of inputVariables) {
    orderedNames.add(variable.name);
  }
  for (const variable of overrideVariables) {
    orderedNames.add(variable.name);
  }
  for (const variable of runtimeVariables) {
    orderedNames.add(variable.name);
  }

  return [...orderedNames].map((name) => {
    const defaultVariable = inputByName.get(name);
    const overrideVariable = overrideByName.get(name);
    const runtimeVariable = runtimeByName.get(name);
    const activeVariable = runtimeVariable ?? overrideVariable ?? defaultVariable;

    if (activeVariable === undefined) {
      throw new Error(`Missing variable payload for ${name}`);
    }

    return {
      ...activeVariable,
      category: activeVariable.category ?? defaultVariable?.category ?? overrideVariable?.category ?? 'flow',
      defaultValue: defaultVariable?.value,
      overrideValue: overrideVariable?.value,
      runtimeValue: runtimeVariable?.value,
      sensitive: activeVariable.sensitive ?? defaultVariable?.sensitive ?? overrideVariable?.sensitive ?? false,
      source: resolveVariableSource(defaultVariable, overrideVariable, runtimeVariable)
    };
  });
}

/**
 * Determines the active layer for a variable.
 * An override is only reported as such when its value actually differs from the
 * declared default, avoiding a misleading 'override' badge for unchanged values.
 */
function resolveVariableSource(
  defaultVariable: RuntimeVariable | undefined,
  overrideVariable: RuntimeVariable | undefined,
  runtimeVariable: RuntimeVariable | undefined
): RuntimeVariableSource {
  if (runtimeVariable !== undefined) {
    return 'runtime';
  }
  if (overrideVariable !== undefined && (defaultVariable === undefined || overrideVariable.value !== defaultVariable.value)) {
    return 'override';
  }
  return 'default';
}
