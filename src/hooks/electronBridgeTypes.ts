import type {
  AppInfo,
  ArtifactContent,
  ArtifactSnapshot,
  BackendServiceStatus,
  FlowSnapshot,
  GeneratedScriptResult,
  PickerResult,
  QueueStats,
  RunMode,
  ScheduleSnapshot,
  SiteAnalysisResult,
  TaskSnapshot
} from '../types/electron';
import type { CreateScheduleOptions, StartRunOptions } from './useElectronBridgeActions';
import type { FlowTemplate } from '../lib/flowTemplates';
import type { NodeRuntimeState, RunLogEntry, RuntimeProgress, RuntimeStatus, RuntimeVariable, RuntimeVariableView } from '../types/rpa';

export type DebugControlCommand = 'continue' | 'step-over' | 'step-into';

export type BridgeToast = {
  id: number;
  type: 'success' | 'error' | 'info';
  message: string;
};

/** silent 抑制成功/失败 toast 提示 */
export type BridgeCallOptions = {
  silent?: boolean;
};

export type ElectronBridgeState = {
  available: boolean;
  appInfo: AppInfo | null;
  backendStatus: BackendServiceStatus | null;
  generatedScript: GeneratedScriptResult | null;
  flows: FlowSnapshot[];
  currentFlow: FlowSnapshot | null;
  siteAnalysis: SiteAnalysisResult | null;
  artifacts: ArtifactSnapshot[];
  artifactContent: ArtifactContent | null;
  windowId: number | null;
  lastPickerResult: PickerResult | null;
  pickerActive: boolean;
  inputPrompt: string | null;
  humanTakeoverMessage: string | null;
  pausedPageUrl: string | null;
  activeRunFlowId: string | null;
  lastRunId: string | null;
  logs: RunLogEntry[];
  canvasFitVersion: number;
  nodeStates: Record<string, NodeRuntimeState>;
  progress: RuntimeProgress;
  queueStats: QueueStats | null;
  runtimeStatus: RuntimeStatus;
  runs: TaskSnapshot[];
  schedules: ScheduleSnapshot[];
  toasts: BridgeToast[];
  inputVariables: RuntimeVariable[];
  lastRunOverrideVariables: RuntimeVariable[];
  variableViews: RuntimeVariableView[];
  variables: RuntimeVariable[];
  openArtifactPath: (storageUrl: string) => Promise<void>;
  applyFlowTemplate: (template: FlowTemplate) => void;
  openFlow: () => Promise<boolean>;
  openFlowById: (flowId: string) => Promise<void>;
  silentlyRestoreCurrentFlow: (flowId: string, options?: { restoreCanvas?: boolean }) => Promise<void>;
  applyAiFlowUpdate: (flowId: string) => Promise<void>;
  rollbackFlowById: (flowId: string) => Promise<void>;
  exportFlow: () => Promise<void>;
  exportFlowById: (flowId: string) => Promise<void>;
  saveFlow: () => Promise<void>;
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
  setDefaultBrowserExecutor: (browserExecutor: import('../types/electron').BrowserExecutorKind) => Promise<void>;
  exportLogs: (content: string) => Promise<void>;
  openPicker: (targetUrl?: string, type?: 'pick' | 'browse') => Promise<void>;
  closePicker: () => Promise<void>;
  startRun: (options?: RunMode | StartRunOptions) => Promise<void>;
  stopRun: () => Promise<void>;
  provideInput: (value: string) => Promise<void>;
  resumeHumanTakeover: (resumeMode: string) => Promise<void>;
  debugControl: (command: DebugControlCommand) => void;
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
  updateSchedule: (scheduleId: string, options: import('./useElectronBridgeActions').CreateScheduleOptions) => Promise<void>;
  deleteSchedule: (scheduleId: string) => Promise<void>;
  triggerSchedule: (scheduleId: string) => Promise<void>;
  refreshBackendStatus: () => Promise<void>;
  restartBackend: () => Promise<void>;
  minimizeWindow: () => Promise<void>;
  toggleMaximizeWindow: () => Promise<void>;
  closeWindow: () => Promise<void>;
  pushToast: (type: BridgeToast['type'], message: string) => number;
  dismissToast: (toastId: number) => void;
  clearToast: () => void;
  clearRuns: () => void;
};
