import type { Dispatch, SetStateAction } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Edge, Node } from '@xyflow/react';

import { buildFlowDefinition, readFlowInputVariables, restoreFlowCanvas } from '../lib/flowDefinition';
import { useFlowDraftStore } from '../stores/useFlowDraftStore';
import { useFlowVariableStore } from '../stores/useFlowVariableStore';
import type { FlowSnapshot } from '../types/electron';
import type { RpaNodeData, RuntimeVariable } from '../types/rpa';

const AUTOSAVE_INTERVAL_MS = 30_000;
const STORAGE_SCHEMA_VERSION = 1 as const;

export function clearDraftStorage(): void {
  useFlowDraftStore.getState().clearDraft();
}

export type FlowDraftAutosaveState = {
  dirty: boolean;
  lastAutosavedAt: string | null;
  restoredAt: string | null;
  /** flowId from the restored draft (null if no draft or draft was for a local-only flow) */
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
  const storeDraft = useFlowDraftStore((s) => s.draft);
  const storeSetDraft = useFlowDraftStore((s) => s.setDraft);
  const storeClearDraft = useFlowDraftStore((s) => s.clearDraft);

  const hydratedRef = useRef(false);
  const [hydrated, setHydrated] = useState(false);
  const [baseSignature, setBaseSignature] = useState<string | null>(null);
  const [lastAutosavedAt, setLastAutosavedAt] = useState<string | null>(null);
  const [restoredAt, setRestoredAt] = useState<string | null>(null);
  const [restoredFlowId, setRestoredFlowId] = useState<string | null>(null);
  const replaceAllInputVariables = useFlowVariableStore((state) => state.replaceAllInputVariables);

  useEffect(() => {
    if (hydratedRef.current) {
      return;
    }

    hydratedRef.current = true;
    const storedDraft = storeDraft;
    if (storedDraft !== null && storedDraft.schemaVersion === STORAGE_SCHEMA_VERSION) {
      const restored = restoreFlowCanvas(storedDraft.definition);
      if (restored !== null) {
        setNodes(restored.nodes);
        setEdges(restored.edges);
        replaceAllInputVariables(readFlowInputVariables(storedDraft.definition));
        setBaseSignature(storedDraft.baseSignature);
        setRestoredAt(storedDraft.savedAt);
        setLastAutosavedAt(storedDraft.savedAt);
      }
      // Expose the restored flowId so App.tsx can silently re-hydrate currentFlow,
      // allowing the AI panel to load flow-specific conversation history.
      if (typeof storedDraft.flowId === 'string' && storedDraft.flowId && !storedDraft.flowId.startsWith('local-')) {
        setRestoredFlowId(storedDraft.flowId);
      }
    }
    setHydrated(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const currentSignature = useMemo(() => createCanvasSignature(nodes, edges, inputVariables), [edges, inputVariables, nodes]);
  const savedSignature = useMemo(
    () => currentFlow === null ? null : createDefinitionSignature(buildFlowDefinition(nodes, edges, currentFlow.inputVariables, currentFlow.name)),
    [currentFlow, edges, nodes]
  );

  useEffect(() => {
    if (!hydrated) {
      return;
    }
    if (savedSignature !== null) {
      setBaseSignature(savedSignature);
      return;
    }
    if (baseSignature === null) {
      setBaseSignature(currentSignature);
    }
  }, [baseSignature, currentSignature, hydrated, savedSignature]);

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
    setLastAutosavedAt(savedAt);
  }, [currentFlow?.flowId, currentFlow?.name, dirty, edges, inputVariables, nodes, baseSignature, storeSetDraft]);

  useEffect(() => {
    if (!hydrated) {
      return;
    }
    if (!dirty) {
      storeClearDraft();
      setLastAutosavedAt(null);
      return;
    }

    const timer = window.setInterval(writeCurrentDraft, AUTOSAVE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [dirty, hydrated, writeCurrentDraft, storeClearDraft]);

  useEffect(() => {
    if (!dirty) {
      return;
    }

    const handleBeforeUnload = (): void => writeCurrentDraft();
    const handleVisibilityChange = (): void => {
      if (document.visibilityState === 'hidden') {
        writeCurrentDraft();
      }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [dirty, writeCurrentDraft]);

  return { dirty, lastAutosavedAt, restoredAt, restoredFlowId };
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
