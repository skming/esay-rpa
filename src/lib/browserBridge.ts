import type {
  AiConfig,
  AiConfigPatch,
  AiModelCatalogPatch,
  AiModelCatalogUpdatePatch,
  AiModelTestPayload,
  AiModelTestResult,
  AiModelsResult,
  ArtifactContent,
  ArtifactSnapshot,
  BackendServiceStatus,
  BridgeResult,
  FlowFileResult,
  FlowSavePayload,
  GenerateScriptPayload,
  PickerCloseResult,
  PickerOpenPayload,
  PickerOpenResult,
  PickerResult,
  RpaBridge,
  RunEvent,
  RunStartPayload,
  RunStartResult,
  RunStopResult,
  ScheduleCreatePayload,
  TaskSnapshot,
  WindowStateResult
} from '../types/electron';
import type { RunLogLevel, RuntimeStatus, RuntimeVariable } from '../types/rpa';
import { BackendClient, type BackendTaskLogEntry } from './backendClient';
import { buildRunConfigVariables as _buildRunConfigVariables } from './runConfigPresentation';

type BrowserBridgeOptions = {
  backendClient?: BackendClient;
};

type ActiveBrowserRun = {
  artifactIds: Set<string>;
  lastInputPrompt: string | null;
  lastLogIds: Set<string>;
  pollTimer: number | null;
  runId: string;
  socket: WebSocket | null;
  startedAt: number;
  usingWebSocket: boolean;
};

type RunListener = (event: RunEvent) => void;
type PickerListener = (result: PickerResult) => void;

function success<T>(data: T): BridgeResult<T> {
  return { data, ok: true };
}

function failure<T = never>(error: unknown): BridgeResult<T> {
  return { error: error instanceof Error ? error.message : String(error), ok: false };
}

/** 浏览器模式下 RpaBridge 的实现：没有 Electron IPC/原生事件，改为对后端做 WebSocket 优先、HTTP 轮询兜底的适配。 */
export function createBrowserBridge({ backendClient = new BackendClient() }: BrowserBridgeOptions = {}): RpaBridge {
  const runListeners = new Set<RunListener>();
  const pickerListeners = new Set<PickerListener>();
  let activeRun: ActiveBrowserRun | null = null;
  let lastPickerResult: PickerResult | null = null;

  const emitRunEvent = (event: RunEvent): void => {
    runListeners.forEach((listener) => listener(event));
  };

  const emitPickerResult = (result: PickerResult): void => {
    lastPickerResult = result;
    pickerListeners.forEach((listener) => listener(result));
  };

  const clearActiveRun = (): void => {
    if (activeRun?.pollTimer != null) {
      window.clearTimeout(activeRun.pollTimer);
    }
    activeRun?.socket?.close();
    activeRun = null;
  };

  const watchBackendTask = (task: TaskSnapshot, payload: Partial<RunStartPayload> = {}): RunStartResult => {
    clearActiveRun();
    const runId = task.taskId;
    activeRun = {
      artifactIds: new Set<string>(),
      lastInputPrompt: null,
      lastLogIds: new Set<string>(),
      pollTimer: null,
      runId,
      socket: null,
      startedAt: Date.now(),
      usingWebSocket: false
    };

    const startPayload: RunStartResult = {
      runId,
      flowId: task.flowId ?? payload.flowId ?? null,
      startedAt: new Date(activeRun.startedAt).toISOString(),
      status: 'running',
      totalSteps: 0,
      flowName: payload.flowName ?? '',
    };
    emitRunEvent({ payload: startPayload, type: 'run:start' });
    emitRunEvent({ payload: { nodeId: 'start', runId, status: 'done' }, type: 'node:update' });
    emitRunEvent({ payload: { nodeId: 'n1', runId, status: 'running', badge: 'Scrapling' }, type: 'node:update' });
    attachLogSocket(runId);
    activeRun.pollTimer = window.setTimeout(() => void pollBackendTask(runId), 250);
    return startPayload;
  };

  const pollBackendTask = async (runId: string): Promise<void> => {
    if (activeRun === null || activeRun.runId !== runId) {
      return;
    }

    try {
      const [snapshot, logs] = await Promise.all([backendClient.getTask(runId), backendClient.getLogs(runId)]);
      if (activeRun === null || activeRun.runId !== runId) {
        return;
      }
      const currentRun = activeRun;

      if (!currentRun.usingWebSocket) {
        logs.forEach((log) => emitBackendLog(currentRun, emitRunEvent, log));
      }

      // 轮询兜底：补上可能因 WS 未连接/日志漏收而错过的 input 提示
      if (snapshot.inputPrompt != null && snapshot.inputPrompt !== currentRun.lastInputPrompt) {
        currentRun.lastInputPrompt = snapshot.inputPrompt;
        const syntheticId = `${runId}:poll-input`;
        if (!currentRun.lastLogIds.has(syntheticId)) {
          currentRun.lastLogIds.add(syntheticId);
          emitRunEvent({
            payload: {
              id: syntheticId,
              level: 'input',
              message: snapshot.inputPrompt,
              nodeId: 'n1',
              runId,
              time: formatLogTime(new Date())
            },
            type: 'log:append'
          });
        }
      } else if (snapshot.inputPrompt == null) {
        currentRun.lastInputPrompt = null;
      }

      const status = normalizeRuntimeStatus(snapshot.status);
      const elapsedMs = Date.now() - currentRun.startedAt;
      emitBackendVariables(emitRunEvent, runId, snapshot.variables);
      emitBackendArtifacts(currentRun, emitRunEvent, runId, snapshot.artifacts);
      emitRunEvent({
        payload: {
          currentStep: 0,
          elapsedMs,
          percent: status === 'running' ? Math.max(snapshot.progress.percent, 20) : 100,
          runId,
          totalSteps: 0
        },
        type: 'run:progress'
      });

      if (status === 'running') {
        emitRunEvent({ payload: { nodeId: 'n1', runId, status: 'running', badge: 'Scrapling' }, type: 'node:update' });
        currentRun.pollTimer = window.setTimeout(() => void pollBackendTask(runId), 700);
        return;
      }

      finishBackendRun(runId, status, status === 'success' ? '任务执行完成' : snapshot.error ?? '任务执行结束');
    } catch (error) {
      emitRunEvent({
        payload: {
          id: `${runId}-poll-error`,
          level: 'error',
          message: `后端任务轮询失败 · ${error instanceof Error ? error.message : String(error)}`,
          nodeId: 'n1',
          runId,
          time: formatLogTime(new Date())
        },
        type: 'log:append'
      });
      finishBackendRun(runId, 'error', '后端任务轮询失败');
    }
  };

  const finishBackendRun = (runId: string, status: RuntimeStatus, message: string): void => {
    if (activeRun === null || activeRun.runId !== runId) {
      return;
    }

    const finishedAt = new Date().toISOString();
    activeRun.socket?.close();
    if (activeRun.pollTimer !== null) {
      window.clearTimeout(activeRun.pollTimer);
    }
    activeRun = null;

    emitRunEvent({ payload: { nodeId: 'n1', runId, status: status === 'error' ? 'error' : 'done', badge: 'Scrapling' }, type: 'node:update' });
    emitRunEvent({
      payload: {
        nodeId: 'end',
        runId,
        status: status === 'success' ? 'done' : status === 'stopped' ? 'skipped' : 'error'
      },
      type: 'node:update'
    });
    emitRunEvent({ payload: { finishedAt, message, runId, status }, type: 'run:finish' });
  };

  const attachLogSocket = (runId: string): void => {
    if (activeRun === null) {
      return;
    }

    try {
      const socket = backendClient.createLogSocket(runId);
      activeRun.socket = socket;
      socket.addEventListener('open', () => {
        if (activeRun?.runId === runId) {
          activeRun.usingWebSocket = true;
        }
      });
      socket.addEventListener('message', (event) => {
        const run = activeRun;
        if (run === null || run.runId !== runId) {
          return;
        }
        try {
          const parsed = JSON.parse(String(event.data)) as Record<string, unknown>;
          // task not found 时 backend 发 {"type":"error",...} 无 id 字段，非日志条目
          if (typeof parsed.id !== 'string') {
            return;
          }
          emitBackendLog(run, emitRunEvent, parsed as unknown as BackendTaskLogEntry);
        } catch {
          // 丢弃，HTTP 轮询会补全
        }
      });
      socket.addEventListener('error', () => {
        const run = activeRun;
        if (run === null || run.runId !== runId) return;
        run.usingWebSocket = false;
      });
      socket.addEventListener('close', () => {
        const run = activeRun;
        if (run === null || run.runId !== runId) return;
        run.usingWebSocket = false;
      });
    } catch {
      // WS 仅是加速通道，失败时继续靠 HTTP 轮询
    }
  };

  return {
    analyzeSite: async (payload) => {
      try {
        return success(await backendClient.analyzeSite(payload));
      } catch (error) {
        return failure(error);
      }
    },
    closePicker: async (): Promise<BridgeResult<PickerCloseResult>> => success({ status: 'closed' }),
    closeWindow: async (): Promise<BridgeResult<WindowStateResult>> => success({ closed: false }),
    debugRun: async (runId, command) => {
      if (activeRun === null || activeRun.runId !== runId) {
        return failure('当前没有匹配的运行任务');
      }
      try {
        const snapshot = await backendClient.debugTask(runId, command);
        return success({ runId: snapshot.taskId, status: normalizeRuntimeStatus(snapshot.status) });
      } catch (error) {
        return failure(error);
      }
    },
    archiveFlow: async (flowId) => {
      try {
        return success(await backendClient.archiveFlow(flowId));
      } catch (error) {
        return failure(error);
      }
    },
    duplicateFlow: async (flowId) => {
      try {
        return success(await backendClient.duplicateFlow(flowId));
      } catch (error) {
        return failure(error);
      }
    },
    moveFlow: async (flowId, folderPath) => {
      try {
        return success(await backendClient.moveFlow(flowId, folderPath));
      } catch (error) {
        return failure(error);
      }
    },
    setFlowStatus: async (flowId, status) => {
      try {
        return success(await backendClient.setFlowStatus(flowId, status));
      } catch (error) {
        return failure(error);
      }
    },
    createFlow: async (payload) => {
      try {
        return success(await backendClient.createFlow(payload));
      } catch (error) {
        return failure(error);
      }
    },
    createSchedule: async (payload: ScheduleCreatePayload) => {
      try {
        return success(await backendClient.createSchedule(payload));
      } catch (error) {
        return failure(error);
      }
    },
    getBackendStatus: async (): Promise<BridgeResult<BackendServiceStatus>> => {
      try {
        await backendClient.health();
        return success({
          error: null,
          installProgress: null,
          installStep: null,
          installStepLabel: null,
          installStepTotal: null,
          managed: false,
          pid: null,
          source: 'external',
          status: 'ready',
          url: backendClient.baseUrl
        });
      } catch (error) {
        return success({
          error: error instanceof Error ? error.message : String(error),
          installProgress: null,
          installStep: null,
          installStepLabel: null,
          installStepTotal: null,
          managed: false,
          pid: null,
          source: 'unknown',
          status: 'error',
          url: backendClient.baseUrl
        });
      }
    },
    deleteSchedule: async (scheduleId) => {
      try {
        return success(await backendClient.deleteSchedule(scheduleId));
      } catch (error) {
        return failure(error);
      }
    },
    deleteFlow: async (flowId) => {
      try {
        return success(await backendClient.deleteFlow(flowId));
      } catch (error) {
        return failure(error);
      }
    },
    exportLogs: async (payload): Promise<BridgeResult<FlowFileResult>> => {
      const blob = new Blob([payload.content], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.download = payload.filename ? `${payload.filename}-运行日志.log` : '运行日志.log';
      anchor.href = url;
      anchor.click();
      URL.revokeObjectURL(url);
      return success({ canceled: false, name: anchor.download });
    },
    generateScraplingScript: async (payload: GenerateScriptPayload) => {
      try {
        return success(await backendClient.generateScript(payload));
      } catch (error) {
        return failure(error);
      }
    },
    getAppVersion: async () =>
      success({
        arch: navigator.userAgent.includes('arm64') ? 'arm64' : 'browser',
        hostname: location.hostname,
        platform: navigator.platform,
        version: '0.1.0'
      }),
    openDataDir: async (subDir?: string) => {
      const base = '~/.easy-rpa';
      const target = subDir ? `${base}/${subDir}` : base;
      try {
        await navigator.clipboard.writeText(target);
        return success({ opened: target });
      } catch {
        return failure('无法打开目录（仅桌面端支持）');
      }
    },
    showInFinder: async (filePath: string) => {
      try {
        await navigator.clipboard.writeText(filePath);
        return success({ opened: filePath });
      } catch {
        return failure('无法在文件管理器中显示（仅桌面端支持）');
      }
    },
    getQueueStats: async () => {
      try {
        return success(await backendClient.getQueueStats());
      } catch (error) {
        return failure(error);
      }
    },
    getWindowId: async () => success(null),
    listArtifacts: async (taskId): Promise<BridgeResult<ArtifactSnapshot[]>> => {
      try {
        return success(await backendClient.getArtifacts(taskId));
      } catch (error) {
        return failure(error);
      }
    },
    listFlowRuns: async (flowId, options) => {
      try {
        return success(await backendClient.listFlowRuns(flowId, options));
      } catch (error) {
        return failure(error);
      }
    },
    listFlows: async () => {
      try {
        return success(await backendClient.listFlows());
      } catch (error) {
        return failure(error);
      }
    },
    listRuns: async (options) => {
      try {
        return success(await backendClient.listTasks(options));
      } catch (error) {
        return failure(error);
      }
    },
    listTaskVariables: async (taskId) => {
      try {
        return success(await backendClient.getVariables(taskId));
      } catch (error) {
        return failure(error);
      }
    },
    listSchedules: async () => {
      try {
        return success(await backendClient.listSchedules());
      } catch (error) {
        return failure(error);
      }
    },
    getAiConfig: async (): Promise<BridgeResult<AiConfig>> => {
      try {
        return success(await backendClient.getAiConfig());
      } catch (error) {
        return failure(error);
      }
    },
    setAiConfig: async (payload: AiConfigPatch): Promise<BridgeResult<AiConfig>> => {
      try {
        return success(await backendClient.setAiConfig(payload));
      } catch (error) {
        return failure(error);
      }
    },
    listAiModels: async (): Promise<BridgeResult<AiModelsResult>> => {
      try {
        return success(await backendClient.listAiModels());
      } catch (error) {
        return failure(error);
      }
    },
    addAiModel: async (payload: AiModelCatalogPatch): Promise<BridgeResult<AiModelsResult>> => {
      try {
        return success(await backendClient.addAiModel(payload));
      } catch (error) {
        return failure(error);
      }
    },
    updateAiModel: async (payload: AiModelCatalogUpdatePatch): Promise<BridgeResult<AiModelsResult>> => {
      try {
        return success(await backendClient.updateAiModel(payload));
      } catch (error) {
        return failure(error);
      }
    },
    deleteAiModel: async (modelId: string): Promise<BridgeResult<AiModelsResult>> => {
      try {
        return success(await backendClient.deleteAiModel(modelId));
      } catch (error) {
        return failure(error);
      }
    },
    testAiModel: async (payload: AiModelTestPayload): Promise<BridgeResult<AiModelTestResult>> => {
      try {
        const res = await fetch(`${backendClient.baseUrl}/api/ai/test-model`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json() as AiModelTestResult & { detail?: string };
        if (!res.ok) throw new Error(data.detail ?? '模型测试失败');
        return success(data);
      } catch (error) {
        return failure(error);
      }
    },
    minimizeWindow: async (): Promise<BridgeResult<WindowStateResult>> => success({ minimized: false }),
    onPickerResult: (callback) => {
      pickerListeners.add(callback);
      return () => pickerListeners.delete(callback);
    },
    onPickerCancel: (_callback) => () => { },
    onRunEvent: (callback) => {
      runListeners.add(callback);
      return () => runListeners.delete(callback);
    },
    openFlow: async () => {
      try {
        const flows = await backendClient.listFlows();
        return success({ canceled: flows.length === 0, content: flows[0] === undefined ? undefined : JSON.stringify(flows[0].definition), name: flows[0]?.name });
      } catch (error) {
        return failure(error);
      }
    },
    openPicker: async (payload?: PickerOpenPayload): Promise<BridgeResult<PickerOpenResult>> => {
      const result = createPreviewPickerResult(payload, lastPickerResult);
      window.setTimeout(() => emitPickerResult(result), 0);
      return success({ mode: 'selector-picker', status: 'ready' });
    },
    readArtifact: async (taskId, artifactId): Promise<BridgeResult<ArtifactContent>> => {
      try {
        return success(await backendClient.readArtifact(taskId, artifactId));
      } catch (error) {
        return failure(error);
      }
    },
    restartBackend: async (): Promise<BridgeResult<BackendServiceStatus>> => {
      try {
        await backendClient.health();
        return success({
          error: null,
          installProgress: null,
          installStep: null,
          installStepLabel: null,
          installStepTotal: null,
          managed: false,
          pid: null,
          source: 'external',
          status: 'ready',
          url: backendClient.baseUrl
        });
      } catch (error) {
        return success({
          error: error instanceof Error ? error.message : String(error),
          installProgress: null,
          installStep: null,
          installStepLabel: null,
          installStepTotal: null,
          managed: false,
          pid: null,
          source: 'unknown',
          status: 'error',
          url: backendClient.baseUrl
        });
      }
    },
    runFlow: async (flowId, payload) => {
      try {
        return success(await backendClient.runFlow(flowId, payload));
      } catch (error) {
        return failure(error);
      }
    },
    getExtensionInstallInfo: async () => success({ found: false, unpackedDir: null }),
    openExtensionFolder: async () => failure('仅桌面端支持打开扩展文件夹，请在 Electron 应用中操作'),
    openChromeExtensionsPage: async () => success({ opened: false, reason: '仅桌面端支持自动打开，请手动在 Chrome 地址栏输入 chrome://extensions/' }),
    saveFlow: async (payload): Promise<BridgeResult<FlowFileResult>> => {
      try {
        const content = JSON.parse(payload.content) as Record<string, unknown>;
        const flowPayload: FlowSavePayload = {
          definition: content,
          description: '从浏览器预览保存的流程定义',
          inputVariables: Array.isArray(content.inputVariables) ? (content.inputVariables as FlowSavePayload['inputVariables']) : [],
          name: readFlowName(content),
          status: 'active',
          version: readFlowVersion(content)
        };
        const flow = await backendClient.createFlow(flowPayload);
        return success({ canceled: false, name: flow.name, path: flow.flowId, content: payload.content });
      } catch (error) {
        return failure(error);
      }
    },
    startRun: async (payload: RunStartPayload): Promise<BridgeResult<RunStartResult>> => {
      try {
        // overrideVariables 仅供前端 UI 展示来源标记，后端接口不识别该字段，需剔除后再发送
        const { overrideVariables: _overrideVariables, ...backendPayload } = payload;
        const task =
          typeof payload.flowId === 'string' && payload.flowId.length > 0
            ? await backendClient.runFlow(payload.flowId, {
              browserExecutor: payload.browserExecutor,
              concurrency: payload.concurrency,
              failureStrategy: payload.failureStrategy,
              mode: payload.mode,
              scope: payload.scope,
              screenshot: payload.screenshot,
              startNodeId: payload.startNodeId,
              variables: payload.variables
            })
            : await backendClient.startTask(backendPayload);
        return success(watchBackendTask(task, payload));
      } catch (error) {
        clearActiveRun();
        return failure(error);
      }
    },
    stopRun: async (runId?: string): Promise<BridgeResult<RunStopResult>> => {
      if (activeRun === null || (typeof runId === 'string' && runId.length > 0 && activeRun.runId !== runId)) {
        return success({ runId, status: 'ready', stopped: false });
      }
      const activeRunId = activeRun.runId;
      try {
        await backendClient.stopTask(activeRunId);
      } catch {
        // 任务可能已结束，仍需完成本地清理
      }
      finishBackendRun(activeRunId, 'stopped', '流程已停止');
      return success({ runId: activeRunId, status: 'stopped', stopped: true });
    },
    provideInput: async (runId: string, value: string): Promise<BridgeResult<void>> => {
      try {
        await backendClient.provideInput(runId, value);
        return success(undefined);
      } catch (error) {
        return failure(error);
      }
    },
    resumeHumanTakeover: async (runId: string, resumeMode: string): Promise<BridgeResult<void>> => {
      try {
        await backendClient.resumeHumanTakeover(runId, resumeMode);
        return success(undefined);
      } catch (error) {
        return failure(error);
      }
    },
    toggleMaximizeWindow: async (): Promise<BridgeResult<WindowStateResult>> => success({ maximized: false }),
    triggerSchedule: async (scheduleId) => {
      try {
        const schedule = await backendClient.triggerSchedule(scheduleId);
        const run =
          typeof schedule.lastTaskId === 'string' && schedule.lastTaskId.length > 0
            ? watchBackendTask(await backendClient.getTask(schedule.lastTaskId), schedule.task)
            : null;
        return success({ schedule, run });
      } catch (error) {
        return failure(error);
      }
    },
    updateFlow: async (flowId, payload) => {
      try {
        return success(await backendClient.updateFlow(flowId, payload));
      } catch (error) {
        return failure(error);
      }
    },
    updateSchedule: async (scheduleId, payload) => {
      try {
        return success(await backendClient.updateSchedule(scheduleId, payload));
      } catch (error) {
        return failure(error);
      }
    },
    // 自动更新仅 Electron 支持，浏览器模式全部空实现
    checkForUpdates: async () => success(null),
    downloadUpdate: async () => success(null),
    quitAndInstall: async () => success(null),
    onUpdateStatus: (_callback) => () => { },
    onBackendStatusChanged: (_callback) => () => { },
  };
}

function emitBackendLog(activeRun: ActiveBrowserRun, emitRunEvent: (event: RunEvent) => void, log: BackendTaskLogEntry): void {
  if (activeRun.lastLogIds.has(log.id)) {
    return;
  }

  activeRun.lastLogIds.add(log.id);
  const level = normalizeLogLevel(log.level);
  // 标记 prompt 已处理，避免 pollBackendTask 的轮询兜底重复生成
  if (level === 'input') {
    activeRun.lastInputPrompt = log.detail ?? log.message;
    activeRun.lastLogIds.add(`${activeRun.runId}:poll-input`);
  }
  emitRunEvent({
    payload: {
      id: log.id,
      level,
      message: log.detail ? `${log.message} · ${log.detail}` : log.message,
      nodeId: log.nodeId ?? resolveBackendLogNodeId(log.level),
      runId: activeRun.runId,
      time: formatLogTime(new Date(log.time))
    },
    type: 'log:append'
  });
}

function resolveBackendLogNodeId(level: string): string {
  return level === 'success' ? 'end' : 'n1';
}

function emitVariable(emitRunEvent: (event: RunEvent) => void, runId: string, variable: RuntimeVariable): void {
  emitRunEvent({ payload: { ...variable, runId }, type: 'variable:set' });
}

function emitBackendVariables(emitRunEvent: (event: RunEvent) => void, runId: string, variables: RuntimeVariable[] | undefined): void {
  if (variables === undefined) {
    return;
  }
  variables.forEach((variable) => emitVariable(emitRunEvent, runId, variable));
}

function emitBackendArtifacts(activeRun: ActiveBrowserRun, emitRunEvent: (event: RunEvent) => void, runId: string, artifacts: ArtifactSnapshot[] | undefined): void {
  if (artifacts === undefined) {
    return;
  }
  const nextIds = new Set(artifacts.map((artifact) => artifact.artifactId));
  const hasChanged = nextIds.size !== activeRun.artifactIds.size || [...nextIds].some((artifactId) => !activeRun.artifactIds.has(artifactId));
  if (!hasChanged) {
    return;
  }
  activeRun.artifactIds = nextIds;
  emitRunEvent({ payload: { artifacts, runId }, type: 'artifacts:update' });
}

function normalizeRuntimeStatus(status: string): RuntimeStatus {
  if (status === 'success') return 'success';
  if (status === 'stopped') return 'stopped';
  if (status === 'error') return 'error';
  return 'running';
}

function normalizeLogLevel(level: string): RunLogLevel {
  if (level === 'success' || level === 'running' || level === 'warn' || level === 'error' || level === 'input') {
    return level;
  }
  return 'info';
}

function formatLogTime(date: Date): string {
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}.${String(date.getMilliseconds()).padStart(3, '0')}`;
}

function createPreviewPickerResult(payload: PickerOpenPayload | undefined, lastPickerResult: PickerResult | null): PickerResult {
  return {
    capturedAt: new Date().toISOString(),
    confidence: 0.82,
    selector: lastPickerResult?.selector ?? '',
    strategy: 'css',
    text: '浏览器预览模式选择器',
    url: payload?.targetUrl ?? lastPickerResult?.url ?? ''
  };
}

function readFlowName(definition: Record<string, unknown>): string {
  return typeof definition.name === 'string' && definition.name.trim().length > 0 ? definition.name.trim() : '未命名流程';
}

function readFlowVersion(definition: Record<string, unknown>): string {
  return typeof definition.version === 'string' && definition.version.trim().length > 0 ? definition.version.trim() : 'v1.0.0';
}
