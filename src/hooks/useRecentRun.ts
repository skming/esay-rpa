import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import { useCallback, useRef } from 'react';

import type { BridgeCallOptions } from './electronBridgeTypes';
import { failedNodeId, historicalNodeStates, historicalRuntimeStatus, recentRunSummary, selectLatestTerminalRun, toRuntimeLogs } from '../lib/recentRunContext';
import { useBottomPanelStore } from '../stores/useBottomPanelStore';
import type { ArtifactSnapshot, BridgeResult, RpaBridge } from '../types/electron';
import type { NodeRuntimeState, RunLogEntry, RuntimeProgress, RuntimeStatus, RuntimeVariable } from '../types/rpa';

type RecentRunParams = {
  activeRunIdRef: MutableRefObject<string | null>;
  lastRunIdRef: MutableRefObject<string | null>;
  callBridge: <T>(action: (bridge: RpaBridge) => Promise<BridgeResult<T>>, successMessage?: string, options?: BridgeCallOptions) => Promise<T | null>;
  setLastRunId: Dispatch<SetStateAction<string | null>>;
  setLogs: Dispatch<SetStateAction<RunLogEntry[]>>;
  setNodeStates: Dispatch<SetStateAction<Record<string, NodeRuntimeState>>>;
  setProgress: Dispatch<SetStateAction<RuntimeProgress>>;
  setRuntimeStatus: Dispatch<SetStateAction<RuntimeStatus>>;
  setVariables: Dispatch<SetStateAction<RuntimeVariable[]>>;
  setArtifacts: Dispatch<SetStateAction<ArtifactSnapshot[]>>;
};

export function useRecentRun({
  activeRunIdRef, lastRunIdRef, callBridge, setLastRunId, setLogs, setNodeStates,
  setProgress, setRuntimeStatus, setVariables, setArtifacts,
}: RecentRunParams) {
  const requestVersion = useRef(0);
  const cancelRestore = useCallback(() => { requestVersion.current += 1; }, []);
  const restoreRecentRun = useCallback(async (flowId: string): Promise<void> => {
    if (activeRunIdRef.current !== null || lastRunIdRef.current !== null) return;
    const version = ++requestVersion.current;
    // Resetting the panel invalidates pending history even before React commits a flow or run switch.
    const isCurrent = (): boolean => requestVersion.current === version
      && activeRunIdRef.current === null && lastRunIdRef.current === null;
    const runs = await callBridge((api) => api.listFlowRuns(flowId, { limit: 1 }));
    if (!isCurrent() || runs === null) return;
    const latest = selectLatestTerminalRun(runs);
    if (latest === null) return;
    const detail = await callBridge((api) => api.getRunDetail(latest.taskId));
    if (!isCurrent() || detail === null || detail.run.flowId !== flowId) return;
    const status = historicalRuntimeStatus(detail.run);
    if (status === null) return;
    const logs = toRuntimeLogs(detail.logs);
    const nodeStates = historicalNodeStates(detail.run);
    if (status === 'error') {
      const nodeId = failedNodeId(detail);
      if (nodeId !== null) nodeStates[nodeId] = { status: 'error' };
      if (!logs.some((log) => log.level === 'error')) {
        logs.push({
          id: `${latest.taskId}:error`, level: 'error', message: recentRunSummary(detail),
          time: detail.run.updatedAt, ...(nodeId === null ? {} : { nodeId }),
        });
      }
    }
    lastRunIdRef.current = detail.run.taskId;
    setLastRunId(detail.run.taskId);
    setLogs(logs);
    setNodeStates(nodeStates);
    setProgress(detail.run.progress);
    setRuntimeStatus(status);
    setVariables(detail.run.variables ?? []);
    setArtifacts(detail.run.artifacts ?? []);
    if (status === 'error') {
      useBottomPanelStore.getState().setActiveTab('errors');
      useBottomPanelStore.getState().setOpen(true);
    }
  }, [activeRunIdRef, lastRunIdRef, callBridge, setLastRunId, setLogs, setNodeStates,
    setProgress, setRuntimeStatus, setVariables, setArtifacts]);
  return { cancelRestore, restoreRecentRun };
}
