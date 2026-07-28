import type { Dispatch, SetStateAction } from 'react';
import type { Edge, Node } from '@xyflow/react';
import { useMemo } from 'react';

import type { BridgeCallOptions, BridgeToast } from './electronBridgeTypes';
import { initialEdges, initialNodes } from '../data/studioData';
import { buildFlowDefinition, readFlowInputVariables, restoreFlowCanvas, serializeFlowDefinition } from '../lib/flowDefinition';
import { cloneFlowTemplate, type FlowTemplate } from '../lib/flowTemplates';
import { isSafeVariableName } from '../lib/variableNaming';
import { getBlockingRunIssue, validateRunConfiguration } from '../lib/runValidation';
import { useBottomPanelStore } from '../stores/useBottomPanelStore';
import { usePropertyPanelStore } from '../stores/usePropertyPanelStore';
import { applyPendingDraftToNodes } from '../lib/pendingNodeDraft';
import { buildInitialFlowPayload, buildUpdatePayload, hasDefinitionChanged } from '../lib/flowVersioning';
import type {
  ArtifactContent,
  ArtifactSnapshot,
  BridgeResult,
  FlowSnapshot,
  GeneratedScriptResult,
  PickerResult,
  QueueStats,
  RpaBridge,
  BrowserExecutorKind,
  RunFailureStrategy,
  RunMode,
  RunScope,
  ScheduleSnapshot,
  SiteAnalysisResult,
  TaskSnapshot
} from '../types/electron';
import type { FlowCanvasSnapshot, RpaNodeData, RunLogEntry, RuntimeStatus, RuntimeVariable } from '../types/rpa';
import { normalizeRunConcurrency } from '../lib/runConfigPresentation';
import { clearDraftStorage } from './useFlowDraftAutosave';
import { fetchFlowSnapshot } from '../lib/backendClient';
import { toSafeFilename } from '../lib/filenames';
type UseElectronBridgeActionsParams = {
  activeFlowNameRef: import('react').MutableRefObject<string>;
  activeRunId: string | null;
  activeRunFlowId: string | null;
  callBridge: <T>(action: (bridge: RpaBridge) => Promise<BridgeResult<T>>, successMessage?: string, options?: BridgeCallOptions) => Promise<T | null>;
  clearLastRunOverrides: () => void;
  currentFlow: FlowSnapshot | null;
  flowCanvas: FlowCanvasSnapshot;
  flows: FlowSnapshot[];
  lastPickerResult: PickerResult | null;
  pushToast: (type: BridgeToast['type'], message: string, icon?: string) => number;
  dismissToast: (toastId: number) => void;
  inputVariables: RuntimeVariable[];
  runtimeVariables: RuntimeVariable[];
  resetRunView: () => void;
  setLastRunOverrides: (flowKey: string | null, variables: RuntimeVariable[]) => void;
  setCurrentFlow: Dispatch<SetStateAction<FlowSnapshot | null>>;
  setFlowEdges: Dispatch<SetStateAction<Edge[]>>;
  setFlowNodes: Dispatch<SetStateAction<Node<RpaNodeData>[]>>;
  setFlows: Dispatch<SetStateAction<FlowSnapshot[]>>;
  setInputVariables: (variables: RuntimeVariable[]) => void;
  setSelectedNodeId?: Dispatch<SetStateAction<string>>;
  setActiveRunId: Dispatch<SetStateAction<string | null>>;
  setActiveRunFlowId: Dispatch<SetStateAction<string | null>>;
  setArtifactContent: Dispatch<SetStateAction<ArtifactContent | null>>;
  setArtifacts: Dispatch<SetStateAction<ArtifactSnapshot[]>>;
  setGeneratedScript: Dispatch<SetStateAction<GeneratedScriptResult | null>>;
  setQueueStats: Dispatch<SetStateAction<QueueStats | null>>;
  setRuntimeStatus: Dispatch<SetStateAction<RuntimeStatus>>;
  setRuns: Dispatch<SetStateAction<TaskSnapshot[]>>;
  setSchedules: Dispatch<SetStateAction<ScheduleSnapshot[]>>;
  setSiteAnalysis: Dispatch<SetStateAction<SiteAnalysisResult | null>>;
  setVariables: Dispatch<SetStateAction<RuntimeVariable[]>>;
  setLogs: Dispatch<SetStateAction<RunLogEntry[]>>;
  setInputPrompt: Dispatch<SetStateAction<string | null>>;
  setHumanTakeoverMessage: Dispatch<SetStateAction<string | null>>;
  setPickerActive: Dispatch<SetStateAction<boolean>>;
  setCanvasFitVersion: Dispatch<SetStateAction<number>>;
};

export type ElectronBridgeActions = {
  openArtifactPath: (storageUrl: string) => Promise<void>;
  openFlow: () => Promise<boolean>;
  applyFlowTemplate: (template: FlowTemplate) => void;
  openFlowById: (flowId: string) => Promise<void>;
  /** 静默恢复 currentFlow；restoreCanvas: true 时同时应用到画布（启动时无草稿场景使用） */
  silentlyRestoreCurrentFlow: (flowId: string, options?: { restoreCanvas?: boolean }) => Promise<void>;
  /** AI 更新流程后的轻量画布刷新：不清运行日志/草稿，不弹 toast */
  applyAiFlowUpdate: (flowId: string) => Promise<void>;
  rollbackFlowById: (flowId: string) => Promise<void>;
  saveFlow: () => Promise<void>;
  exportFlow: () => Promise<void>;
  exportFlowById: (flowId: string) => Promise<void>;
  createNewFlow: (name?: string) => Promise<void>;
  loadFlows: (options?: BridgeCallOptions) => Promise<void>;
  archiveCurrentFlow: () => Promise<void>;
  archiveFlowById: (flowId: string) => Promise<void>;
  duplicateFlowById: (flowId: string) => Promise<void>;
  moveFlowById: (flowId: string, folderPath: string) => Promise<void>;
  setFlowStatusById: (flowId: string, status: import('../types/electron').FlowStatus) => Promise<void>;
  deleteCurrentFlow: () => Promise<void>;
  deleteFlowById: (flowId: string) => Promise<void>;
  renameCurrentFlow: (name: string) => Promise<void>;
  setDefaultBrowserExecutor: (browserExecutor: BrowserExecutorKind) => Promise<void>;
  exportLogs: (content: string) => Promise<void>;
  openPicker: (targetUrl?: string) => Promise<void>;
  closePicker: () => Promise<void>;
  startRun: (options?: RunMode | StartRunOptions) => Promise<void>;
  stopRun: () => Promise<void>;
  provideInput: (value: string) => Promise<void>;
  resumeHumanTakeover: (resumeMode: string) => Promise<void>;
  generateScraplingScript: () => Promise<void>;
  analyzeCurrentSite: () => Promise<void>;
  loadRuns: (options?: { flowId?: string; limit?: number } & BridgeCallOptions) => Promise<void>;
  loadFlowRuns: (flowId: string, options?: { limit?: number } & BridgeCallOptions) => Promise<void>;
  loadTaskVariables: (taskId: string) => Promise<void>;
  loadArtifacts: (taskId: string) => Promise<void>;
  readArtifact: (taskId: string, artifactId: string) => Promise<void>;
  loadQueueStats: (options?: BridgeCallOptions) => Promise<void>;
  loadSchedules: (options?: BridgeCallOptions) => Promise<void>;
  createDefaultSchedule: (options?: CreateScheduleOptions) => Promise<void>;
  createScheduleForFlow: (flowId: string, options?: CreateScheduleOptions) => Promise<void>;
  updateScheduleEnabled: (scheduleId: string, enabled: boolean) => Promise<void>;
  updateSchedule: (scheduleId: string, options: CreateScheduleOptions) => Promise<void>;
  deleteSchedule: (scheduleId: string) => Promise<void>;
  triggerSchedule: (scheduleId: string) => Promise<void>;
  minimizeWindow: () => Promise<void>;
  toggleMaximizeWindow: () => Promise<void>;
  closeWindow: () => Promise<void>;
};

export type StartRunOptions = {
  browserExecutor?: BrowserExecutorKind;
  concurrency?: number;
  failureStrategy?: RunFailureStrategy;
  flowId?: string;
  mode?: RunMode;
  overrideVariables?: RuntimeVariable[];
  scope?: RunScope;
  screenshot?: boolean;
  startNodeId?: string;
  timeoutMs?: number;
};

export type CreateScheduleOptions = {
  browserExecutor?: BrowserExecutorKind;
  cronExpression?: string;
  enabled?: boolean;
  flowId?: string;
  name?: string;
  timezone?: string;
};

/** 有则替换、无则置顶插入。后端返回的快照总是比列表里的新，直接覆盖。 */
function upsertFlow(flow: FlowSnapshot): (prev: FlowSnapshot[]) => FlowSnapshot[] {
  return (prev) => {
    const idx = prev.findIndex((f) => f.flowId === flow.flowId);
    if (idx < 0) return [flow, ...prev];
    const next = [...prev];
    next[idx] = flow;
    return next;
  };
}

export function useElectronBridgeActions({
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
  resetRunView,
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
  setSiteAnalysis,
  setVariables,
  setLogs,
  setInputPrompt,
  setHumanTakeoverMessage,
  setPickerActive,
  setCanvasFitVersion
}: UseElectronBridgeActionsParams): ElectronBridgeActions {
  return useMemo(
    () => ({
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
      applyFlowTemplate: (template: FlowTemplate) => {
        const snapshot = cloneFlowTemplate(template);
        clearDraftStorage();
        resetRunView();
        setCurrentFlow(null);
        setFlowNodes(snapshot.nodes);
        setFlowEdges(snapshot.edges);
        setInputVariables(snapshot.variables);
        clearLastRunOverrides();
        if (typeof setSelectedNodeId === 'function') {
          setSelectedNodeId(snapshot.nodes.find((node) => node.id !== 'start' && node.id !== 'end')?.id ?? 'start');
        }
        pushToast('info', `已应用场景模板：${template.name}`);
      },
      openFlow: async (): Promise<boolean> => {
        const fileResult = await callBridge((api) => api.openFlow());
        if (fileResult === null || fileResult.canceled || fileResult.name === undefined) {
          return false;
        }
        const rawContent = typeof fileResult.content === 'string' ? fileResult.content.trim() : '';
        if (!rawContent) {
          pushToast('error', '文件内容为空');
          return false;
        }
        let definition: Record<string, unknown>;
        try {
          definition = JSON.parse(rawContent) as Record<string, unknown>;
        } catch {
          pushToast('error', '流程文件不是有效 JSON');
          return false;
        }

        // 清除旧草稿，避免页面重新挂载时恢复之前的流程
        clearDraftStorage();
        resetRunView();

        applyFlowDefinitionToCanvas(definition, setFlowNodes, setFlowEdges);
        const parsedVars = readFlowInputVariables(definition);
        setInputVariables(parsedVars);
        clearLastRunOverrides();

        const flowName = typeof definition.name === 'string' && definition.name.trim()
          ? definition.name.trim()
          : fileResult.name.replace(/\.rpa\.json$/i, '').replace(/\.json$/i, '') || '导入流程';
        const flowVersion = typeof definition.version === 'string' ? definition.version : 'v1.0.0';

        const loadingToastId = pushToast('info', '正在导入流程...');
        const saved = await callBridge(
          (api) =>
            api.createFlow({
              definition,
              folderPath: '默认目录',
              inputVariables: parsedVars,
              name: flowName,
              status: 'draft',
              version: flowVersion,
            }),
          undefined,
          { silent: true }
        );
        dismissToast(loadingToastId);
        if (saved !== null) {
          setCurrentFlow(saved);
          setFlows((current) => [saved, ...current.filter((f) => f.flowId !== saved.flowId)]);
          pushToast('success', `已导入并保存「${flowName}」`);
        } else {
          // 后端不可用时仍应用到画布，不阻断使用
          setCurrentFlow(null);
          pushToast('info', `已打开 ${fileResult.name}（未连接后端，仅本地使用）`);
        }
        return true;
      },
      openFlowById: async (flowId: string) => {
        // 直接取单个流程，避免全量 listFlows 往返及其偶发的 500 错误
        let target = await fetchFlowSnapshot(flowId);

        if (target === null) {
          const flows = await callBridge((api) => api.listFlows());
          if (flows === null) return;
          setFlows(flows);
          target = flows.find((flow) => flow.flowId === flowId) ?? null;
          if (target === null) {
            pushToast('error', '未找到指定流程版本');
            return;
          }
        } else {
          setFlows(upsertFlow(target));
        }

        if (!(activeRunId !== null && activeRunFlowId === flowId)) {
          resetRunView();
        }
        clearDraftStorage();
        openFlowSnapshot(target, { pushToast, setCurrentFlow, setFlowEdges, setFlowNodes, setInputVariables });
      },
      silentlyRestoreCurrentFlow: async (flowId: string, options?: { restoreCanvas?: boolean }) => {
        const flow = await fetchFlowSnapshot(flowId);
        if (flow === null) return;
        setCurrentFlow(flow);
        if (options?.restoreCanvas === true) {
          applyFlowDefinitionToCanvas(flow.definition, setFlowNodes, setFlowEdges);
          setInputVariables(flow.inputVariables);
          setCanvasFitVersion((v) => v + 1);
        }
      },
      applyAiFlowUpdate: async (flowId: string) => {
        const flow = await fetchFlowSnapshot(flowId);
        if (flow === null) return; // 取不到时画布保持原状
        // 故意不调用 setInputVariables：AI 工具只改节点/边，调用它会清掉用户未保存的本地变量编辑
        setCurrentFlow(flow);
        setFlows(upsertFlow(flow));
        applyFlowDefinitionToCanvas(flow.definition, setFlowNodes, setFlowEdges);
      },
      rollbackFlowById: async (savedAt: string) => {
        if (currentFlow === null) return;
        // 按 savedAt 查找而非 version：AI/工具编辑从不改 version，同流程快照几乎共享同一个值，
        // 用它查找永远命中数组里第一个同 version 的快照，回退到错误版本
        const snapshot = currentFlow.snapshots.find((s) => s.savedAt === savedAt)
          ?? currentFlow.snapshots[0];
        if (snapshot === undefined) {
          pushToast('error', '未找到可回退的版本快照');
          return;
        }
        const updated = await callBridge((api) => api.updateFlow(currentFlow.flowId, {
          definition: snapshot.definition,
          inputVariables: snapshot.inputVariables,
        }));
        if (updated === null) return;
        clearDraftStorage();
        resetRunView();
        setCurrentFlow(updated);
        clearLastRunOverrides();
        setFlows((prev) => [updated, ...prev.filter((f) => f.flowId !== updated.flowId)]);
        applyFlowDefinitionToCanvas(updated.definition, setFlowNodes, setFlowEdges);
        setInputVariables(updated.inputVariables);
        pushToast('success', `已回退为 ${snapshot.version}`);
      },
      loadFlows: async (options?: BridgeCallOptions) => {
        const flows = await callBridge((api) => api.listFlows(), undefined, options);
        if (flows !== null) {
          setFlows(flows);
        }
      },
      createNewFlow: async (name?: string) => {
        const flowName = typeof name === 'string' && name.trim() ? name.trim() : '新建 RPA 流程';
        clearDraftStorage();
        resetRunView();
        setCurrentFlow(createLocalDraftFlow(flowName));
        setFlowNodes(restoreInitialNodes());
        setFlowEdges(restoreInitialEdges());
        clearLastRunOverrides();
        pushToast('info', `已创建草稿：${flowName}`);
      },
      saveFlow: async () => {
        const flow = await persistCurrentFlow({ callBridge, currentFlow, flows, flowCanvas, inputVariables });
        if (flow !== null) {
          setCurrentFlow(flow);
          setFlows((current) => [flow, ...current.filter((item) => item.flowId !== flow.flowId)]);
          pushToast('success', `已保存流程 ${flow.name}`);
        }
      },
      exportFlow: async () => {
        const suggestedName = `${toSafeFilename(currentFlow?.name)}.rpa.json`;
        const fileResult = await callBridge((api) =>
          api.saveFlow({ suggestedName, content: serializeFlowDefinition(flowCanvas.nodes, flowCanvas.edges, inputVariables, currentFlow?.name) })
        );
        if (fileResult !== null && !fileResult.canceled && fileResult.name !== undefined) {
          pushToast('success', `已导出 ${fileResult.name}`);
        }
      },
      exportFlowById: async (flowId: string) => {
        // 优先从已加载列表取，避免 open flow（会替换 currentFlow/画布状态）及 stale closure
        const flow = flows.find((f) => f.flowId === flowId) ?? await fetchFlowSnapshot(flowId);
        if (flow === null || flow === undefined) {
          pushToast('error', '未找到指定流程');
          return;
        }
        const suggestedName = `${toSafeFilename(flow.name)}.rpa.json`;
        const content = JSON.stringify(flow.definition, null, 2);
        const fileResult = await callBridge((api) => api.saveFlow({ suggestedName, content }));
        if (fileResult !== null && !fileResult.canceled && fileResult.name !== undefined) {
          pushToast('success', `已导出 ${fileResult.name}`);
        }
      },
      archiveCurrentFlow: async () => {
        if (currentFlow === null) {
          pushToast('error', '当前流程尚未保存，无法归档');
          return;
        }
        const archived = await callBridge((api) => api.archiveFlow(currentFlow.flowId));
        if (archived !== null) {
          setCurrentFlow(archived);
          setFlows((current) => [archived, ...current.filter((item) => item.flowId !== archived.flowId)]);
          pushToast('success', `已归档 ${archived.name}`);
        }
      },
      archiveFlowById: async (flowId: string) => {
        const archived = await callBridge((api) => api.archiveFlow(flowId));
        if (archived !== null) {
          setFlows((current) => [archived, ...current.filter((item) => item.flowId !== archived.flowId)]);
          if (currentFlow?.flowId === archived.flowId) {
            setCurrentFlow(archived);
          }
          pushToast('success', `已归档 ${archived.name}`);
        }
      },
      duplicateFlowById: async (flowId: string) => {
        const copy = await callBridge((api) => api.duplicateFlow(flowId), '已创建副本');
        if (copy !== null) {
          setFlows((current) => [copy, ...current]);
        }
      },
      moveFlowById: async (flowId: string, folderPath: string) => {
        const moved = await callBridge((api) => api.moveFlow(flowId, folderPath), `已移动到 ${folderPath}`);
        if (moved !== null) {
          setFlows((current) => current.map((item) => (item.flowId === flowId ? moved : item)));
          if (currentFlow?.flowId === flowId) {
            setCurrentFlow(moved);
          }
        }
      },
      setFlowStatusById: async (flowId: string, status: import('../types/electron').FlowStatus) => {
        const updated = await callBridge((api) => api.setFlowStatus(flowId, status));
        if (updated !== null) {
          setFlows((current) => current.map((item) => (item.flowId === flowId ? updated : item)));
          if (currentFlow?.flowId === flowId) {
            setCurrentFlow(updated);
          }
          pushToast('success', `流程状态已更新`);
        }
      },
      deleteCurrentFlow: async () => {
        if (currentFlow === null) {
          pushToast('error', '当前流程尚未保存，无法删除');
          return;
        }
        const flowId = currentFlow.flowId;
        const result = await callBridge((api) => api.deleteFlow(flowId));
        if (result !== null && result.deleted) {
          resetRunView();
          setCurrentFlow(null);
          setFlows((current) => current.filter((item) => item.flowId !== flowId));
          pushToast('success', '流程版本已删除');
        }
      },
      deleteFlowById: async (flowId: string) => {
        const result = await callBridge((api) => api.deleteFlow(flowId));
        if (result !== null && result.deleted) {
          if (currentFlow?.flowId === flowId) {
            resetRunView();
            setCurrentFlow(null);
          }
          setFlows((current) => current.filter((item) => item.flowId !== flowId));
          pushToast('success', '流程版本已删除');
        }
      },
      renameCurrentFlow: async (name: string) => {
        const trimmed = name.trim();
        if (!trimmed) return;
        if (currentFlow === null) {
          // 未保存的本地草稿，仅内存中改名
          setCurrentFlow((prev) => prev ? { ...prev, name: trimmed } : prev);
          return;
        }
        const renamed = { ...currentFlow, name: trimmed };
        const flow = await persistCurrentFlow({ callBridge, currentFlow: renamed, flows, flowCanvas, inputVariables });
        if (flow !== null) {
          setCurrentFlow(flow);
          setFlows((current) => [flow, ...current.filter((f) => f.flowId !== flow.flowId)]);
          pushToast('success', `已重命名为「${flow.name}」`);
        }
      },
      setDefaultBrowserExecutor: async (browserExecutor: BrowserExecutorKind) => {
        if (currentFlow === null || currentFlow.flowId.startsWith('local-')) {
          setCurrentFlow((prev) => (prev ? { ...prev, defaultBrowserExecutor: browserExecutor } : prev));
          return;
        }
        const flow = await callBridge((api) => api.updateFlow(currentFlow.flowId, { defaultBrowserExecutor: browserExecutor }));
        if (flow !== null) {
          setCurrentFlow(flow);
          setFlows((current) => current.map((item) => (item.flowId === flow.flowId ? flow : item)));
        }
      },
      exportLogs: async (content: string) => {
        const ts = new Date().toISOString().replace(/[-:]/g, '').replace('T', '_').slice(0, 15);
        const filename = `${toSafeFilename(currentFlow?.name)}_${ts}_运行日志.log`;
        const result = await callBridge((api) => api.exportLogs({ content, filename }));
        if (result !== null && !result.canceled && result.name !== undefined) {
          pushToast('success', `已导出 ${result.name}`);
        }
      },
      openPicker: async (targetUrl?: string, type: 'pick' | 'browse' = 'pick') => {
        const url = targetUrl?.trim() || lastPickerResult?.url || '';
        if (type === 'browse') {
          await callBridge((api) => api.openPicker({ targetUrl: url, mode: 'browse' }));
          return;
        }
        setPickerActive(true);
        await callBridge((api) => api.openPicker({ targetUrl: url, mode: 'pick' }), '元素拾取器已启动');
      },
      closePicker: async () => {
        setPickerActive(false);
        await callBridge((api) => api.closePicker(), '元素拾取器已关闭');
      },
      startRun: async (options = 'run') => {
        const runOptions: StartRunOptions = typeof options === 'string' ? { mode: options } : options;
        const mode = runOptions.mode ?? 'run';

        // 指定了已保存流程 ID 时（如来自任务中心）跳过画布校验，直接让后端运行已保存的定义
        const savedFlowId = typeof runOptions.flowId === 'string' && runOptions.flowId.length > 0
          ? runOptions.flowId
          : null;
        const isRemoteRun = savedFlowId !== null && savedFlowId !== currentFlow?.flowId;

        // 远程运行分支：目标流程不是当前打开的画布，直接让后端按已保存定义启动，
        // 不做画布校验/不拼 flowDefinition——本地画布可能是另一个流程，不能混用
        if (isRemoteRun) {
          const savedFlow = flows.find((f) => f.flowId === savedFlowId);
          const flowName = savedFlow?.name ?? '未命名流程';
          const browserExecutor = runOptions.browserExecutor ?? savedFlow?.defaultBrowserExecutor ?? 'playwright';
          activeFlowNameRef.current = flowName;
          resetRunView();
          setActiveRunFlowId(savedFlowId);
          setRuntimeStatus('running');
          const result = await callBridge((api) =>
            api.startRun({
              mode,
              adaptive: true,
              autoSave: true,
              browserExecutor,
              concurrency: normalizeRunConcurrency(runOptions.concurrency),
              failureStrategy: runOptions.failureStrategy ?? 'stop',
              flowId: savedFlowId,
              flowName,
              scope: runOptions.scope ?? 'full',
              screenshot: runOptions.screenshot ?? true,
              selector: '',
              startNodeId: runOptions.startNodeId,
              targetUrl: '',
              timeoutMs: runOptions.timeoutMs ?? 30_000,
            })
          );
          if (result !== null) {
            setActiveRunId(result.runId);
            setActiveRunFlowId(savedFlowId);
            setRuntimeStatus(result.status);
            pushToast('info', `「${flowName}」已提交运行`);
          } else {
            setActiveRunFlowId(null);
            setRuntimeStatus('ready');
          }
          return;
        }

        const canvas = commitPendingNodeDraft(flowCanvas, setFlowNodes);
        const executableNode = findExecutableFetchNode(canvas);
        const flowDefinition = buildFlowDefinition(canvas.nodes, canvas.edges, inputVariables, currentFlow?.name);
        const runVariables = mergeRunVariables(inputVariables, runOptions.overrideVariables ?? []);
        const scope = runOptions.scope ?? 'full';
        const validation = validateRunConfiguration(canvas.nodes, canvas.edges, {
          availableVariableNames: runVariables.map((variable) => variable.name),
          scope,
          startNodeId: runOptions.startNodeId
        });
        const blockingIssue = getBlockingRunIssue(validation);
        if (blockingIssue !== null) {
          if (typeof setSelectedNodeId === 'function') {
            setSelectedNodeId(blockingIssue.nodeId);
          }
          // 注入错误日志条目，使错误在底部面板的错误 tab 中可见（而不仅是 toast）
          const now = new Date();
          const timeStr = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
          setLogs([{
            id: `validation-${Date.now()}`,
            time: timeStr,
            level: 'error',
            message: blockingIssue.message,
            nodeId: blockingIssue.nodeId,
          }]);
          useBottomPanelStore.getState().setActiveTab('errors');
          useBottomPanelStore.getState().setOpen(true);
          return;
        }
        if (validation.primaryIssue?.severity === 'warn') {
          pushToast('info', validation.primaryIssue.message);
        }
        // 乐观反馈：点击运行后立即进入"运行中"状态，无需等待后端
        const flowName = currentFlow?.name ?? '未命名流程';
        const browserExecutor = runOptions.browserExecutor ?? currentFlow?.defaultBrowserExecutor ?? 'playwright';
        activeFlowNameRef.current = flowName;
        resetRunView();
        setActiveRunFlowId(currentFlow?.flowId ?? null);
        setRuntimeStatus('running');
        const result = await callBridge((api) =>
          api.startRun({
            mode,
            adaptive: true,
            autoSave: true,
            browserExecutor,
            concurrency: normalizeRunConcurrency(runOptions.concurrency),
            failureStrategy: runOptions.failureStrategy ?? 'stop',
            flowDefinition,
            flowId: currentFlow?.flowId ?? undefined,
            flowName,
            scope,
            selector: executableNode.selector ?? lastPickerResult?.selector ?? '',
            screenshot: runOptions.screenshot ?? true,
            startNodeId: runOptions.startNodeId,
            targetUrl: executableNode.targetUrl ?? lastPickerResult?.url ?? '',
            timeoutMs: executableNode.timeoutMs ?? runOptions.timeoutMs ?? 30_000,
            overrideVariables: buildRuntimeVariablePayload(runOptions.overrideVariables ?? []),
            variables: buildRuntimeVariablePayload(runVariables)
          })
        );
        if (result !== null) {
          setLastRunOverrides(currentFlow?.flowId ?? null, runOptions.overrideVariables ?? []);
          setActiveRunId(result.runId);
          setActiveRunFlowId(currentFlow?.flowId ?? null);
          setRuntimeStatus(result.status);
          pushToast('info', getRunStartedMessage(runOptions));
        } else {
          setActiveRunFlowId(null);
          setRuntimeStatus('ready');
        }
      },
      stopRun: async () => {
        const result = await callBridge((api) => api.stopRun(activeRunId ?? undefined));
        if (result !== null) {
          setActiveRunId(null);
          setActiveRunFlowId(null);
          setRuntimeStatus(result.status);
          if (result.stopped) {
            pushToast('info', '流程已停止');
          }
        }
      },
      provideInput: async (value: string) => {
        if (activeRunId === null) return;
        const result = await callBridge((api) => api.provideInput(activeRunId, value));
        if (result !== null) setInputPrompt(null);
      },
      resumeHumanTakeover: async (resumeMode: string) => {
        if (activeRunId === null) return;
        const result = await callBridge((api) => api.resumeHumanTakeover(activeRunId, resumeMode));
        if (result !== null) setHumanTakeoverMessage(null);
      },
      generateScraplingScript: async () => {
        const flowDefinition = buildFlowDefinition(flowCanvas.nodes, flowCanvas.edges, inputVariables, currentFlow?.name ?? '未命名流程');
        const result = await callBridge((api) => api.generateScraplingScript({ adaptive: true, autoSave: true, flowDefinition, flowName: currentFlow?.name ?? '未命名流程' }));
        if (result !== null) {
          setGeneratedScript(result);
          pushToast('success', `已生成 ${result.filename}`);
        }
      },
      analyzeCurrentSite: async () => {
        const scriptTarget = readBrowserScriptTarget(flowCanvas, lastPickerResult, { actionLabel: '站点分析', requireSelector: false });
        if (scriptTarget.error !== null) {
          pushToast('info', scriptTarget.error);
          return;
        }
        const { selector, targetUrl } = scriptTarget;
        const result = await callBridge((api) => api.analyzeSite({ maxCandidates: 8, selector: selector || undefined, targetUrl }), '站点分析已完成');
        if (result !== null) {
          setSiteAnalysis(result);
        }
      },
      loadRuns: async (options = {}) => {
        const { silent, ...query } = options;
        const result = await callBridge((api) => api.listRuns(query), undefined, { silent });
        if (result !== null) {
          setRuns(result);
        }
      },
      loadFlowRuns: async (flowId: string, options = {}) => {
        const { silent, ...query } = options;
        const result = await callBridge((api) => api.listFlowRuns(flowId, query), undefined, { silent });
        if (result !== null) {
          setRuns(result);
        }
      },
      loadTaskVariables: async (taskId: string) => {
        const result = await callBridge((api) => api.listTaskVariables(taskId));
        if (result !== null) {
          setVariables(result);
        }
      },
      loadArtifacts: async (taskId: string) => {
        const result = await callBridge((api) => api.listArtifacts(taskId));
        if (result !== null) {
          setArtifacts(result);
        }
      },
      readArtifact: async (taskId: string, artifactId: string) => {
        const result = await callBridge((api) => api.readArtifact(taskId, artifactId));
        if (result !== null) {
          setArtifactContent(result);
          pushToast('success', `已读取 ${result.artifact.filename}`);
        }
      },
      loadQueueStats: async (options?: BridgeCallOptions) => {
        const result = await callBridge((api) => api.getQueueStats(), undefined, options);
        if (result !== null) {
          setQueueStats(result);
        }
      },
      loadSchedules: async (options?: BridgeCallOptions) => {
        const result = await callBridge((api) => api.listSchedules(), undefined, options);
        if (result !== null) {
          setSchedules(result);
        }
      },
      createDefaultSchedule: async (options = {}) => {
        const { flowId: targetFlowId } = options;
        let taskFlowId: string | undefined;
        let taskFlowName: string;
        let taskFlowDefaultExecutor: BrowserExecutorKind | undefined;

        if (targetFlowId === '__all__') {
          taskFlowId = undefined;
          taskFlowName = '所有流程';
        } else if (typeof targetFlowId === 'string' && targetFlowId.length > 0) {
          const target = flows.find((f) => f.flowId === targetFlowId);
          if (target === undefined) {
            pushToast('error', '未找到所选流程');
            return;
          }
          taskFlowId = target.flowId;
          taskFlowName = target.name;
          taskFlowDefaultExecutor = target.defaultBrowserExecutor;
        } else {
          const flow = await persistCurrentFlow({ callBridge, currentFlow, flows, flowCanvas, inputVariables });
          if (flow === null) {
            pushToast('error', '创建调度前需要先保存当前流程');
            return;
          }
          setCurrentFlow(flow);
          setFlows((current) => [flow, ...current.filter((item) => item.flowId !== flow.flowId)]);
          taskFlowId = flow.flowId;
          taskFlowName = flow.name;
          taskFlowDefaultExecutor = flow.defaultBrowserExecutor;
        }

        const result = await callBridge((api) =>
          api.createSchedule({
            name: normalizeScheduleName(options.name),
            cronExpression: normalizeCronExpression(options.cronExpression),
            timezone: normalizeScheduleTimezone(options.timezone),
            enabled: options.enabled ?? true,
            task: {
              mode: 'run',
              adaptive: true,
              autoSave: true,
              browserExecutor: options.browserExecutor ?? taskFlowDefaultExecutor,
              flowId: taskFlowId,
              flowName: taskFlowName,
              timeoutMs: 30_000
            }
          })
        );
        if (result !== null) {
          setSchedules((current) => [...current.filter((item) => item.scheduleId !== result.scheduleId), result]);
          pushToast('success', `已创建调度 ${result.name}`);
        }
      },
      createScheduleForFlow: async (flowId: string, options = {}) => {
        const source = flows.find((flow) => flow.flowId === flowId) ?? (currentFlow?.flowId === flowId ? currentFlow : null);
        if (source === null) {
          pushToast('error', '未找到要调度的流程');
          return;
        }
        const result = await callBridge((api) =>
          api.createSchedule({
            name: normalizeScheduleName(options.name ?? `${source.name} 定时任务`),
            cronExpression: normalizeCronExpression(options.cronExpression),
            timezone: normalizeScheduleTimezone(options.timezone),
            enabled: options.enabled ?? true,
            task: {
              mode: 'run',
              adaptive: true,
              autoSave: true,
              browserExecutor: options.browserExecutor ?? source.defaultBrowserExecutor,
              flowId: source.flowId,
              flowName: source.name,
              selector: '',
              targetUrl: '',
              timeoutMs: 30_000
            }
          })
        );
        if (result !== null) {
          setSchedules((current) => [...current.filter((item) => item.scheduleId !== result.scheduleId), result]);
          pushToast('success', `已创建调度 ${result.name}`);
        }
      },
      updateScheduleEnabled: async (scheduleId: string, enabled: boolean) => {
        const result = await callBridge((api) => api.updateSchedule(scheduleId, { enabled }));
        if (result !== null) {
          setSchedules((current) => [...current.filter((item) => item.scheduleId !== result.scheduleId), result]);
          pushToast('success', `${result.name} 已${result.status === 'enabled' ? '启用' : '停用'}`);
        }
      },
      updateSchedule: async (scheduleId: string, options: CreateScheduleOptions) => {
        const { flowId: targetFlowId } = options;
        let taskFlowId: string | undefined;
        let taskFlowName: string | undefined;

        if (targetFlowId === '__all__') {
          taskFlowId = undefined;
          taskFlowName = '所有流程';
        } else if (typeof targetFlowId === 'string' && targetFlowId.length > 0) {
          const target = flows.find((f) => f.flowId === targetFlowId);
          taskFlowId = target?.flowId;
          taskFlowName = target?.name ?? targetFlowId;
        }

        const existing = (await callBridge((api) => api.listSchedules()))?.find((s) => s.scheduleId === scheduleId);
        const task = existing?.task;

        const result = await callBridge((api) =>
          api.updateSchedule(scheduleId, {
            name: options.name?.trim() ? options.name.trim() : undefined,
            cronExpression: options.cronExpression ? normalizeCronExpression(options.cronExpression) : undefined,
            timezone: options.timezone?.trim() ? options.timezone.trim() : undefined,
            enabled: options.enabled,
            task: taskFlowName !== undefined || options.browserExecutor !== undefined
              ? {
                  ...(task ?? { mode: 'run', adaptive: true, autoSave: true, flowName: taskFlowName ?? '未命名流程', timeoutMs: 30_000 }),
                  flowId: taskFlowId ?? task?.flowId,
                  flowName: taskFlowName ?? task?.flowName ?? '未命名流程',
                  browserExecutor: options.browserExecutor ?? task?.browserExecutor
                }
              : undefined
          })
        );
        if (result !== null) {
          setSchedules((current) => [...current.filter((item) => item.scheduleId !== result.scheduleId), result]);
          pushToast('success', `调度 ${result.name} 已更新`);
        }
      },
      deleteSchedule: async (scheduleId: string) => {
        const result = await callBridge((api) => api.deleteSchedule(scheduleId));
        if (result !== null && result.deleted) {
          setSchedules((current) => current.filter((item) => item.scheduleId !== scheduleId));
          pushToast('success', '调度已删除');
        }
      },
      triggerSchedule: async (scheduleId: string) => {
        const result = await callBridge((api) => api.triggerSchedule(scheduleId));
        if (result !== null) {
          setSchedules((current) => [...current.filter((item) => item.scheduleId !== result.schedule.scheduleId), result.schedule]);
          if (result.run !== null && result.run !== undefined) {
            setActiveRunId(result.run.runId);
            setActiveRunFlowId(result.run.flowId ?? result.schedule.task.flowId ?? null);
            setRuntimeStatus(result.run.status);
          }
          pushToast('info', `已触发调度 ${result.schedule.name}`);
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
    }),
    [
      activeRunId,
      activeRunFlowId,
      callBridge,
      clearLastRunOverrides,
      currentFlow,
      dismissToast,
      flowCanvas,
      flows,
      inputVariables,
      lastPickerResult,
      pushToast,
      resetRunView,
      setLastRunOverrides,
      setCurrentFlow,
      setFlowEdges,
      setFlowNodes,
      setFlows,
      setInputVariables,
      setActiveRunId,
      setActiveRunFlowId,
      setArtifactContent,
      setArtifacts,
      setGeneratedScript,
      setLogs,
      setQueueStats,
      setRuntimeStatus,
      setRuns,
      setSchedules,
      setSelectedNodeId,
      setSiteAnalysis,
      setInputPrompt,
      setHumanTakeoverMessage,
      setVariables
    ]
  );
}

async function persistCurrentFlow({
  callBridge,
  currentFlow,
  flows,
  flowCanvas,
  inputVariables
}: {
  callBridge: <T>(action: (bridge: RpaBridge) => Promise<BridgeResult<T>>, successMessage?: string, options?: BridgeCallOptions) => Promise<T | null>;
  currentFlow: FlowSnapshot | null;
  flows: FlowSnapshot[];
  flowCanvas: FlowCanvasSnapshot;
  inputVariables: RuntimeVariable[];
}): Promise<FlowSnapshot | null> {
  const definition = buildFlowDefinition(flowCanvas.nodes, flowCanvas.edges, inputVariables, currentFlow?.name);
  if (currentFlow === null) {
    return await callBridge((api) => api.createFlow(buildInitialFlowPayload(definition, inputVariables)));
  }
  if (currentFlow.flowId.startsWith('local-')) {
    return await callBridge((api) => api.createFlow(buildInitialFlowPayload(definition, inputVariables, currentFlow.name)));
  }
  const savedFlow = flows.find((flow) => flow.flowId === currentFlow.flowId);
  const nameChanged = savedFlow !== undefined && savedFlow.name !== currentFlow.name;
  // 无变化且已是 active 状态时跳过写入，避免每次保存都产生新的版本快照
  if (!nameChanged && !hasDefinitionChanged(currentFlow, definition, inputVariables) && currentFlow.status === 'active') {
    return currentFlow;
  }
  return await callBridge((api) => api.updateFlow(currentFlow.flowId, buildUpdatePayload(currentFlow, flows, definition, inputVariables)));
}

function applyFlowDefinitionToCanvas(
  definition: Record<string, unknown>,
  setFlowNodes: Dispatch<SetStateAction<Node<RpaNodeData>[]>>,
  setFlowEdges: Dispatch<SetStateAction<Edge[]>>
): void {
  const restored = restoreFlowCanvas(definition);
  if (restored === null) {
    return;
  }
  const nodes = ensureStartEndNodes(restored.nodes);
  setFlowNodes(nodes);
  setFlowEdges(restored.edges);
}

function ensureStartEndNodes(nodes: Node<RpaNodeData>[]): Node<RpaNodeData>[] {
  const hasStart = nodes.some((n) => n.id === 'start');
  const hasEnd = nodes.some((n) => n.id === 'end');
  if (hasStart && hasEnd) {
    return nodes;
  }
  const result = [...nodes];
  if (!hasStart) {
    result.unshift({ ...initialNodes[0], position: { ...initialNodes[0].position }, data: { ...initialNodes[0].data } });
  }
  if (!hasEnd) {
    const lastNode = result[result.length - 1];
    const endY = lastNode !== undefined ? lastNode.position.y + 120 : initialNodes[1].position.y;
    result.push({ ...initialNodes[1], position: { x: initialNodes[1].position.x, y: endY }, data: { ...initialNodes[1].data } });
  }
  return result;
}

function openFlowSnapshot(
  flow: FlowSnapshot,
  {
    pushToast,
    setCurrentFlow,
    setFlowEdges,
    setFlowNodes,
    setInputVariables
  }: {
    pushToast: (type: BridgeToast['type'], message: string, icon?: string) => void;
    setCurrentFlow: Dispatch<SetStateAction<FlowSnapshot | null>>;
    setFlowEdges: Dispatch<SetStateAction<Edge[]>>;
    setFlowNodes: Dispatch<SetStateAction<Node<RpaNodeData>[]>>;
    setInputVariables: (variables: RuntimeVariable[]) => void;
  }
): void {
  setCurrentFlow(flow);
  applyFlowDefinitionToCanvas(flow.definition, setFlowNodes, setFlowEdges);
  setInputVariables(flow.inputVariables);
  pushToast('success', `已打开 ${flow.name} ${flow.version}`);
}

function restoreInitialNodes(): Node<RpaNodeData>[] {
  return initialNodes.map((node) => ({
    ...node,
    data: { ...node.data, action: node.data.action === undefined ? undefined : { ...node.data.action } },
    position: { ...node.position }
  }));
}

function restoreInitialEdges(): Edge[] {
  return initialEdges.map((edge) => ({
    ...edge
  }));
}

// flowId 以 "local-" 开头即代表尚未持久化到后端的本地草稿；多处代码据此判断是
// 该走 createFlow 还是 updateFlow（见 persistCurrentFlow / setDefaultBrowserExecutor）
function createLocalDraftFlow(name: string): FlowSnapshot {
  const now = new Date().toISOString();
  return {
    createdAt: now,
    definition: {},
    flowId: `local-${Date.now()}`,
    folderPath: '默认目录',
    inputVariables: [],
    name,
    snapshots: [],
    status: 'draft',
    updatedAt: now,
    version: 'draft'
  };
}

/** 把属性面板里未点保存的草稿落到画布，返回落盘后的快照。
 *
 * 运行读的是画布节点，草稿只活在面板本地 state：改完直接点运行，跑的是改之前的值，
 * 结果还是 success，唯一提示是面板标题旁 1.5px 的小圆点。
 * 就地返回新快照而不是等 setFlowNodes 生效：状态更新要下一帧才可见，本次运行仍会拿到旧节点。
 */
function commitPendingNodeDraft(
  flowCanvas: FlowCanvasSnapshot,
  setFlowNodes: Dispatch<SetStateAction<Node<RpaNodeData>[]>>
): FlowCanvasSnapshot {
  const { pendingDraft, setPendingDraft } = usePropertyPanelStore.getState();
  const nodes = applyPendingDraftToNodes(flowCanvas.nodes, pendingDraft);
  if (nodes === flowCanvas.nodes) {
    return flowCanvas;
  }
  setFlowNodes((current) => applyPendingDraftToNodes(current, pendingDraft));
  setPendingDraft(null);
  return { ...flowCanvas, nodes };
}

function findExecutableFetchNode(flowCanvas: FlowCanvasSnapshot): { selector?: string; targetUrl?: string; timeoutMs?: number } {
  for (const node of flowCanvas.nodes) {
    if (node.data.action?.type !== 'browser.fetch') {
      continue;
    }
    return {
      selector: node.data.action.selector,
      targetUrl: node.data.action.targetUrl,
      timeoutMs: node.data.action.timeoutMs
    };
  }
  return {};
}

type BrowserScriptTarget =
  | { error: null; selector: string; targetUrl: string }
  | { error: string; selector?: never; targetUrl?: never };

function readBrowserScriptTarget(
  flowCanvas: FlowCanvasSnapshot,
  lastPickerResult: PickerResult | null,
  options: { actionLabel: string; requireSelector: boolean }
): BrowserScriptTarget {
  const openNode = flowCanvas.nodes.find((node) => node.data.action?.type === 'browser.open' || node.data.action?.type === 'browser.tab.open');
  const fetchNode = flowCanvas.nodes.find((node) => node.data.action?.type === 'browser.fetch' || node.data.action?.type === 'browser.extract');
  const flowUrl = openNode?.data.action?.targetUrl ?? openNode?.data.action?.url;
  const flowSelector = fetchNode?.data.action?.selector;
  const targetUrl = lastPickerResult?.url ?? normalizeNonEmptyString(flowUrl);
  const selector = lastPickerResult?.selector ?? normalizeNonEmptyString(flowSelector);

  if (!isHttpUrl(targetUrl)) {
    return {
      error: targetUrl.includes('${')
        ? `${options.actionLabel}需要具体网址，请先用浏览器拾取器捕获当前页面，或把打开网页节点改为真实 URL`
        : `${options.actionLabel}前需要先配置打开网页节点的 HTTP URL`
    };
  }
  if (options.requireSelector && selector === '') {
    return { error: `${options.actionLabel}前需要先配置抓取/提取节点选择器，或使用拾取器选择页面元素` };
  }
  return { error: null, selector, targetUrl };
}

function normalizeNonEmptyString(value: unknown): string {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : '';
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

function buildRuntimeVariablePayload(variables: RuntimeVariable[]): Record<string, unknown> {
  return variables.reduce<Record<string, unknown>>((payload, variable) => {
    if (!isSafeVariableName(variable.name)) {
      return payload;
    }
    payload[variable.name] = parseRuntimeVariableValue(variable);
    return payload;
  }, {});
}

function mergeRunVariables(baseVariables: RuntimeVariable[], overrideVariables: RuntimeVariable[]): RuntimeVariable[] {
  const merged = new Map(baseVariables.map((variable) => [variable.name, variable]));
  for (const variable of overrideVariables) {
    merged.set(variable.name, variable);
  }
  return [...merged.values()];
}

function parseRuntimeVariableValue(variable: RuntimeVariable): unknown {
  if (variable.type === 'Integer') {
    const value = Number.parseInt(variable.value, 10);
    return Number.isFinite(value) ? value : 0;
  }
  if (variable.type === 'Boolean') {
    return variable.value.trim().toLowerCase() === 'true';
  }
  if (variable.type === 'List' || variable.type === 'Dict') {
    try {
      return JSON.parse(variable.value) as unknown;
    } catch {
      return variable.value;
    }
  }
  return variable.value;
}

function getRunStartedMessage(options: StartRunOptions): string {
  if (options.mode === 'debug') {
    return '调试运行已启动';
  }
  if (options.scope === 'from-selection') {
    return '已从选中步骤启动运行';
  }
  if (options.scope === 'selected-only') {
    return '已启动选中步骤运行';
  }
  return '流程运行已启动';
}

function normalizeScheduleName(value: string | undefined): string {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : '未命名计划';
}

function normalizeScheduleTimezone(value: string | undefined): string {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : 'Asia/Shanghai';
}

function normalizeCronExpression(value: string | undefined): string {
  const normalized = typeof value === 'string' ? value.trim().replace(/\s+/g, ' ') : '';
  return normalized.length > 0 ? normalized : '0 9 * * *';
}
