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

/** A transient notification surfaced in the UI after a bridge operation completes. */
export type BridgeToast = {
  /** Timestamp-based ID used to deduplicate rapid identical toasts within a short window. */
  id: number;
  type: 'success' | 'error' | 'info';
  message: string;
  /** Optional lucide icon name override (e.g. 'Rocket', 'Download', 'Cpu'). Defaults to type icon. */
  icon?: string;
};

/** Options accepted by bridge action methods; `silent` suppresses toast notifications on both success and error. */
export type BridgeCallOptions = {
  silent?: boolean;
};

/**
 * Full state + action surface returned by `useElectronBridge`. Consumed by the
 * root canvas page; components receive slices via props or context rather than
 * importing this type directly.
 */
export type ElectronBridgeState = {
  /** False when neither the Electron preload nor the browser polyfill is available. */
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
  lastRunId: string | null;
  logs: RunLogEntry[];
  nodeStates: Record<string, NodeRuntimeState>;
  progress: RuntimeProgress;
  queueStats: QueueStats | null;
  runtimeStatus: RuntimeStatus;
  runs: TaskSnapshot[];
  schedules: ScheduleSnapshot[];
  toasts: BridgeToast[];
  inputVariables: RuntimeVariable[];
  /** Variable overrides configured in the last run dialog; persisted across page reloads. */
  lastRunOverrideVariables: RuntimeVariable[];
  /** Merged view rows displayed in the Variables panel. */
  variableViews: RuntimeVariableView[];
  variables: RuntimeVariable[];
  openArtifactPath: (storageUrl: string) => Promise<void>;
  applyFlowTemplate: (template: FlowTemplate) => void;
  openFlow: () => Promise<boolean>;
  openFlowById: (flowId: string) => Promise<void>;
  silentlyRestoreCurrentFlow: (flowId: string) => Promise<void>;
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
  exportLogs: (content: string) => Promise<void>;
  openPicker: (targetUrl?: string) => Promise<void>;
  closePicker: () => Promise<void>;
  startRun: (options?: RunMode | StartRunOptions) => Promise<void>;
  stopRun: () => Promise<void>;
  provideInput: (value: string) => Promise<void>;
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
  pushToast: (type: BridgeToast['type'], message: string, icon?: string) => number;
  dismissToast: (toastId: number) => void;
  clearToast: () => void;
  clearRuns: () => void;
};
