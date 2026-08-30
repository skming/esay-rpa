import type { NodeRuntimeState, RunLogEntry, RuntimeProgress, RuntimeStatus, RuntimeVariable } from './rpa';

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

export type CustomModelEntry = {
  id: string;
  label: string;
  provider: string;
  env_key: string;
  base_url: string;
};

export type AiConfig = {
  default_model: string;
  api_keys: Record<string, string>;
  base_urls?: Record<string, string>;
  provider_models?: Record<string, Array<{ id: string; label: string }>>;
  custom_models?: CustomModelEntry[];
};

export type AiConfigPatch = {
  default_model?: string;
  api_keys?: Record<string, string>;
  base_urls?: Record<string, string>;
  provider_models?: Record<string, Array<{ id: string; label: string }>>;
  custom_models?: CustomModelEntry[];
};

export type AiModelMeta = {
  id: string;
  label: string;
  provider: string;
  provider_label?: string;
  env_key?: string;
  context_window: number;
  recommended?: boolean;
  local?: boolean;
  no_vision?: boolean;
  /** 已被同厂商新版取代：仍可选用，但在选择器里排到分组末尾并标注 */
  legacy?: boolean;
  badge?: string;
  tier?: string;
  custom?: boolean;
  configured: boolean;
};

export type AiModelCatalogPatch = {
  id: string;
  label?: string;
  provider: string;
  env_key: string;
  context_window?: number;
  recommended?: boolean;
  no_vision?: boolean;
  local?: boolean;
  tier?: string;
};

export type AiModelCatalogUpdatePatch = {
  id: string;
  label?: string;
  context_window?: number;
  tier?: string;
  recommended?: boolean;
};

/** 设置页的厂商分组。后端单独给，不从 models 推：某厂商被删空后分组不能跟着消失。 */
export type AiProviderGroupMeta = { id: string; label: string; env_key: string };

export type AiModelsResult = {
  models: AiModelMeta[];
  default: string;
  /** 老后端不返回此字段，前端回退到按 models 推导 */
  providers?: AiProviderGroupMeta[];
};

export type AiModelTestPayload = {
  env_key: string;
  model: string;
  api_key?: string;
  base_url?: string;
};

export type AiModelTestResult = {
  ok: boolean;
  latency_ms?: number;
  model?: string;
  served_by?: string | null;
  error?: string;
};

// source: 'managed' = Electron 拉起的进程，'external' = 发现的已运行进程
export type BackendServiceStatus = {
  status: 'idle' | 'checking' | 'installing-browser' | 'starting' | 'ready' | 'error' | 'stopped';
  source: 'unknown' | 'external' | 'managed' | 'missing';
  managed: boolean;
  pid: number | null;
  url: string;
  error: string | null;
  installProgress: number | null;
  /** 当前正在下载的产物序号（如 Playwright 依次下载 Chromium/FFmpeg/Headless Shell 时为 1/2/3）。*/
  installStep: number | null;
  /** 当前产物的友好名称，如 "Chromium 浏览器内核"。*/
  installStepLabel: string | null;
  /** 已知的产物总数，仅作文案提示，不保证长期准确。*/
  installStepTotal: number | null;
};

export type ExtensionInstallInfo = {
  found: boolean;
  unpackedDir: string | null;
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
  mode: 'selector-picker' | 'browse';
};

export type PickerOpenPayload = {
  targetUrl?: string;
  mode?: 'pick' | 'browse';
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
export type RunScope = 'full' | 'from-selection' | 'selected-only';
export type RunFailureStrategy = 'stop' | 'continue' | 'retry';
export type DebugControlCommand = 'continue' | 'step-over' | 'step-into';
// playwright = 一次性隔离环境；extension = 通过扩展桥接用户真实 Chrome
export type BrowserExecutorKind = 'playwright' | 'extension';

export type RunStartPayload = {
  mode: RunMode;
  browserExecutor?: BrowserExecutorKind;
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
  flowId?: string | null;
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
  flowDefinition: Record<string, unknown>;
  targetUrl?: string;
  selector?: string;
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

export type DeliverableContract = {
  id: string;
  variable: string;
  kind: 'table' | 'document' | 'file' | 'scalar';
  required?: boolean;
  minRows?: number | null;
  maxRows?: number | null;
  requiredFields?: string[];
  dateRanges?: Array<{ field: string; start?: string | null; end?: string | null }>;
  allowedValues?: Array<{ field: string; values: string[] }>;
  uniqueBy?: string[];
  minChars?: number | null;
  requiredTerms?: string[];
  forbiddenTerms?: string[];
  sourceVariables?: string[];
  extensions?: string[];
  minBytes?: number | null;
  requirementIds?: string[];
  numericRanges?: Array<{ field: string; minimum?: number | null; maximum?: number | null }>;
  fieldFormats?: Array<{ field: string; format: 'integer' | 'decimal' | 'email' | 'url' | 'date' | 'datetime' | 'non_empty' }>;
  crossFieldAssertions?: Array<{ leftField: string; operator: 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte'; rightField: string }>;
  sortAssertions?: Array<{ field: string; direction: 'asc' | 'desc' }>;
  aggregateAssertions?: Array<{ field?: string | null; operation: 'count' | 'sum' | 'avg' | 'min' | 'max'; operator: 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte'; expected: number; tolerance?: number }>;
  expectedCountVariable?: string | null;
  minimumCoverageRatio?: number;
};

export type RequirementClause = {
  id: string;
  description: string;
  sourceKind: 'user' | 'product_default';
  sourceQuote?: string | null;
  sourceTurnId?: string | null;
  confidence: number;
  confirmed: boolean;
};

export type FlowAcceptanceContract = {
  requirements: RequirementClause[];
  deliverables: DeliverableContract[];
};

export type FlowVersionSnapshot = {
  version: string;
  description?: string | null;
  definition: Record<string, unknown>;
  inputVariables: RuntimeVariable[];
  acceptanceContract?: FlowAcceptanceContract;
  revision?: number;
  savedAt: string;
};

export type FlowSnapshot = {
  flowId: string;
  name: string;
  version: string;
  description?: string | null;
  definition: Record<string, unknown>;
  inputVariables: RuntimeVariable[];
  acceptanceContract?: FlowAcceptanceContract;
  revision?: number;
  status: FlowStatus;
  folderPath: string;
  defaultBrowserExecutor?: BrowserExecutorKind;
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
  acceptanceContract?: FlowAcceptanceContract;
  status: FlowStatus;
  folderPath?: string;
  defaultBrowserExecutor?: BrowserExecutorKind;
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

// riskLevel 反映页面抗抓取的可能性（JS 重度渲染、反爬机制等）
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
  lastError?: string | null;
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
  status: 'queued' | 'running' | 'success' | 'stopped' | 'error' | 'paused_for_human';
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
  flowRevision?: number | null;
  definitionDigest?: string | null;
  acceptanceContract?: FlowAcceptanceContract;
  executionEvidence?: Array<{
    nodeId: string;
    nodeType: string;
    inputs: Array<Record<string, unknown>>;
    outputs: Array<Record<string, unknown>>;
    unchangedPairs: string[];
    status: 'success' | 'error';
    durationMs: number;
    browserUrl?: string | null;
    selector?: string | null;
    matchCount?: number | null;
  }>;
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

// window.rpaBridge；纯浏览器开发模式下由 polyfill 注入
export type RpaBridge = {
  openPicker: (payload?: PickerOpenPayload) => Promise<BridgeResult<PickerOpenResult>>;
  closePicker: () => Promise<BridgeResult<PickerCloseResult>>;
  openFlow: () => Promise<BridgeResult<FlowFileResult>>;
  saveFlow: (payload: SaveFlowPayload) => Promise<BridgeResult<FlowFileResult>>;
  exportLogs: (payload: ExportLogsPayload) => Promise<BridgeResult<FlowFileResult>>;
  startRun: (payload: RunStartPayload) => Promise<BridgeResult<RunStartResult>>;
  stopRun: (runId?: string) => Promise<BridgeResult<RunStopResult>>;
  provideInput: (runId: string, value: string) => Promise<BridgeResult<void>>;
  resumeHumanTakeover: (runId: string, resumeMode: string) => Promise<BridgeResult<void>>;
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
  runFlow: (flowId: string, payload: Pick<RunStartPayload, 'browserExecutor' | 'concurrency' | 'failureStrategy' | 'mode' | 'scope' | 'screenshot' | 'startNodeId' | 'variables'>) => Promise<BridgeResult<TaskSnapshot>>;
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
  getExtensionInstallInfo: () => Promise<BridgeResult<ExtensionInstallInfo>>;
  openExtensionFolder: () => Promise<BridgeResult<{ opened: string }>>;
  openChromeExtensionsPage: () => Promise<BridgeResult<{ opened: boolean; reason?: string }>>;
  getAiConfig: () => Promise<BridgeResult<AiConfig>>;
  setAiConfig: (payload: AiConfigPatch) => Promise<BridgeResult<AiConfig>>;
  listAiModels: () => Promise<BridgeResult<AiModelsResult>>;
  addAiModel: (payload: AiModelCatalogPatch) => Promise<BridgeResult<AiModelsResult>>;
  updateAiModel: (payload: AiModelCatalogUpdatePatch) => Promise<BridgeResult<AiModelsResult>>;
  deleteAiModel: (modelId: string) => Promise<BridgeResult<AiModelsResult>>;
  testAiModel: (payload: AiModelTestPayload) => Promise<BridgeResult<AiModelTestResult>>;
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
