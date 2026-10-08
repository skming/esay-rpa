import { useMemo } from 'react';
import type { PickerOpenPayload } from '../types/electron';
import { toSafeFilename } from '../lib/filenames';
import type { UseElectronBridgeActionsParams, ElectronBridgeActions } from './electronBridgeActionTypes';
import { createFlowBridgeActions } from './flowBridgeActions';
import { createRunBridgeActions } from './runBridgeActions';
import { createScheduleBridgeActions } from './scheduleBridgeActions';

export type { ElectronBridgeActions, StartRunOptions, CreateScheduleOptions } from './electronBridgeActionTypes';

export function useElectronBridgeActions({
  activeFlowNameRef,
  activeRunId,
  activeRunFlowId,
  callBridge,
  clearLastRunOverrides,
  currentFlow,
  flowCanvas,
  flows,
  pushToast,
  dismissToast,
  inputVariables,
  resetRunView,
  restoreRecentRun,
  setLastRunOverrides,
  setCurrentFlow,
  setFlowEdges,
  setFlowNodes,
  setFlows,
  setInputVariables,
  setSelectedNodeId,
  setActiveRunId,
  setActiveRunFlowId,
  setArtifactContent,
  setArtifacts,
  setGeneratedScript,
  setQueueStats,
  setRuntimeStatus,
  setRuns,
  setSchedules,
  setScheduleRunSummaries,
  setSiteAnalysis,
  setVariables,
  setLogs,
  setConfirmationMessage,
  setActivePickerRequest,
  setCanvasFitVersion,
}: UseElectronBridgeActionsParams): ElectronBridgeActions {
  const flowBridgeActions = useMemo(
    () => createFlowBridgeActions({
      activeRunId,
      activeRunFlowId,
      callBridge,
      clearLastRunOverrides,
      currentFlow,
      flowCanvas,
      flows,
      pushToast,
      dismissToast,
      inputVariables,
      resetRunView,
      restoreRecentRun,
      setCurrentFlow,
      setFlowEdges,
      setFlowNodes,
      setFlows,
      setInputVariables,
      setSelectedNodeId,
      setCanvasFitVersion,
    }),
    [
      activeRunId,
      activeRunFlowId,
      callBridge,
      clearLastRunOverrides,
      currentFlow,
      flowCanvas,
      flows,
      pushToast,
      dismissToast,
      inputVariables,
      resetRunView,
      restoreRecentRun,
      setCurrentFlow,
      setFlowEdges,
      setFlowNodes,
      setFlows,
      setInputVariables,
      setSelectedNodeId,
      setCanvasFitVersion,
    ],
  );
  const runBridgeActions = useMemo(
    () => createRunBridgeActions({
      activeFlowNameRef,
      activeRunId,
      callBridge,
      currentFlow,
      flowCanvas,
      flows,
      pushToast,
      inputVariables,
      resetRunView,
      setLastRunOverrides,
      setFlowNodes,
      setSelectedNodeId,
      setActiveRunId,
      setActiveRunFlowId,
      setArtifactContent,
      setArtifacts,
      setGeneratedScript,
      setQueueStats,
      setRuntimeStatus,
      setRuns,
      setSiteAnalysis,
      setVariables,
      setLogs,
      setConfirmationMessage,
    }),
    [
      activeFlowNameRef,
      activeRunId,
      callBridge,
      currentFlow,
      flowCanvas,
      flows,
      pushToast,
      inputVariables,
      resetRunView,
      setLastRunOverrides,
      setFlowNodes,
      setSelectedNodeId,
      setActiveRunId,
      setActiveRunFlowId,
      setArtifactContent,
      setArtifacts,
      setGeneratedScript,
      setQueueStats,
      setRuntimeStatus,
      setRuns,
      setSiteAnalysis,
      setVariables,
      setLogs,
      setConfirmationMessage,
    ],
  );
  const scheduleBridgeActions = useMemo(
    () => createScheduleBridgeActions({
      callBridge,
      currentFlow,
      flowCanvas,
      flows,
      pushToast,
      inputVariables,
      setCurrentFlow,
      setFlows,
      setActiveRunId,
      setActiveRunFlowId,
      setRuntimeStatus,
      setSchedules,
      setScheduleRunSummaries,
    }),
    [
      callBridge,
      currentFlow,
      flowCanvas,
      flows,
      pushToast,
      inputVariables,
      setCurrentFlow,
      setFlows,
      setActiveRunId,
      setActiveRunFlowId,
      setRuntimeStatus,
      setSchedules,
      setScheduleRunSummaries,
    ],
  );
  return useMemo(() => ({
    ...flowBridgeActions,
    ...runBridgeActions,
    ...scheduleBridgeActions,
    openArtifactPath: async (storageUrl: string) => {
      // storageUrl 是 Python Path.as_uri() 产生的 file:// URI
      let artifactsDir: string | null = null;
      try {
        const url = new URL(storageUrl);
        if (url.protocol === 'file:') {
          // Windows 下 pathname 以 /C:/... 开头，需去掉前导斜杠
          const raw = decodeURIComponent(url.pathname);
          const normalized = /^\/[A-Za-z]:\//.test(raw) ? raw.slice(1) : raw;
          artifactsDir = normalized.replace(/[/\\][^/\\]+$/, '');
        }
      } catch { /* ignore invalid artifact URL */ }
      if (artifactsDir === null) {
        pushToast('error', `无法解析产物路径：${storageUrl}`);
        return;
      }
      await callBridge((api) => api.showInFinder(artifactsDir));
    },
    exportLogs: async (content: string) => {
      const ts = new Date().toISOString().replace(/[-:]/g, '').replace('T', '_').slice(0, 15);
      const filename = `${toSafeFilename(currentFlow?.name)}_${ts}_运行日志.log`;
      const result = await callBridge((api) => api.exportLogs({ content, filename }));
      if (result !== null && !result.canceled && result.name !== undefined) {
        pushToast('success', `已导出 ${result.name}`);
      }
    },
    openPicker: async (payload: PickerOpenPayload) => {
      if (payload.mode === 'browse') {
        await callBridge((api) => api.openPicker(payload));
        return;
      }
      setActivePickerRequest(payload);
      const opened = await callBridge((api) => api.openPicker(payload), '元素拾取器已启动');
      if (opened === null) {
        setActivePickerRequest((current) => current?.requestId === payload.requestId ? null : current);
      }
    },
    closePicker: async (requestId: string) => {
      const result = await callBridge((api) => api.closePicker({ requestId }));
      if (result !== null) {
        setActivePickerRequest((current) => current?.requestId === requestId ? null : current);
      }
    },
    minimizeWindow: async () => {
      await callBridge((api) => api.minimizeWindow());
    },
    toggleMaximizeWindow: async () => {
      await callBridge((api) => api.toggleMaximizeWindow());
    },
    closeWindow: async () => {
      await callBridge((api) => api.closeWindow());
    }
  }), [flowBridgeActions, runBridgeActions, scheduleBridgeActions, callBridge, currentFlow, pushToast, setActivePickerRequest]);
}
