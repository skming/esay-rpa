import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import type { RuntimeVariable } from '../types/rpa';

type RunConfigStore = {
  // null 表示当前没有生效的覆盖值
  flowKey: string | null;
  lastRunOverrideVariables: RuntimeVariable[];
  clearLastRunOverrides: () => void;
  setLastRunOverrides: (flowKey: string | null, variables: RuntimeVariable[]) => void;
};

// 画布处于本地未保存模式（无后端 flow ID）时使用的哨兵 key
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
