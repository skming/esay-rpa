import type { Edge, Node } from '@xyflow/react';
import type { LucideIcon } from 'lucide-react';

export type NodeStatus = 'done' | 'running' | 'pending' | 'error' | 'skipped';
/** Broad category that determines which action runners handle a node. */
export type NodeKind = 'browser' | 'excel' | 'ui' | 'file' | 'data' | 'script' | 'control' | 'variable';
export type PanelTab = 'config' | 'io' | 'advanced';
export type BottomTab = 'logs' | 'variables' | 'breakpoints' | 'errors' | 'artifacts';
export type CanvasToolMode = 'select' | 'pan';
/** Overall lifecycle state of a running (or finished) task. */
export type RuntimeStatus = 'ready' | 'running' | 'success' | 'stopped' | 'error';
/** Log entry severity; 'input' signals the runner is waiting for user input. */
export type RunLogLevel = 'info' | 'success' | 'running' | 'warn' | 'error' | 'input';
/** HTTP fetching strategy — 'stealthy' uses headless browser with anti-bot measures. */
export type FetcherType = 'static' | 'dynamic' | 'stealthy';
export type ExtractMode = 'text' | 'html' | 'attribute' | 'count' | 'table';
/** Variable lifetime: '全局' persists across nodes; '循环' is per loop iteration; '局部' is per node. */
export type VariableScope = '全局' | '循环' | '局部';
export type VariableCategory = 'flow' | 'environment' | 'credential';
export type ContextMenuAction = 'edit' | 'breakpoint' | 'run-from-here' | 'duplicate' | 'insert-before' | 'insert-after' | 'disable' | 'delete';

/**
 * Flat union of all per-node action parameters. Fields are optional because
 * each action type uses only a subset — see the action runner implementations
 * for which fields each type requires.
 */
export type RpaNodeAction = {
  type: string;
  targetUrl?: string;
  url?: string;
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  headers?: Record<string, string> | string;
  requestBody?: string;
  message?: string;
  channel?: string;
  defaultValue?: string;
  logLevel?: RunLogLevel;
  scope?: VariableScope;
  responseVariable?: string;
  statusVariable?: string;
  jsonVariable?: string;
  resultVariable?: string;
  path?: string;
  scriptPath?: string;
  code?: string;
  filePath?: string;
  targetPath?: string;
  column?: string;
  content?: string;
  rows?: unknown[];
  selector?: string;
  fetcher?: FetcherType;
  extractMode?: ExtractMode;
  attribute?: string;
  adaptive?: boolean;
  autoSave?: boolean;
  continueOnError?: boolean;
  fillMode?: 'js' | 'type';
  timeoutMs?: number;
  inputValue?: string;
  inputVariable?: string;
  operation?: string;
  pattern?: string;
  search?: string;
  replacement?: string;
  delimiter?: string;
  left?: string;
  right?: string;
  leftVariable?: string;
  rightVariable?: string;
  operator?: string;
  variableName?: string;
  value?: string;
  outputVariable?: string;
  appendVariable?: string;
  appendOutputVariable?: string;
  appendMode?: 'record' | 'values';
  countVariable?: string;
  loadedCountVariable?: string;
  pageCountVariable?: string;
  dismissedCountVariable?: string;
  firstValueVariable?: string;
  stderrVariable?: string;
  delayMs?: number;
  distance?: number;
  index?: number;
  targetSelector?: string;
  checked?: boolean;
  itemsVariable?: string;
  itemVariable?: string;
  indexVariable?: string;
  maxIterations?: number;
  retryCount?: number;
  errorVariable?: string;
  flowId?: string;
  command?: string;
};

/** Data payload stored on every React Flow node in the canvas. */
export type RpaNodeData = {
  title: string;
  description: string;
  kind: NodeKind;
  status: NodeStatus;
  /** Short status label displayed as a chip on the node card (e.g. "3 行"). */
  badge?: string;
  /** Number of pre-flight validation issues; drives the warning indicator on the node. */
  validationCount?: number;
  validationSeverity?: 'error' | 'warn';
  action?: RpaNodeAction;
  disabled?: boolean;
  /** When true the runner pauses before executing this node in debug mode. */
  breakpoint?: boolean;
  onAction?: (action: ContextMenuAction) => void;
};

export type RpaNodeConfigDraft = {
  attribute: string;
  autoSave: boolean;
  breakpoint: boolean;
  continueOnError: boolean;
  debugLog: boolean;
  description: string;
  extractMode: ExtractMode;
  inputValue: string;
  inputVariable: string;
  operation: string;
  pattern: string;
  delimiter: string;
  checked: boolean;
  delayMs: number;
  distance: number;
  left: string;
  right: string;
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  message: string;
  channel: string;
  defaultValue: string;
  logLevel: RunLogLevel;
  preScreenshot: boolean;
  command: string;
  errorVariable: string;
  flowId: string;
  fillMode: 'fill' | 'type' | 'js';
  retryCount: number;
  requestBody: string;
  responseVariable: string;
  stderrVariable: string;
  itemVariable: string;
  indexVariable: string;
  maxIterations: number;
  path: string;
  code: string;
  targetPath: string;
  targetSelector: string;
  tabIndex: number;
  column: string;
  content: string;
  statusVariable: string;
  selector: string;
  targetUrl: string;
  timeoutSeconds: number;
  title: string;
  variableName: string;
  variableScope: VariableScope;
};

/** Serialisable point-in-time snapshot of the canvas — nodes + edges. */
export type FlowCanvasSnapshot = {
  nodes: Node<RpaNodeData>[];
  edges: Edge[];
};

export type CanvasToolbarStats = {
  doneSteps: number;
  runningSteps: number;
  totalSteps: number;
};

export type KindStyle = {
  accent: string;
  bg: string;
  border: string;
  pill: string;
  text: string;
  icon: LucideIcon;
  label: string;
};

export type ComponentItem = {
  label: string;
  popular?: boolean;
};

export type ComponentGroup = {
  id: NodeKind;
  label: string;
  icon: LucideIcon;
  items: ComponentItem[];
};

export type ContextMenuState = {
  nodeId: string;
  nodeTitle: string;
} | null;

export type NodeRuntimeState = {
  status: NodeStatus;
  badge?: string;
};

export type RunLogEntry = {
  id: string;
  time: string;
  level: RunLogLevel;
  message: string;
  detail?: string;
  nodeId?: string;
};

export type RuntimeVariable = {
  category?: VariableCategory;
  name: string;
  sensitive?: boolean;
  type: 'String' | 'Integer' | 'Boolean' | 'List' | 'Dict';
  value: string;
  scope: VariableScope;
};

/** Indicates which layer last set the variable's active value. */
export type RuntimeVariableSource = 'default' | 'override' | 'runtime';

/**
 * Enriched variable row shown in the Variables panel. Merges the flow's
 * declared default, any run-time override, and the live value emitted by the
 * backend, exposing all three alongside a `source` discriminator.
 */
export type RuntimeVariableView = RuntimeVariable & {
  defaultValue?: string;
  overrideValue?: string;
  runtimeValue?: string;
  source: RuntimeVariableSource;
};

export type RuntimeProgress = {
  currentStep: number;
  totalSteps: number;
  percent: number;
  elapsedMs: number;
};
