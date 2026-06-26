import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import type { RuntimeVariable } from '../types/rpa';

type RunConfigStore = {
  /** Identifies which flow the stored overrides belong to; null means no overrides are active. */
  flowKey: string | null;
  /** Variable values entered in the last run dialog, persisted so they re-populate on the next open. */
  lastRunOverrideVariables: RuntimeVariable[];
  clearLastRunOverrides: () => void;
  setLastRunOverrides: (flowKey: string | null, variables: RuntimeVariable[]) => void;
};

/** Sentinel key used when the canvas is in local (unsaved) mode with no backend flow ID. */
export const LOCAL_FLOW_KEY = '__local_flow__';

export const useRunConfigStore = create<RunConfigStore>()(
  persist<RunConfigStore>(
    (set) => ({
      flowKey: null,
      lastRunOverrideVariables: [],
      clearLastRunOverrides: () =>
        set({
          flowKey: null,
          lastRunOverrideVariables: []
        }),
      setLastRunOverrides: (flowKey, variables) =>
        set({
          flowKey,
          lastRunOverrideVariables: normalizeVariables(variables)
        })
    }),
    {
      name: 'rpa-studio.run-config'
    }
  )
);

function normalizeVariables(variables: RuntimeVariable[]): RuntimeVariable[] {
  const uniqueVariables = new Map<string, RuntimeVariable>();
  for (const variable of variables) {
    uniqueVariables.set(variable.name, { ...variable });
  }
  return [...uniqueVariables.values()];
}
