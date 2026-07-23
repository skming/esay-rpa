import type { RuntimeVariable, RuntimeVariableSource, RuntimeVariableView } from '../types/rpa';

/** Merges flow input variables with live runtime values; runtime takes precedence so a new run carries forward in-flight values. */
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

/** Merges flow defaults, run-config overrides, and live backend values into the Variables panel rows, tagging each with its source layer. */
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

/** An override only counts as such when its value differs from the default — avoids a misleading badge on unchanged values. */
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
