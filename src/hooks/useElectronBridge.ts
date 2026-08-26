import type { Dispatch, SetStateAction } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Edge, Node } from '@xyflow/react';

import { initialProgress } from './electronBridgeConstants';
import type { BridgeCallOptions, BridgeToast, DebugControlCommand, ElectronBridgeState } from './electronBridgeTypes';
import { useElectronBridgeActions } from './useElectronBridgeActions';
import { useRunEventHandler } from './useRunEventHandler';
import { createBrowserBridge } from '../lib/browserBridge';
import { buildRuntimeVariableViews } from '../lib/runtimeVariables';
import { useFlowVariableStore } from '../stores/useFlowVariableStore';
import { LOCAL_FLOW_KEY, useRunConfigStore } from '../stores/useRunConfigStore';
import { useBottomPanelStore } from '../stores/useBottomPanelStore';
import { useWorkspaceStore } from '../stores/useWorkspaceStore';
import type {
  AppInfo,
  ArtifactContent,
  ArtifactSnapshot,
  BackendServiceStatus,
  FlowSnapshot,
  BridgeResult,
  GeneratedScriptResult,
  PickerResult,
  QueueStats,
  RpaBridge,
  ScheduleSnapshot,
  SiteAnalysisResult,
  TaskSnapshot
} from '../types/electron';
import type { FlowCanvasSnapshot, NodeRuntimeState, RpaNodeData, RunLogEntry, RuntimeProgress, RuntimeStatus, RuntimeVariable } from '../types/rpa';

export type { BridgeToast, ElectronBridgeState } from './electronBridgeTypes';

const MAX_VISIBLE_TOASTS = 3;
// 同类型+同文案的 toast 在此窗口内去重，避免刷屏
const TOAST_DEDUPE_WINDOW_MS = 2_000;

export function useElectronBridge({
  edges,
  nodes,
  setSelectedNodeId,
  setEdges,
  setNodes
}: FlowCanvasSnapshot & {
  setSelectedNodeId?: Dispatch<SetStateAction<string>>;
  setEdges: Dispatch<SetStateAction<Edge[]>>;
  setNodes: Dispatch<SetStateAction<Node<RpaNodeData>[]>>;
}): ElectronBridgeState {
  const browserBridge = useMemo(() => (typeof window === 'undefined' ? undefined : createBrowserBridge()), []);
  const bridge = typeof window === 'undefined' ? undefined : window.rpaBridge ?? browserBridge;
  const [appInfo, setAppInfo] = useState<AppInfo | null>(null);
  const [backendStatus, setBackendStatus] = useState<BackendServiceStatus | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [artifactContent, setArtifactContent] = useState<ArtifactContent | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactSnapshot[]>([]);
  const [generatedScript, setGeneratedScript] = useState<GeneratedScriptResult | null>(null);
  const [flows, setFlows] = useState<FlowSnapshot[]>([]);
  const [currentFlow, setCurrentFlow] = useState<FlowSnapshot | null>(null);
  const [siteAnalysis, setSiteAnalysis] = useState<SiteAnalysisResult | null>(null);
  const [windowId, setWindowId] = useState<number | null>(null);
  const [lastPickerResult, setLastPickerResult] = useState<PickerResult | null>(null);
  const [pickerActive, setPickerActive] = useState(false);
  const [activeRunFlowId, setActiveRunFlowId] = useState<string | null>(null);
  const [lastRunId, setLastRunId] = useState<string | null>(null);
  const [logs, setLogs] = useState<RunLogEntry[]>([]);
  const [canvasFitVersion, setCanvasFitVersion] = useState(0);
  const [nodeStates, setNodeStates] = useState<Record<string, NodeRuntimeState>>({});
  const [progress, setProgress] = useState<RuntimeProgress>(initialProgress);
  const [inputPrompt, setInputPrompt] = useState<string | null>(null);
  const [humanTakeoverMessage, setHumanTakeoverMessage] = useState<string | null>(null);
  const [pausedPageUrl, setPausedPageUrl] = useState<string | null>(null);
  const [queueStats, setQueueStats] = useState<QueueStats | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus>('ready');
  const [runs, setRuns] = useState<TaskSnapshot[]>([]);
  const [schedules, setSchedules] = useState<ScheduleSnapshot[]>([]);
  const [variables, setVariables] = useState<RuntimeVariable[]>([]);
  const [toasts, setToasts] = useState<BridgeToast[]>([]);
  const toastIdRef = useRef(0);
  const activeRunIdRef = useRef<string | null>(null);
  const lastRunIdRef = useRef<string | null>(null);
  const inputVariables = useFlowVariableStore((state) => state.inputVariables);
  const replaceAllInputVariables = useFlowVariableStore((state) => state.replaceAllInputVariables);
  const clearLastRunOverrides = useRunConfigStore((state) => state.clearLastRunOverrides);
  const lastRunOverrideVariables = useRunConfigStore((state) => state.lastRunOverrideVariables);
  const overrideFlowKey = useRunConfigStore((state) => state.flowKey);
  const setLastRunOverrides = useRunConfigStore((state) => state.setLastRunOverrides);
  const setLastOpenedFlowId = useWorkspaceStore((state) => state.setLastOpenedFlowId);

  const pushToast = useCallback((type: BridgeToast['type'], message: string): number => {
    const now = Date.now();
    let assignedId = 0;
    setToasts((current) => {
      const duplicate = current.find((toast) => toast.type === type && toast.message === message);
      if (duplicate !== undefined && now - duplicate.id < TOAST_DEDUPE_WINDOW_MS) {
        assignedId = duplicate.id;
        return current;
      }
      toastIdRef.current += 1;
      assignedId = now + toastIdRef.current;
      return [
        { id: assignedId, type, message },
        ...current.filter((toast) => toast.type !== type || toast.message !== message)
      ].slice(0, MAX_VISIBLE_TOASTS);
    });
    return assignedId;
  }, []);

  const dismissToast = useCallback((toastId: number): void => {
    setToasts((current) => current.filter((toast) => toast.id !== toastId));
  }, []);

  const unwrap = useCallback(
    <T,>(result: BridgeResult<T>, successMessage?: string, options?: BridgeCallOptions): T | null => {
      if (!result.ok) {
        if (options?.silent !== true) {
          pushToast('error', result.error ?? 'Electron 操作失败');
        }
        return null;
      }
      if (successMessage !== undefined && options?.silent !== true) {
        pushToast('success', successMessage);
      }
      return result.data ?? null;
    },
    [pushToast]
  );

  const callBridge = useCallback(
    async <T,>(action: (bridge: RpaBridge) => Promise<BridgeResult<T>>, successMessage?: string, options?: BridgeCallOptions): Promise<T | null> => {
      if (bridge === undefined) {
        if (options?.silent !== true) {
          pushToast('info', '当前桥接服务不可用，请确认桌面端或后端服务已启动');
        }
        return null;
      }
      const result = await action(bridge);
      return unwrap(result, successMessage, options);
    },
    [bridge, pushToast, unwrap]
  );

  const debugControl = useCallback(
    (command: DebugControlCommand): void => {
      if (runtimeStatus !== 'running') {
        pushToast('info', '调试命令需要在运行中使用');
        return;
      }
      if (activeRunId === null) {
        pushToast('info', '当前没有活跃运行任务');
        return;
      }
      const commandLabel = getDebugCommandLabel(command);
      void callBridge((api) => api.debugRun(activeRunId, command)).then((result) => {
        if (result === null) {
          return;
        }
        const timestamp = formatLogTime(new Date());
        const logEntry: RunLogEntry = {
          id: `debug-${command}-${Date.now()}`,
          level: 'running',
          message: `调试控制 · ${commandLabel}`,
          time: timestamp
        };
        setLogs((current) => [...current, logEntry].slice(-200));
        setVariables((current) => [
          ...current.filter((variable) => variable.name !== 'debug_command'),
          { name: 'debug_command', scope: '局部', type: 'String', value: commandLabel }
        ]);
        pushToast('info', `已发送调试命令：${commandLabel}`);
      });
    },
    [activeRunId, callBridge, pushToast, runtimeStatus]
  );

  const setBottomPanelOpen = useBottomPanelStore((state) => state.setOpen);

  useEffect(() => {
    activeRunIdRef.current = activeRunId;
    lastRunIdRef.current = lastRunId;
  }, [activeRunId, lastRunId]);

  const onArtifactsReady = useCallback(() => {
    setBottomPanelOpen(true);
  }, [setBottomPanelOpen]);

  const resetRunView = useCallback((): void => {
    setActiveRunId(null);
    setActiveRunFlowId(null);
    setLastRunId(null);
    setArtifactContent(null);
    setArtifacts([]);
    setGeneratedScript(null);
    setInputPrompt(null);
    setHumanTakeoverMessage(null);
    setPausedPageUrl(null);
    setLogs([]);
    setNodeStates({});
    setProgress(initialProgress);
    setRuntimeStatus('ready');
    setVariables([]);
  }, []);

  // startRun 前由 useElectronBridgeActions 写入，结束时供 useRunEventHandler 读取
  const activeFlowNameRef = useRef<string>('');

  const applyRunEvent = useRunEventHandler({
    activeRunIdRef,
    activeFlowNameRef,
    callBridge,
    lastRunIdRef,
    onArtifactsReady,
    pushToast,
    setActiveRunId,
    setActiveRunFlowId,
    setLastRunId,
    setArtifactContent,
    setArtifacts,
    setFlows,
    setGeneratedScript,
    setInputPrompt,
    setHumanTakeoverMessage,
    setPausedPageUrl,
    setLogs,
    setNodeStates,
    setProgress,
    setRuntimeStatus,
    setVariables
  });
  const flowCanvas = useMemo(() => ({ edges, nodes }), [edges, nodes]);

  const actions = useElectronBridgeActions({
    activeFlowNameRef,
    activeRunId,
    activeRunFlowId,
    callBridge,
    clearLastRunOverrides,
    currentFlow,
    flowCanvas,
    flows,
    lastPickerResult,
    pushToast,
    dismissToast,
    inputVariables,
    runtimeVariables: variables,
    resetRunView,
    setLastRunOverrides,
    setCurrentFlow,
    setFlowEdges: setEdges,
    setFlowNodes: setNodes,
    setFlows,
    setInputVariables: replaceAllInputVariables,
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
    setSiteAnalysis,
    setVariables,
    setLogs,
    setInputPrompt,
    setHumanTakeoverMessage,
    setPickerActive,
    setCanvasFitVersion
  });

  // 故意不依赖 currentFlow.inputVariables：AI 改完节点调 setCurrentFlow() 会覆盖用户未保存的本地编辑
  useEffect(() => {
    if (currentFlow?.flowId != null) {
      replaceAllInputVariables(currentFlow.inputVariables ?? []);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFlow?.flowId]);

  useEffect(() => {
    if (currentFlow?.flowId != null) {
      setLastOpenedFlowId(currentFlow.flowId);
    }
  }, [currentFlow?.flowId, setLastOpenedFlowId]);

  useEffect(() => {
    const activeFlowKey = currentFlow?.flowId ?? LOCAL_FLOW_KEY;
    if (overrideFlowKey !== null && overrideFlowKey !== activeFlowKey) {
      clearLastRunOverrides();
    }
  }, [clearLastRunOverrides, currentFlow?.flowId, overrideFlowKey]);

  // 仅当覆盖值属于当前打开的流程时才生效，过期覆盖丢弃
  const variableViews = useMemo(
    () => buildRuntimeVariableViews(inputVariables, overrideFlowKey === (currentFlow?.flowId ?? LOCAL_FLOW_KEY) ? lastRunOverrideVariables : [], variables),
    [currentFlow?.flowId, inputVariables, lastRunOverrideVariables, overrideFlowKey, variables]
  );

  useEffect(() => {
    if (bridge === undefined) {
      return;
    }
    void bridge.getBackendStatus().then((result) => {
      if (result.ok && result.data !== undefined) {
        setBackendStatus(result.data);
      }
    });
    void bridge.getAppVersion().then((result) => {
      if (result.ok && result.data !== undefined) {
        setAppInfo(result.data);
      }
    });
    void bridge.getWindowId().then((result) => {
      if (result.ok && result.data !== undefined) {
        setWindowId(result.data);
      }
    });
    const unsubscribePicker = bridge.onPickerResult((result) => {
      setLastPickerResult(result);
      setPickerActive(false);
      pushToast('success', `已捕获选择器 ${result.selector}`);
    });
    const unsubscribePickerCancel = bridge.onPickerCancel(() => {
      setPickerActive(false);
    });
    const unsubscribeRun = bridge.onRunEvent((event) => {
      applyRunEvent(event);
    });
    const unsubscribeBackend = bridge.onBackendStatusChanged((status) => {
      setBackendStatus(status);
    });
    return () => {
      unsubscribePicker();
      unsubscribePickerCancel();
      unsubscribeRun();
      unsubscribeBackend();
    };
  }, [applyRunEvent, bridge, pushToast]);

  return useMemo(
    () => ({
      available: bridge !== undefined,
      appInfo,
      backendStatus,
      artifactContent,
      artifacts,
      generatedScript,
      flows,
      currentFlow,
      siteAnalysis,
      windowId,
      lastPickerResult,
      pickerActive,
      inputPrompt,
      humanTakeoverMessage,
      pausedPageUrl,
      activeRunFlowId,
      lastRunId,
      logs,
      canvasFitVersion,
      nodeStates,
      progress,
      queueStats,
      runtimeStatus,
      runs,
      schedules,
      toasts,
      inputVariables,
      lastRunOverrideVariables,
      variableViews,
      variables,
      ...actions,
      debugControl,
      pushToast,
      dismissToast,
      refreshBackendStatus: async () => {
        if (bridge === undefined) {
          pushToast('info', '当前桥接服务不可用，请确认桌面端已启动');
          return;
        }
        const result = await bridge.getBackendStatus();
        if (result.ok && result.data !== undefined) {
          setBackendStatus(result.data);
        }
      },
      restartBackend: async () => {
        if (bridge === undefined) {
          pushToast('info', '当前桥接服务不可用，请确认桌面端已启动');
          return;
        }
        const result = await bridge.restartBackend();
        if (!result.ok) {
          pushToast('error', result.error ?? '后端重启失败');
          return;
        }
        if (result.data !== undefined) {
          setBackendStatus(result.data);
          pushToast(result.data.status === 'ready' ? 'success' : 'info', result.data.status === 'ready' ? '后端服务已就绪' : '后端服务状态已更新');
        }
      },
      clearToast: () => setToasts([]),
      clearRuns: () => setRuns([])
    }),
    [
      actions,
      appInfo,
      backendStatus,
      artifactContent,
      artifacts,
      bridge,
      generatedScript,
      flows,
      currentFlow,
      siteAnalysis,
      inputPrompt,
      activeRunFlowId,
      lastPickerResult,
      lastRunId,
      pickerActive,
      humanTakeoverMessage,
      pausedPageUrl,
      logs,
      canvasFitVersion,
      nodeStates,
      progress,
      queueStats,
      runtimeStatus,
      runs,
      schedules,
      toasts,
      inputVariables,
      lastRunOverrideVariables,
      variableViews,
      variables,
      windowId,
      debugControl,
      dismissToast,
      pushToast
    ]
  );
}

function getDebugCommandLabel(command: DebugControlCommand): string {
  const labels: Record<DebugControlCommand, string> = {
    continue: '继续执行',
    'step-into': '单步进入',
    'step-over': '单步越过'
  };
  return labels[command];
}

function formatLogTime(date: Date): string {
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}.${String(date.getMilliseconds()).padStart(3, '0')}`;
}
