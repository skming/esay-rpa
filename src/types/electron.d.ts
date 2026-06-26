import type { NodeRuntimeState, RunLogEntry, RuntimeProgress, RuntimeStatus, RuntimeVariable } from './rpa';

/** Standard envelope returned by every Electron IPC bridge call. */
export type BridgeResult<T> = {
  ok: boolean;
  data?: T;
  error?: string;
};

export type AppInfo = {
  version: string;
  platform: string;
  arch: string;
  hostname: string;
  appDataDir?: string;
};

export type AppUpdateStatus = {
  status: 'idle' | 'checking' | 'available' | 'not-available' | 'downloading' | 'ready' | 'error';
  /** Latest available version (when status is available / not-available / ready). */
  version?: string;
  /** Download progress 0–100 (when status is downloading). */
  percent?: number;
  bytesPerSecond?: number;
  transferred?: number;
  total?: number;
  releaseDate?: string;
  releaseNotes?: string | null;
  /** Machine-readable error token or message (when status is error). */
  error?: string;
};

export type AiConfig = {
  default_model: string;
  api_keys: Record<string, string>;
  base_urls?: Record<string, string>;
};

export type AiConfigPatch = {
  default_model?: string;
  api_keys?: Record<string, string>;
  base_urls?: Record<string, string>;
};

export type AiModelMeta = {
  id: string;
  label: string;
  provider: string;
  env_key?: string;
  context_window: number;
  recommended?: boolean;
  local?: boolean;
  configured: boolean;
};

export type AiModelsResult = {
  models: AiModelMeta[];
  default: string;
};

/**
 * Live status of the Python backend process. `source` indicates whether the
 * process was spawned by Electron ('managed') or discovered already running
 * ('external').
 */
export type BackendServiceStatus = {
  status: 'idle' | 'checking' | 'starting' | 'ready' | 'error' | 'stopped';
  source: 'unknown' | 'external' | 'managed' | 'missing';
  managed: boolean;
  pid: number | null;
  url: string;
  error: string | null;
};

export type FlowFileResult = {
  canceled: boolean;
  path?: string;
  name?: string;
  content?: string;
};

export type SaveFlowPayload = {
  suggestedName: string;
  content: string;
};

export type ExportLogsPayload = {
  content: string;
  filename?: string;
};

/** Result emitted by the visual selector picker after the user clicks an element. */
export type PickerResult = {
  selector: string;
  strategy: 'css' | 'xpath' | 'text';
  /** 0–100 score indicating how stable/unique the generated selector is. */
  confidence: number;
  text: string;
  url: string;
  capturedAt: string;
};

export type PickerOpenResult = {
  status: 'ready';
  mode: 'selector-picker';
};

export type PickerOpenPayload = {
  targetUrl?: string;
};

export type PickerCloseResult = {
  status: 'closed';
};

export type WindowStateResult = {
  minimized?: boolean;
  maximized?: boolean;
  closed?: boolean;
};

export type RunMode = 'run' | 'debug';
/** Which nodes are included in a run: all, from a chosen node onward, or only the selected node. */
export type RunScope = 'full' | 'from-selection' | 'selected-only';
export type RunFailureStrategy = 'stop' | 'continue' | 'retry';
export type DebugControlCommand = 'continue' | 'step-over' | 'step-into';

export type RunStartPayload = {
  mode: RunMode;
  concurrency?: number;
  failureStrategy?: RunFailureStrategy;
  flowDefinition?: Record<string, unknown>;
  flowId?: string;
  flowName: string;
  scope?: RunScope;
  screenshot?: boolean;
  startNodeId?: string;
  targetUrl?: string;
  selector?: string;
  timeoutMs?: number;
  adaptive?: boolean;
  autoSave?: boolean;
  overrideVariables?: Record<string, unknown>;
  variables?: Record<string, unknown>;
};

export type RunStartResult = {
  runId: string;
  status: RuntimeStatus;
  totalSteps: number;
  startedAt: string;
  flowName: string;
};

export type RunStopResult = {
  stopped: boolean;
  runId?: string;
  status: RuntimeStatus;
};

export type RunDebugResult = {
  runId: string;
  status: RuntimeStatus;
};

/** Discriminated union of all real-time events pushed from the backend runner via the bridge. */
export type RunEvent =
  | { type: 'run:start'; payload: RunStartResult }
  | { type: 'run:progress'; payload: RuntimeProgress & { runId: string } }
  | { type: 'node:update'; payload: NodeRuntimeState & { nodeId: string; runId: string } }
  | { type: 'log:append'; payload: RunLogEntry & { runId: string } }
  | { type: 'variable:set'; payload: RuntimeVariable & { runId: string } }
  | { type: 'artifacts:update'; payload: { runId: string; artifacts: ArtifactSnapshot[] } }
  | { type: 'run:finish'; payload: { runId: string; status: RuntimeStatus; finishedAt: string; message: string } };

export type GenerateScriptPayload = {
  flowName: string;
  targetUrl: string;
  selector: string;
  extractMode?: 'text' | 'html' | 'attribute' | 'count' | 'table';
  fetcher?: 'static' | 'dynamic' | 'stealthy';
  attribute?: string;
  adaptive?: boolean;
  autoSave?: boolean;
};

export type GeneratedScriptResult = {
  filename: string;
  language: 'python';
  dependencies: string[];
  content: string;
};

export type FlowStatus = 'draft' | 'active' | 'paused' | 'disabled' | 'archived';

export type FlowVersionSnapshot = {
  version: string;
  description?: string | null;
  definition: Record<string, unknown>;
  inputVariables: RuntimeVariable[];
  savedAt: string;
};

export type FlowSnapshot = {
  flowId: string;
  name: string;
  version: string;
  description?: string | null;
  definition: Record<string, unknown>;
  inputVariables: RuntimeVariable[];
  status: FlowStatus;
  folderPath: string;
  lastRunStatus?: string | null;
  lastRunAt?: string | null;
  successRate30d?: number | null;
  createdAt: string;
  updatedAt: string;
  snapshots: FlowVersionSnapshot[];
};

export type FlowSavePayload = {
  name: string;
  version: string;
  description?: string;
  definition: Record<string, unknown>;
  inputVariables: RuntimeVariable[];
  status: FlowStatus;
  folderPath?: string;
};

export type FlowUpdatePayload = Partial<FlowSavePayload>;

export type AnalyzeSitePayload = {
  targetUrl: string;
  selector?: string;
  fetcher?: 'static' | 'dynamic' | 'stealthy';
  timeoutMs?: number;
  maxCandidates?: number;
};

export type SelectorCandidate = {
  selector: string;
  matchCount: number;
  sampleText: string;
  stabilityScore: number;
  reasons: string[];
};

export type SelectorCheck = {
  selector: string;
  matchCount: number;
  sampleTexts: string[];
  stable: boolean;
};

/**
 * Result returned by the site-analysis endpoint. `riskLevel` reflects how
 * likely the page is to resist scraping (JS-heavy, anti-bot, etc.).
 */
export type SiteAnalysisResult = {
  url: string;
  title?: string | null;
  fetcher: 'static' | 'dynamic' | 'stealthy';
  hasCssInJs: boolean;
  riskLevel: 'low' | 'medium' | 'high';
  warnings: string[];
  checkedSelector?: SelectorCheck | null;
  candidates: SelectorCandidate[];
};

export type ScheduleTaskPayload = RunStartPayload & {
  timeoutMs?: number;
};

export type ScheduleCreatePayload = {
  name: string;
  cronExpression: string;
  timezone: string;
  enabled: boolean;
  task: ScheduleTaskPayload;
};

export type ScheduleUpdatePayload = Partial<Omit<ScheduleCreatePayload, 'task'>> & {
  task?: ScheduleTaskPayload;
};

export type ScheduleSnapshot = {
  scheduleId: string;
  name: string;
  cronExpression: string;
  timezone: string;
  status: 'enabled' | 'disabled';
  task: ScheduleTaskPayload;
  createdAt: string;
  updatedAt: string;
  lastRunAt?: string | null;
  nextRunAt?: string | null;
  lastTaskId?: string | null;
};

export type ScheduleTriggerResult = {
  schedule: ScheduleSnapshot;
  run?: RunStartResult | null;
};

export type ArtifactSnapshot = {
  artifactId: string;
  taskId: string;
  artifactType: 'script' | 'screenshot' | 'report' | 'dataset' | 'log';
  filename: string;
  storageUrl: string;
  contentType: string;
  sizeBytes: number;
  createdAt: string;
  metadata: Record<string, string | number | boolean | null>;
};

export type ArtifactContent = {
  artifact: ArtifactSnapshot;
  content: string;
};

export type QueueStats = {
  backend: 'memory' | 'redis';
  concurrency: number;
  queuedCount: number;
  activeCount: number;
  activeTaskIds: string[];
  started: boolean;
};

export type TaskSnapshot = {
  taskId: string;
  flowId?: string | null;
  flowName: string;
  status: 'queued' | 'running' | 'success' | 'stopped' | 'error';
  mode: RunMode;
  runConfig: {
    scope: RunScope;
    startNodeId?: string | null;
    failureStrategy: RunFailureStrategy;
    screenshot: boolean;
    concurrency: number;
  };
  progress: {
    currentStep: number;
    totalSteps: number;
    percent: number;
    elapsedMs: number;
  };
  createdAt: string;
  updatedAt: string;
  result?: {
    url: string;
    selector: string;
    count: number;
    values: string[];
  } | null;
  variables?: RuntimeVariable[];
  artifacts?: ArtifactSnapshot[];
  error?: string | null;
  inputPrompt?: string | null;
};

export type BackendTaskLogEntry = {
  id: string;
  taskId?: string;
  time: string;
  level: string;
  message: string;
  detail?: string | null;
  nodeId?: string | null;
};

/**
 * The RPA bridge API exposed on `window.rpaBridge` by the Electron preload
 * script. In the browser-only dev mode a polyfill is injected instead.
 */
export type RpaBridge = {
  openPicker: (payload?: PickerOpenPayload) => Promise<BridgeResult<PickerOpenResult>>;
  closePicker: () => Promise<BridgeResult<PickerCloseResult>>;
  openFlow: () => Promise<BridgeResult<FlowFileResult>>;
  saveFlow: (payload: SaveFlowPayload) => Promise<BridgeResult<FlowFileResult>>;
  exportLogs: (payload: ExportLogsPayload) => Promise<BridgeResult<FlowFileResult>>;
  startRun: (payload: RunStartPayload) => Promise<BridgeResult<RunStartResult>>;
  stopRun: (runId?: string) => Promise<BridgeResult<RunStopResult>>;
  provideInput: (runId: string, value: string) => Promise<BridgeResult<void>>;
  debugRun: (runId: string, command: DebugControlCommand) => Promise<BridgeResult<RunDebugResult>>;
  listRuns: (options?: { flowId?: string; limit?: number }) => Promise<BridgeResult<TaskSnapshot[]>>;
  listFlowRuns: (flowId: string, options?: { limit?: number }) => Promise<BridgeResult<TaskSnapshot[]>>;
  generateScraplingScript: (payload: GenerateScriptPayload) => Promise<BridgeResult<GeneratedScriptResult>>;
  analyzeSite: (payload: AnalyzeSitePayload) => Promise<BridgeResult<SiteAnalysisResult>>;
  listFlows: () => Promise<BridgeResult<FlowSnapshot[]>>;
  createFlow: (payload: FlowSavePayload) => Promise<BridgeResult<FlowSnapshot>>;
  updateFlow: (flowId: string, payload: FlowUpdatePayload) => Promise<BridgeResult<FlowSnapshot>>;
  duplicateFlow: (flowId: string) => Promise<BridgeResult<FlowSnapshot>>;
  moveFlow: (flowId: string, folderPath: string) => Promise<BridgeResult<FlowSnapshot>>;
  setFlowStatus: (flowId: string, status: FlowStatus) => Promise<BridgeResult<FlowSnapshot>>;
  archiveFlow: (flowId: string) => Promise<BridgeResult<FlowSnapshot>>;
  deleteFlow: (flowId: string) => Promise<BridgeResult<{ deleted: boolean }>>;
  runFlow: (flowId: string, payload: Pick<RunStartPayload, 'concurrency' | 'failureStrategy' | 'mode' | 'scope' | 'screenshot' | 'startNodeId' | 'variables'>) => Promise<BridgeResult<TaskSnapshot>>;
  listTaskVariables: (taskId: string) => Promise<BridgeResult<RuntimeVariable[]>>;
  listArtifacts: (taskId: string) => Promise<BridgeResult<ArtifactSnapshot[]>>;
  readArtifact: (taskId: string, artifactId: string) => Promise<BridgeResult<ArtifactContent>>;
  getQueueStats: () => Promise<BridgeResult<QueueStats>>;
  listSchedules: () => Promise<BridgeResult<ScheduleSnapshot[]>>;
  createSchedule: (payload: ScheduleCreatePayload) => Promise<BridgeResult<ScheduleSnapshot>>;
  updateSchedule: (scheduleId: string, payload: ScheduleUpdatePayload) => Promise<BridgeResult<ScheduleSnapshot>>;
  deleteSchedule: (scheduleId: string) => Promise<BridgeResult<{ deleted: boolean }>>;
  triggerSchedule: (scheduleId: string) => Promise<BridgeResult<ScheduleTriggerResult>>;
  getWindowId: () => Promise<BridgeResult<number | null>>;
  getAppVersion: () => Promise<BridgeResult<AppInfo>>;
  checkForUpdates: () => Promise<BridgeResult<null>>;
  downloadUpdate: () => Promise<BridgeResult<null>>;
  quitAndInstall: () => Promise<BridgeResult<null>>;
  onUpdateStatus: (callback: (status: AppUpdateStatus) => void) => () => void;
  openDataDir: (subDir?: string) => Promise<BridgeResult<{ opened: string }>>;
  showInFinder: (filePath: string) => Promise<BridgeResult<{ opened: string }>>;
  getBackendStatus: () => Promise<BridgeResult<BackendServiceStatus>>;
  restartBackend: () => Promise<BridgeResult<BackendServiceStatus>>;
  getAiConfig: () => Promise<BridgeResult<AiConfig>>;
  setAiConfig: (payload: AiConfigPatch) => Promise<BridgeResult<AiConfig>>;
  listAiModels: () => Promise<BridgeResult<AiModelsResult>>;
  minimizeWindow: () => Promise<BridgeResult<WindowStateResult>>;
  toggleMaximizeWindow: () => Promise<BridgeResult<WindowStateResult>>;
  closeWindow: () => Promise<BridgeResult<WindowStateResult>>;
  onPickerResult: (callback: (selector: PickerResult) => void) => () => void;
  onPickerCancel: (callback: () => void) => () => void;
  onRunEvent: (callback: (event: RunEvent) => void) => () => void;
  onBackendStatusChanged: (callback: (status: BackendServiceStatus) => void) => () => void;
};

declare global {
  interface Window {
    rpaBridge?: RpaBridge;
  }
}
