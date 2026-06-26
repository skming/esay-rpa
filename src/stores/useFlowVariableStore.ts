import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import { normalizeVariableName } from '../lib/variableNaming';
import type { RuntimeVariable, VariableCategory } from '../types/rpa';

type FlowVariableStore = {
  inputVariables: RuntimeVariable[];
  addInputVariable: (category?: VariableCategory) => void;
  /** Adds a variable with a specific name (e.g. when a node references an undefined variable). */
  addNamedInputVariable: (name: string) => void;
  removeInputVariable: (name: string) => void;
  replaceAllInputVariables: (variables: RuntimeVariable[]) => void;
  updateInputVariable: (name: string, patch: Partial<RuntimeVariable>) => void;
};

const DEFAULT_VARIABLE: RuntimeVariable = {
  category: 'flow',
  name: 'new_variable',
  sensitive: false,
  type: 'String',
  value: '',
  scope: '全局'
};

export const useFlowVariableStore = create<FlowVariableStore>()(
  persist(
    (set) => ({
      inputVariables: [],
      addInputVariable: (category?: VariableCategory) =>
        set((state) => ({
          inputVariables: [
            ...state.inputVariables,
            createUniqueVariable(
              category !== undefined ? { ...DEFAULT_VARIABLE, category } : DEFAULT_VARIABLE,
              state.inputVariables
            )
          ]
        })),
      addNamedInputVariable: (name: string) =>
        set((state) => ({
          inputVariables: [
            ...state.inputVariables,
            createUniqueVariable({ ...DEFAULT_VARIABLE, name }, state.inputVariables)
          ]
        })),
      removeInputVariable: (name) =>
        set((state) => ({
          inputVariables: state.inputVariables.filter((variable) => variable.name !== name)
        })),
      replaceAllInputVariables: (variables) =>
        set({
          inputVariables: normalizeVariables(variables)
        }),
      updateInputVariable: (name, patch) =>
        set((state) => ({
          inputVariables: state.inputVariables.map((variable) =>
            variable.name === name ? createUniqueVariable({ ...variable, ...patch }, state.inputVariables, name) : variable
          )
        }))
    }),
    {
      name: 'rpa-studio.flow-input-variables'
    }
  )
);

function normalizeVariables(variables: RuntimeVariable[]): RuntimeVariable[] {
  const normalized: RuntimeVariable[] = [];
  for (const variable of variables) {
    normalized.push(createUniqueVariable(variable, normalized));
  }
  return normalized;
}

/**
 * Ensures a variable has a unique, valid name by appending a numeric suffix
 * when the desired name is already taken. `originalName` is excluded from the
 * reserved set so renaming a variable to itself is a no-op.
 */
function createUniqueVariable(variable: RuntimeVariable, existing: RuntimeVariable[], originalName?: string): RuntimeVariable {
  const baseName = normalizeVariableName(variable.name) || DEFAULT_VARIABLE.name;
  const reserved = new Set(existing.filter((item) => item.name !== originalName).map((item) => item.name));
  let nextName = baseName;
  let suffix = 2;
  while (reserved.has(nextName)) {
    nextName = `${baseName}_${suffix}`;
    suffix += 1;
  }
  return {
    category: variable.category ?? DEFAULT_VARIABLE.category,
    name: nextName,
    sensitive: variable.sensitive ?? false,
    type: variable.type,
    value: variable.value,
    scope: variable.scope
  };
}
