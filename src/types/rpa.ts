import type { Edge, Node } from '@xyflow/react';
import type { LucideIcon } from 'lucide-react';

export type NodeStatus = 'done' | 'running' | 'pending' | 'error' | 'skipped';
export type NodeKind = 'browser' | 'excel' | 'ui' | 'file' | 'data' | 'script' | 'control' | 'variable';
/**
 * 每个 kind 在识别不出具体动作时退到哪个真实类型。
 *
 * 必须是后端执行器认得的类型：拼一个 `<kind>.step` 之类的占位类型不会报错，节点照样能存进
 * 流程文件，但运行时既进不了 executable_nodes 也匹配不到任何 runner，表现是这一步被静默跳过。
 */
export const DEFAULT_ACTION_TYPE_BY_KIND: Record<NodeKind, string> = {
  browser: 'browser.click',
  ui: 'ui.click',
  excel: 'excel.read',
  file: 'file.read',
  data: 'data.json.parse',
  script: 'script.python',
  control: 'control.noop',
  variable: 'variable.set'
};

export type PanelTab = 'config' | 'io' | 'advanced';
export type BottomTab = 'logs' | 'variables' | 'breakpoints' | 'errors' | 'artifacts';
export type CanvasToolMode = 'select' | 'pan';
export type RuntimeStatus = 'ready' | 'running' | 'success' | 'stopped' | 'error' | 'paused_for_human';
// 'input' 表示运行器正等待用户输入
export type RunLogLevel = 'info' | 'success' | 'running' | 'warn' | 'error' | 'input';
// 'stealthy' 使用带反反爬措施的无头浏览器
export type FetcherType = 'static' | 'dynamic' | 'stealthy';
export type ExtractMode = 'text' | 'html' | 'attribute' | 'count' | 'table';
// 全局=跨节点持久；循环=每次循环迭代；局部=仅当前节点
export type VariableScope = '全局' | '循环' | '局部';
export type VariableCategory = 'flow' | 'environment' | 'credential';
export type ContextMenuAction = 'edit' | 'breakpoint' | 'run-from-here' | 'duplicate' | 'insert-before' | 'insert-after' | 'disable' | 'delete';

// 所有节点动作参数的扁平合并；每种 action type 只用其中一部分字段，全部设为可选
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
  /** control.repeat_until 的退出条件表达式（与 control.condition 同语法）。 */
  condition?: string;
  /** control.repeat_until 跑满 maxIterations 仍未满足条件时不失败，仅告警。 */
  continueOnMaxIterations?: boolean;
  retryCount?: number;
  errorVariable?: string;
  flowId?: string;
  command?: string;
  humanTakeoverMessage?: string;
  humanTakeoverResumeMode?: 'next_node' | 'current_node';
  /** 换行分隔的备选 selector，运行时主 selector 未命中会自动逐个尝试。 */
  fallbackSelectors?: string;
  /** 元素可见文字锚点，运行时兜底按文字定位（抗页面改版）。 */
  anchorText?: string;
  /** 期望输出字段声明（JSON 数组），运行时按表头对齐改名、必需字段未命中报错。 */
  outputSchema?: string;
  /** browser.waitFor 使用：等待条件，textContains 时配合 inputValue 作为期望文本。 */
  waitCondition?: 'visible' | 'hidden' | 'textContains';
};

export type RpaNodeData = {
  title: string;
  description: string;
  kind: NodeKind;
  status: NodeStatus;
  badge?: string;
  validationCount?: number;
  validationSeverity?: 'error' | 'warn';
  action?: RpaNodeAction;
  disabled?: boolean;
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
  firstValueVariable: string;
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
  humanTakeoverMessage: string;
  humanTakeoverResumeMode: 'next_node' | 'current_node';
  fallbackSelectors: string;
  anchorText: string;
  outputSchema: string;
  waitCondition: 'visible' | 'hidden' | 'textContains';
};

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

export type RuntimeVariableSource = 'default' | 'override' | 'runtime';

// 合并流程默认值、运行时覆盖值、后端实时值三者，source 标识当前生效值来自哪一层
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
