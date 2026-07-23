import type { Dispatch, SetStateAction } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Edge, Node } from '@xyflow/react';

import { buildFlowDefinition, readFlowInputVariables, restoreFlowCanvas } from '../lib/flowDefinition';
import { useFlowDraftStore, type StoredFlowDraft } from '../stores/useFlowDraftStore';
import { useFlowVariableStore } from '../stores/useFlowVariableStore';
import type { FlowSnapshot } from '../types/electron';
import type { RpaNodeData, RuntimeVariable } from '../types/rpa';

const AUTOSAVE_INTERVAL_MS = 30_000;
// 草稿结构变更时递增此值，旧版本号的草稿会被视为不兼容并直接丢弃（见下方 hydrate 逻辑）
const STORAGE_SCHEMA_VERSION = 1 as const;

export function clearDraftStorage(): void {
  useFlowDraftStore.getState().clearDraft();
}

type RestorableDraft = {
  nodes: Node<RpaNodeData>[];
  edges: Edge[];
  inputVariables: RuntimeVariable[];
  baseSignature: string;
  savedAt: string;
  flowId: string | null;
};

export function readRestorableDraft(draft: StoredFlowDraft | null): RestorableDraft | null {
  if (draft === null || draft.schemaVersion !== STORAGE_SCHEMA_VERSION) {
    return null;
  }
  const restored = restoreFlowCanvas(draft.definition);
  if (restored === null) {
    return null;
  }
  return {
    baseSignature: draft.baseSignature,
    edges: restored.edges,
    // local- 开头的是未落库的临时流程，回填给 App 也查不到对应记录
    flowId: typeof draft.flowId === 'string' && draft.flowId !== '' && !draft.flowId.startsWith('local-')
      ? draft.flowId
      : null,
    inputVariables: readFlowInputVariables(draft.definition),
    nodes: restored.nodes,
    savedAt: draft.savedAt,
  };
}

export type FlowDraftAutosaveState = {
  dirty: boolean;
  hydrated: boolean;
  lastAutosavedAt: string | null;
  restoredAt: string | null;
  restoredFlowId: string | null;
};

export function useFlowDraftAutosave({
  currentFlow,
  edges,
  inputVariables,
  nodes,
  setEdges,
  setNodes
}: {
  currentFlow: FlowSnapshot | null;
  edges: Edge[];
  inputVariables: RuntimeVariable[];
  nodes: Node<RpaNodeData>[];
  setEdges: Dispatch<SetStateAction<Edge[]>>;
  setNodes: Dispatch<SetStateAction<Node<RpaNodeData>[]>>;
}): FlowDraftAutosaveState {
  const storeSetDraft = useFlowDraftStore((s) => s.setDraft);
  const storeClearDraft = useFlowDraftStore((s) => s.clearDraft);

  // 只在首帧读一次：之后 store 的变化都是本 hook 自己写回去的，再读会把恢复初值和刚存的现值混在一起
  const [restorable] = useState(() => readRestorableDraft(useFlowDraftStore.getState().draft));

  const hydratedRef = useRef(false);
  const [hydrated, setHydrated] = useState(false);
  const [fallbackBaseSignature, setFallbackBaseSignature] = useState<string | null>(restorable?.baseSignature ?? null);
  // 直接读 store 而非用 state 镜像一份：写草稿和清草稿都只改 store 一处，不会两边脱节
  const lastAutosavedAt = useFlowDraftStore((s) => s.draft?.savedAt ?? null);
  const restoredAt = restorable?.savedAt ?? null;
  // 暴露 restoredFlowId 供 App.tsx 静默重新水合 currentFlow，AI 面板据此加载对应对话历史
  const restoredFlowId = restorable?.flowId ?? null;
  const replaceAllInputVariables = useFlowVariableStore((state) => state.replaceAllInputVariables);

  useEffect(() => {
    if (hydratedRef.current) {
      return;
    }

    hydratedRef.current = true;
    if (restorable !== null) {
      setNodes(restorable.nodes);
      setEdges(restorable.edges);
      replaceAllInputVariables(restorable.inputVariables);
    }
    // 必须等画布真被写入后才置真：下面的 effect 在 dirty 为假时会清草稿，提前放行会删掉用户未保存的工作
    setHydrated(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const currentSignature = useMemo(() => createCanvasSignature(nodes, edges, inputVariables), [edges, inputVariables, nodes]);
  const savedSignature = useMemo(
    () => currentFlow === null ? null : createDefinitionSignature(buildFlowDefinition(nodes, edges, currentFlow.inputVariables, currentFlow.name)),
    [currentFlow, edges, nodes]
  );

  // 未保存流程的基线必须冻结在首帧：跟着 currentSignature 走则两者恒等，dirty 永远为假，自动保存不再触发。
  // 放渲染期而非 effect：effect 会多绘一帧 baseSignature 为 null 的中间态
  if (hydrated && savedSignature === null && fallbackBaseSignature === null) {
    setFallbackBaseSignature(currentSignature);
  }
  const baseSignature = savedSignature ?? fallbackBaseSignature;

  const dirty = hydrated && baseSignature !== null && currentSignature !== baseSignature;

  const writeCurrentDraft = useCallback(() => {
    if (!dirty) {
      return;
    }

    const savedAt = new Date().toISOString();
    storeSetDraft({
      schemaVersion: STORAGE_SCHEMA_VERSION,
      savedAt,
      flowId: currentFlow?.flowId ?? null,
      flowName: currentFlow?.name ?? '未命名流程',
      baseSignature: baseSignature!,
      definition: buildFlowDefinition(nodes, edges, inputVariables, currentFlow?.name),
    });
  }, [currentFlow?.flowId, currentFlow?.name, dirty, edges, inputVariables, nodes, baseSignature, storeSetDraft]);

  // writeCurrentDraft 的身份每次编辑都变，定时器若直接依赖它会被反复重建，持续编辑时 30s 周期永远走不完
  const writeCurrentDraftRef = useRef(writeCurrentDraft);
  useEffect(() => {
    writeCurrentDraftRef.current = writeCurrentDraft;
  }, [writeCurrentDraft]);

  useEffect(() => {
    if (!hydrated) {
      return;
    }
    if (!dirty) {
      storeClearDraft();
      return;
    }

    const timer = window.setInterval(() => writeCurrentDraftRef.current(), AUTOSAVE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [dirty, hydrated, storeClearDraft]);

  useEffect(() => {
    if (!dirty) {
      return;
    }

    const handleBeforeUnload = (): void => writeCurrentDraftRef.current();
    const handleVisibilityChange = (): void => {
      if (document.visibilityState === 'hidden') {
        writeCurrentDraftRef.current();
      }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [dirty]);

  return { dirty, hydrated, lastAutosavedAt, restoredAt, restoredFlowId };
}

function createCanvasSignature(nodes: Node<RpaNodeData>[], edges: Edge[], inputVariables: RuntimeVariable[]): string {
  return createDefinitionSignature(buildFlowDefinition(nodes, edges, inputVariables));
}

function createDefinitionSignature(definition: Record<string, unknown>): string {
  const stableDefinition = { ...definition };
  // name and exportedAt are metadata — exclude so renaming a flow doesn't mark it dirty
  delete stableDefinition.name;
  delete stableDefinition.exportedAt;
  return stableStringify(stableDefinition);
}

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(',')}]`;
  }

  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, item]) => item !== undefined)
    .sort(([left], [right]) => left.localeCompare(right));

  return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`).join(',')}}`;
}
