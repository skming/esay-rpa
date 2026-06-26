import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import { useCallback } from 'react';

import { useBottomPanelStore } from '../stores/useBottomPanelStore';
import { useNotificationStore } from '../stores/useNotificationStore';
import type { ArtifactContent, ArtifactSnapshot, BridgeResult, GeneratedScriptResult, RpaBridge, RunEvent } from '../types/electron';
import type { NodeRuntimeState, RunLogEntry, RuntimeProgress, RuntimeStatus, RuntimeVariable } from '../types/rpa';
import type { BridgeCallOptions, BridgeToast } from './electronBridgeTypes';

type UseRunEventHandlerParams = {
  activeRunIdRef: MutableRefObject<string | null>;
  activeFlowNameRef: MutableRefObject<string>;
  callBridge: <T>(action: (bridge: RpaBridge) => Promise<BridgeResult<T>>, successMessage?: string, options?: BridgeCallOptions) => Promise<T | null>;
  lastRunIdRef: MutableRefObject<string | null>;
  pushToast: (type: BridgeToast['type'], message: string, icon?: string) => void;
  onArtifactsReady?: () => void;
  setActiveRunId: Dispatch<SetStateAction<string | null>>;
  setLastRunId: Dispatch<SetStateAction<string | null>>;
  setArtifactContent: Dispatch<SetStateAction<ArtifactContent | null>>;
  setArtifacts: Dispatch<SetStateAction<ArtifactSnapshot[]>>;
  setGeneratedScript: Dispatch<SetStateAction<GeneratedScriptResult | null>>;
  setInputPrompt: Dispatch<SetStateAction<string | null>>;
  setLogs: Dispatch<SetStateAction<RunLogEntry[]>>;
  setNodeStates: Dispatch<SetStateAction<Record<string, NodeRuntimeState>>>;
  setProgress: Dispatch<SetStateAction<RuntimeProgress>>;
  setRuntimeStatus: Dispatch<SetStateAction<RuntimeStatus>>;
  setVariables: Dispatch<SetStateAction<RuntimeVariable[]>>;
};

export function useRunEventHandler({
  activeRunIdRef,
  activeFlowNameRef,
  callBridge,
  lastRunIdRef,
  pushToast,
  onArtifactsReady,
  setActiveRunId,
  setLastRunId,
  setArtifactContent,
  setArtifacts,
  setGeneratedScript,
  setInputPrompt,
  setLogs,
  setNodeStates,
  setProgress,
  setRuntimeStatus,
  setVariables
}: UseRunEventHandlerParams): (event: RunEvent) => void {
  return useCallback(
    (event: RunEvent): void => {
      if (event.type === 'run:start') {
        // Prefer the name already written by the call site (actions hook), fall
        // back to the event payload for runs started externally (e.g. scheduler).
        if (!activeFlowNameRef.current && event.payload.flowName) {
          activeFlowNameRef.current = event.payload.flowName;
        }
        setActiveRunId(event.payload.runId);
        setLastRunId(event.payload.runId);
        setRuntimeStatus(event.payload.status);
        setInputPrompt(null);
        setLogs([]);
        setVariables([]);
        setArtifacts([]);
        setArtifactContent(null);
        setGeneratedScript(null);
        setProgress({ currentStep: 0, totalSteps: event.payload.totalSteps, percent: 0, elapsedMs: 0 });
        setNodeStates({});
        return;
      }

      if (!isCurrentRunEvent(event, activeRunIdRef.current, lastRunIdRef.current)) {
        return;
      }

      if (event.type === 'run:progress') {
        setProgress(event.payload);
        return;
      }

      if (event.type === 'node:update') {
        setNodeStates((current) => ({
          ...current,
          [event.payload.nodeId]: {
            badge: event.payload.badge,
            status: event.payload.status
          }
        }));
        return;
      }

      if (event.type === 'log:append') {
        setLogs((current) => [...current, event.payload].slice(-200));
        if (event.payload.level === 'input') {
          setInputPrompt(event.payload.message.replace(/^等待用户输入 · /, ''));
        }
        if (event.payload.level === 'warn' && event.payload.message.startsWith('命中断点')) {
          useBottomPanelStore.getState().setActiveTab('breakpoints');
          useBottomPanelStore.getState().setOpen(true);
        }
        return;
      }

      if (event.type === 'variable:set') {
        setVariables((current) => {
          const next = current.filter((variable) => variable.name !== event.payload.name);
          return [...next, event.payload];
        });
        return;
      }

      if (event.type === 'artifacts:update') {
        setArtifacts(event.payload.artifacts);
        if (event.payload.artifacts.length > 0) {
          onArtifactsReady?.();
        }
        return;
      }

      if (event.type === 'run:finish') {
        setLastRunId(event.payload.runId);
        setActiveRunId(null);
        setInputPrompt(null);
        setRuntimeStatus(event.payload.status);
        void callBridge((api) => api.listTaskVariables(event.payload.runId), undefined, { silent: true }).then((result) => {
          if (result !== null) {
            setVariables(result);
          }
        });
        void callBridge((api) => api.listArtifacts(event.payload.runId), undefined, { silent: true }).then((result) => {
          if (result !== null) {
            setArtifacts(result);
            if (result.length > 0) {
              onArtifactsReady?.();
            }
          }
        });
        const finishAt = new Date().toISOString();
        const flowName = activeFlowNameRef.current || '未命名流程';
        if (event.payload.status === 'error') {
          useBottomPanelStore.getState().setActiveTab('errors');
          useBottomPanelStore.getState().setOpen(true);
          pushToast('error', `「${flowName}」执行失败，详见错误面板`);
          useNotificationStore.getState().push({
            kind: 'error',
            title: `「${flowName}」执行失败`,
            body: event.payload.message || undefined,
            at: finishAt,
          });
        } else {
          const kind = event.payload.status === 'success' ? 'success' : 'info';
          const title = event.payload.status === 'success' ? `「${flowName}」已完成` : `「${flowName}」已停止`;
          pushToast(kind, event.payload.message || title);
          useNotificationStore.getState().push({
            kind,
            title,
            body: event.payload.message || undefined,
            at: finishAt,
          });
        }
      }
    },
    [
      activeRunIdRef,
      callBridge,
      lastRunIdRef,
      onArtifactsReady,
      pushToast,
      setActiveRunId,
      setLastRunId,
      setArtifactContent,
      setArtifacts,
      setGeneratedScript,
      setInputPrompt,
      setLogs,
      setNodeStates,
      setProgress,
      setRuntimeStatus,
      setVariables
    ]
  );
}

function isCurrentRunEvent(event: Exclude<RunEvent, { type: 'run:start' }>, activeRunId: string | null, lastRunId: string | null): boolean {
  const eventRunId = event.payload.runId;
  return eventRunId === activeRunId || eventRunId === lastRunId;
}
