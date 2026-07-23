import type { Edge, Node } from '@xyflow/react';

import type { RunScope } from '../types/electron';
import type { RpaNodeAction, RpaNodeData } from '../types/rpa';

type ValidationSeverity = 'error' | 'warn';

export type RunValidationIssue = {
  nodeId: string;
  severity: ValidationSeverity;
  message: string;
};

export type RunValidationResult = {
  issues: RunValidationIssue[];
  primaryIssue: RunValidationIssue | null;
};

const EXECUTABLE_ACTION_PREFIXES = ['browser.', 'ui.', 'http.', 'script.', 'data.', 'file.', 'excel.', 'variable.', 'control.'];
// 后端在任务启动时自动注入，运行时始终可用
const RUNTIME_BUILTIN_VARIABLES = new Set(['run_timestamp', 'flow_slug', 'output_dir', 'output_prefix']);
const TEMPLATE_VARIABLE_PATTERN = /\$\{var\.([A-Za-z_][A-Za-z0-9_.-]{0,119})\}/g;
const BARE_VARIABLE_PATTERN = /\b([A-Za-z_][A-Za-z0-9_.-]{0,119})\b/g;
const CONDITION_KEYWORDS = new Set(['true', 'false', 'null', 'none', 'and', 'or', 'not', 'yes', 'no']);
const OUTPUT_ONLY_NODE_TYPES = new Set(['variable.get']);
const CONDITION_TRUE_BRANCH_LABELS = new Set(['1', 'true', 'yes', 'y', '是', '真', '成功', 'then', 'if-true', 'true-branch']);
const CONDITION_FALSE_BRANCH_LABELS = new Set(['0', 'false', 'no', 'n', '否', '假', '失败', 'else', 'if-false', 'false-branch']);
const LOOP_BODY_EDGE_LABELS = new Set(['body', 'loop', 'loop-body', 'foreach-body', 'each', 'iterate', 'true', 'yes', '是', '循环', '循环体', '每项', '迭代']);
const LOOP_EXIT_EDGE_LABELS = new Set(['exit', 'done', 'complete', 'loop-exit', 'foreach-exit', 'false', 'no', '否', '完成', '结束', '退出', '跳出']);
const ABSOLUTE_URL_ACTION_TYPES = new Set(['browser.fetch', 'browser.open', 'browser.tab.open', 'http.request']);

/** Pre-flight validator: reachability, node config, flow structure (conditions/loops), and variable dependencies in topological order. */
export function validateRunConfiguration(
  nodes: Node<RpaNodeData>[],
  edges: Edge[],
  options: {
    availableVariableNames?: string[];
    scope: RunScope;
    startNodeId?: string;
  }
): RunValidationResult {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const issues: RunValidationIssue[] = [];
  const seen = new Set<string>();

  const pushIssue = (issue: RunValidationIssue): void => {
    const key = `${issue.nodeId}:${issue.severity}:${issue.message}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    issues.push(issue);
  };

  const startNodeId = options.scope === 'full' ? 'start' : options.startNodeId;
  if (typeof startNodeId === 'string' && !nodeById.has(startNodeId)) {
    pushIssue({ nodeId: 'start', severity: 'error', message: `运行起点不存在：${startNodeId}` });
    return { issues, primaryIssue: issues[0] ?? null };
  }

  const reachableNodeIds = collectReachableNodeIds(nodes, edges, startNodeId ?? 'start');
  if (reachableNodeIds.length === 0) {
    pushIssue({ nodeId: startNodeId ?? 'start', severity: 'error', message: '当前运行范围内没有可执行节点' });
    return { issues, primaryIssue: issues[0] ?? null };
  }

  const executableNodeIds = reachableNodeIds.filter((nodeId) => isExecutableNode(nodeById.get(nodeId)));
  if (executableNodeIds.length === 0) {
    pushIssue({ nodeId: startNodeId ?? 'start', severity: 'error', message: '当前运行范围内没有可执行动作节点' });
  }

  for (const issue of validateFlowStructure(nodes, edges, reachableNodeIds, options.scope)) {
    pushIssue(issue);
  }

  const variableIssues = validateVariableDependencies(
    reachableNodeIds.map((nodeId) => nodeById.get(nodeId)).filter((node): node is Node<RpaNodeData> => node !== undefined),
    options.availableVariableNames ?? []
  );
  for (const issue of variableIssues) {
    pushIssue(issue);
  }

  for (const nodeId of reachableNodeIds) {
    const node = nodeById.get(nodeId);
    if (node === undefined || node.id === 'start' || node.id === 'end') {
      continue;
    }
    for (const issue of validateNodeConfiguration(node)) {
      pushIssue(issue);
    }
  }

  return {
    issues,
    primaryIssue: issues.find((issue) => issue.severity === 'error') ?? issues[0] ?? null
  };
}

export function getBlockingRunIssue(result: RunValidationResult): RunValidationIssue | null {
  return result.issues.find((issue) => issue.severity === 'error') ?? null;
}

export function validateNodeConfiguration(node: Node<RpaNodeData>): RunValidationIssue[] {
  if (node.id === 'start' || node.id === 'end') {
    return [];
  }
  const action = node.data.action;
  if (action === undefined) {
    return [
      {
        nodeId: node.id,
        severity: 'warn',
        message: `节点“${node.data.title}”缺少动作配置，运行时会被跳过`
      }
    ];
  }
  return validateNodeAction(node, action);
}

/** Like `validateNodeConfiguration` but also scopes flow-structural and variable-dependency issues to this node, for inline panel warnings. */
export function validateNodeConfigurationInFlow(
  node: Node<RpaNodeData>,
  nodes: Node<RpaNodeData>[],
  edges: Edge[],
  availableVariableNames: string[]
): RunValidationIssue[] {
  const baseIssues = validateNodeConfiguration(node);
  const reachableNodeIds = collectReachableNodeIds(nodes, edges, 'start');
  const structureIssues = validateFlowStructure(nodes, edges, reachableNodeIds, 'full').filter((issue) => issue.nodeId === node.id);
  const orderedNodes = reachableNodeIds
    .map((nodeId) => nodes.find((item) => item.id === nodeId))
    .filter((item): item is Node<RpaNodeData> => item !== undefined);
  const variableIssues = validateVariableDependencies(orderedNodes, availableVariableNames).filter((issue) => issue.nodeId === node.id);
  return [...baseIssues, ...structureIssues, ...variableIssues];
}

function collectReachableNodeIds(nodes: Node<RpaNodeData>[], edges: Edge[], startNodeId: string): string[] {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  if (!nodeById.has(startNodeId)) {
    return [];
  }

  const adjacency = new Map<string, string[]>();
  for (const edge of edges) {
    if (!nodeById.has(edge.source) || !nodeById.has(edge.target)) {
      continue;
    }
    const current = adjacency.get(edge.source) ?? [];
    current.push(edge.target);
    adjacency.set(edge.source, current);
  }

  const visited = new Set<string>();
  const ordered: string[] = [];
  const stack = [startNodeId];

  while (stack.length > 0) {
    const current = stack.pop();
    if (current === undefined || visited.has(current)) {
      continue;
    }
    visited.add(current);
    ordered.push(current);
    const next = adjacency.get(current) ?? [];
    for (let index = next.length - 1; index >= 0; index -= 1) {
      stack.push(next[index]);
    }
  }

  return ordered;
}

function isExecutableNode(node: Node<RpaNodeData> | undefined): boolean {
  if (node === undefined) {
    return false;
  }
  if (node.id === 'start' || node.id === 'end') {
    return false;
  }
  const actionType = node.data.action?.type;
  return typeof actionType === 'string' && EXECUTABLE_ACTION_PREFIXES.some((prefix) => actionType.startsWith(prefix));
}

function validateNodeAction(node: Node<RpaNodeData>, action: RpaNodeAction): RunValidationIssue[] {
  const issues: RunValidationIssue[] = [];
  const title = node.data.title;

  const requireField = (value: string | undefined, label: string): void => {
    if (typeof value !== 'string' || value.trim() === '') {
      issues.push({ nodeId: node.id, severity: 'error', message: `节点“${title}”缺少${label}` });
    }
  };

  const type = action.type;
  const actionUrl = action.targetUrl ?? action.url;

  if (type === 'browser.fetch') {
    requireField(action.targetUrl, '目标网址');
    requireField(action.selector, '选择器');
  }

  if (type === 'browser.click' || type === 'browser.wait' || type === 'browser.waitFor' || type === 'browser.extract' || type === 'browser.screenshot' || type === 'ui.click' || type === 'ui.wait' || type === 'ui.extract' || type === 'ui.screenshot') {
    requireField(action.selector, '选择器');
  }

  if (type === 'browser.waitFor' && action.waitCondition === 'textContains') {
    requireField(action.inputValue, '期望文本');
  }

  if (type === 'browser.dismiss') {
    requireField(action.selector, '弹窗候选选择器');
  }

  if (
    (type === 'browser.extract' || type === 'ui.extract' || type === 'browser.clickLoadMore' || type === 'browser.paginateNext') &&
    typeof action.outputSchema === 'string' &&
    action.outputSchema.trim() !== ''
  ) {
    try {
      const parsed: unknown = JSON.parse(action.outputSchema);
      if (!Array.isArray(parsed) || parsed.length === 0) {
        issues.push({ nodeId: node.id, severity: 'error', message: `节点“${title}”的输出字段 Schema 必须是非空 JSON 数组` });
      }
    } catch {
      issues.push({ nodeId: node.id, severity: 'error', message: `节点“${title}”的输出字段 Schema 不是合法 JSON` });
    }
  }

  if (type === 'browser.clickLoadMore' || type === 'browser.paginateNext') {
    requireField(action.selector, type === 'browser.paginateNext' ? '下一页按钮选择器' : '加载按钮选择器');
    requireField(action.targetSelector, '列表项选择器');
  }

  if (type === 'browser.fill' || type === 'ui.fill') {
    requireField(action.selector, '选择器');
    requireField(action.inputValue, '输入内容');
  }

  if (type === 'browser.press') {
    requireField(action.selector, '选择器');
    requireField(action.inputValue, '按键');
  }

  if (type === 'browser.select' || type === 'ui.select') {
    requireField(action.selector, '选择器');
    requireField(action.inputValue, '选项值');
  }

  if (type === 'browser.check' || type === 'ui.check') {
    requireField(action.selector, '选择器');
  }

  if (type === 'browser.drag' || type === 'ui.drag') {
    requireField(action.selector, '源选择器');
    requireField(action.targetSelector, '目标选择器');
  }

  if (type === 'browser.open' || type === 'browser.tab.open' || type === 'browser.ensureLogin') {
    requireField(action.targetUrl ?? action.url, '目标网址');
  }

  if (type === 'http.request') {
    requireField(action.url ?? action.targetUrl, '请求 URL');
  }

  if (ABSOLUTE_URL_ACTION_TYPES.has(type)) {
    validateAbsoluteUrl(actionUrl, node.id, title, issues);
  }

  if (action.timeoutMs !== undefined && !(Number.isFinite(action.timeoutMs) && action.timeoutMs > 0)) {
    issues.push({ nodeId: node.id, severity: 'error', message: `节点“${title}”超时必须大于 0 ms` });
  }

  if (type === 'excel.read' || type === 'excel.write' || type === 'excel.addrow' || type === 'excel.deleterow' || type === 'excel.save' || type === 'excel.filter' || type === 'file.read' || type === 'file.write' || type === 'file.delete' || type === 'file.list' || type === 'file.watch') {
    requireField(action.path, '路径');
  }

  if (type === 'file.copy' || type === 'file.move' || type === 'file.compress' || type === 'file.rename') {
    requireField(action.path, '源路径');
    requireField(action.targetPath, '目标路径');
  }

  if (type === 'script.python' || type === 'script.javascript') {
    requireField(action.path ?? action.scriptPath ?? action.code, '脚本路径或内联代码');
  }

  if (type === 'script.shell') {
    requireField(action.command ?? action.path, 'Shell 命令');
  }

  if (type === 'script.websocket') {
    requireField(action.url, 'WebSocket 地址');
  }

  if (type === 'control.retry') {
    if (!(typeof action.retryCount === 'number' && action.retryCount > 0) && !(typeof action.maxIterations === 'number' && action.maxIterations > 0)) {
      issues.push({ nodeId: node.id, severity: 'error', message: `节点"${title}"重试次数必须大于 0` });
    }
  }

  if (type === 'control.subprocess') {
    requireField(action.flowId, '子流程 ID');
  }

  if (type === 'data.convert' || type === 'data.encrypt') {
    requireField(action.inputValue, '输入值');
  }

  if (type === 'control.condition') {
    requireField(action.inputValue, '条件表达式');
    if (typeof action.inputValue === 'string' && TEMPLATE_VARIABLE_PATTERN.test(action.inputValue)) {
      TEMPLATE_VARIABLE_PATTERN.lastIndex = 0;
      issues.push({
        nodeId: node.id,
        severity: 'error',
        message: `节点“${title}”条件表达式只能使用裸变量名，例如 login_count > 0，不能写 \${var.xxx}`
      });
    }
    TEMPLATE_VARIABLE_PATTERN.lastIndex = 0;
  }

  if (type === 'control.foreach') {
    requireField(action.itemsVariable ?? action.responseVariable, '遍历变量');
    requireField(action.itemVariable, '当前项变量');
  }

  // 退出条件缺失时循环会一直跑到上限再失败，报在校验阶段比运行时才发现划算得多
  if (type === 'control.repeat_until') {
    requireField(action.condition ?? action.inputValue, '退出条件');
  }

  if (type === 'control.delay' && !(typeof action.delayMs === 'number' && Number.isFinite(action.delayMs) && action.delayMs >= 0)) {
    issues.push({ nodeId: node.id, severity: 'error', message: `节点“${title}”缺少有效的延时毫秒数` });
  }

  if (type === 'variable.set' || type === 'variable.assign' || type === 'variable.get' || type === 'variable.input') {
    requireField(action.variableName, '变量名');
  }

  if (type === 'variable.notify' || type === 'variable.log') {
    requireField(action.message, '消息内容');
  }

  if (type === 'variable.notify') {
    requireField(action.channel, '通知通道');
  }

  if (type === 'data.regex.match') {
    requireField(action.pattern, '正则表达式');
  }

  if (type === 'data.math.compute') {
    requireField(action.left, '左操作数');
    requireField(action.right, '右操作数');
  }

  return issues;
}

function validateAbsoluteUrl(value: string | undefined, nodeId: string, title: string, issues: RunValidationIssue[]): void {
  if (typeof value !== 'string') {
    return;
  }
  const normalized = value.trim();
  if (normalized === '' || TEMPLATE_VARIABLE_PATTERN.test(normalized)) {
    TEMPLATE_VARIABLE_PATTERN.lastIndex = 0;
    return;
  }
  TEMPLATE_VARIABLE_PATTERN.lastIndex = 0;

  try {
    const url = new URL(normalized);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') {
      issues.push({ nodeId, severity: 'error', message: `节点“${title}”目标网址必须使用 http 或 https` });
    }
  } catch {
    issues.push({ nodeId, severity: 'error', message: `节点“${title}”目标网址不是有效 URL` });
  }
}

function validateVariableDependencies(nodes: Node<RpaNodeData>[], availableVariableNames: string[]): RunValidationIssue[] {
  const available = new Set(availableVariableNames.map((name) => name.trim()).filter((name) => name.length > 0));
  const issues: RunValidationIssue[] = [];

  for (const node of nodes) {
    if (node.id === 'start' || node.id === 'end') {
      continue;
    }
    const action = node.data.action;
    if (action === undefined) {
      continue;
    }

    const referenced = collectReferencedVariableNames(action);
    const missing = referenced.filter((name) => !isVariableAvailable(name, available));
    if (missing.length > 0) {
      issues.push({
        nodeId: node.id,
        severity: 'error',
        message: `节点“${node.data.title}”引用了未定义变量：${missing.join('、')}`
      });
    }

    for (const produced of collectProducedVariableNames(action)) {
      available.add(produced);
    }
  }

  return issues;
}

/** Dot-path access (e.g. `obj.key`) counts as available when the root name (`obj`) is in the set. */
function isVariableAvailable(name: string, available: Set<string>): boolean {
  if (RUNTIME_BUILTIN_VARIABLES.has(name)) {
    return true;
  }
  if (available.has(name)) {
    return true;
  }
  const rootName = name.split('.')[0];
  return rootName !== undefined && available.has(rootName);
}

function validateFlowStructure(
  nodes: Node<RpaNodeData>[],
  edges: Edge[],
  reachableNodeIds: string[],
  scope: RunScope
): RunValidationIssue[] {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const reachable = new Set(reachableNodeIds);
  const issues: RunValidationIssue[] = [];
  const seen = new Set<string>();
  const adjacency = buildOutgoingEdges(edges, nodeById);

  const pushIssue = (issue: RunValidationIssue): void => {
    const key = `${issue.nodeId}:${issue.severity}:${issue.message}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    issues.push(issue);
  };

  if (scope === 'full') {
    for (const node of nodes) {
      if (node.id === 'start' || node.id === 'end') {
        continue;
      }
      if (!reachable.has(node.id)) {
        pushIssue({
          nodeId: node.id,
          severity: 'warn',
          message: `节点“${node.data.title}”当前从开始节点不可达，运行时不会执行`
        });
      }
    }
  }

  for (const nodeId of reachableNodeIds) {
    const node = nodeById.get(nodeId);
    if (node === undefined || node.id === 'start' || node.id === 'end') {
      continue;
    }

    const outgoingEdges = adjacency.get(node.id) ?? [];
    const actionType = node.data.action?.type;
    if (actionType === 'control.condition') {
      validateConditionStructure(node, outgoingEdges, pushIssue);
    }
    if (actionType === 'control.foreach' || actionType === 'control.loop' || actionType === 'control.for-each' || actionType === 'control.repeat_until') {
      validateLoopStructure(node, outgoingEdges, pushIssue);
    }
  }

  return issues;
}

function validateConditionStructure(
  node: Node<RpaNodeData>,
  outgoingEdges: Edge[],
  pushIssue: (issue: RunValidationIssue) => void
): void {
  if (outgoingEdges.length === 0) {
    pushIssue({
      nodeId: node.id,
      severity: 'error',
      message: `节点“${node.data.title}”缺少分支出口`
    });
    return;
  }

  if (outgoingEdges.length === 1) {
    pushIssue({
      nodeId: node.id,
      severity: 'warn',
      message: `节点“${node.data.title}”只有单条分支，条件结果无法完整覆盖`
    });
    return;
  }

  const classified = outgoingEdges.map(readConditionBranchRole);
  const trueCount = classified.filter((role) => role === 'true').length;
  const falseCount = classified.filter((role) => role === 'false').length;
  if (trueCount === 0 || falseCount === 0) {
    pushIssue({
      nodeId: node.id,
      severity: 'warn',
      message: `节点“${node.data.title}”缺少明确的“是/否”分支标记，将按连线顺序推断`
    });
  }
}

function validateLoopStructure(
  node: Node<RpaNodeData>,
  outgoingEdges: Edge[],
  pushIssue: (issue: RunValidationIssue) => void
): void {
  if (outgoingEdges.length === 0) {
    pushIssue({
      nodeId: node.id,
      severity: 'error',
      message: `节点“${node.data.title}”缺少循环出口`
    });
    return;
  }

  const { bodyEdges, exitEdges } = splitLoopEdges(outgoingEdges);
  if (bodyEdges.length === 0) {
    pushIssue({
      nodeId: node.id,
      severity: 'error',
      message: `节点“${node.data.title}”缺少循环体连线`
    });
  }
  if (exitEdges.length === 0) {
    pushIssue({
      nodeId: node.id,
      severity: 'warn',
      message: `节点“${node.data.title}”缺少退出连线，循环结束后不会进入后续步骤`
    });
  }
}

function buildOutgoingEdges(edges: Edge[], nodeById: Map<string, Node<RpaNodeData>>): Map<string, Edge[]> {
  const adjacency = new Map<string, Edge[]>();
  for (const edge of edges) {
    if (!nodeById.has(edge.source) || !nodeById.has(edge.target)) {
      continue;
    }
    const current = adjacency.get(edge.source) ?? [];
    current.push(edge);
    adjacency.set(edge.source, current);
  }
  return adjacency;
}

function readConditionBranchRole(edge: Edge): 'true' | 'false' | null {
  for (const key of ['label', 'sourceHandle', 'targetHandle'] as const) {
    const rawValue = edge[key];
    if (typeof rawValue !== 'string') {
      continue;
    }
    const value = rawValue.trim().toLowerCase();
    if (CONDITION_TRUE_BRANCH_LABELS.has(value)) {
      return 'true';
    }
    if (CONDITION_FALSE_BRANCH_LABELS.has(value)) {
      return 'false';
    }
  }
  return null;
}

/** Unlabelled edges are treated as body-first when no explicit body edge is found. */
function splitLoopEdges(outgoingEdges: Edge[]): { bodyEdges: Edge[]; exitEdges: Edge[] } {
  const bodyEdges: Edge[] = [];
  const exitEdges: Edge[] = [];
  const unknownEdges: Edge[] = [];

  for (const edge of outgoingEdges) {
    const role = readLoopEdgeRole(edge);
    if (role === 'body') {
      bodyEdges.push(edge);
      continue;
    }
    if (role === 'exit') {
      exitEdges.push(edge);
      continue;
    }
    unknownEdges.push(edge);
  }

  if (bodyEdges.length === 0 && unknownEdges.length > 0) {
    bodyEdges.push(unknownEdges[0]);
    exitEdges.push(...unknownEdges.slice(1));
  } else {
    exitEdges.push(...unknownEdges);
  }

  return { bodyEdges, exitEdges };
}

function readLoopEdgeRole(edge: Edge): 'body' | 'exit' | null {
  for (const key of ['label', 'sourceHandle', 'targetHandle'] as const) {
    const rawValue = edge[key];
    if (typeof rawValue !== 'string') {
      continue;
    }
    const value = rawValue.trim().toLowerCase();
    if (LOOP_BODY_EDGE_LABELS.has(value)) {
      return 'body';
    }
    if (LOOP_EXIT_EDGE_LABELS.has(value)) {
      return 'exit';
    }
  }
  return null;
}

function collectReferencedVariableNames(action: RpaNodeAction): string[] {
  const names = new Set<string>();

  const addTemplateRefs = (value: string | undefined): void => {
    if (typeof value !== 'string' || value.trim() === '') {
      return;
    }
    for (const match of value.matchAll(TEMPLATE_VARIABLE_PATTERN)) {
      const variableName = match[1]?.trim();
      if (variableName !== undefined && variableName.length > 0) {
        names.add(variableName);
      }
    }
  };

  addTemplateRefs(action.targetUrl);
  addTemplateRefs(action.url);
  addTemplateRefs(action.selector);
  addTemplateRefs(action.inputValue);
  addTemplateRefs(action.requestBody);
  addTemplateRefs(action.message);
  addTemplateRefs(action.content);
  addTemplateRefs(action.defaultValue);
  addTemplateRefs(action.left);
  addTemplateRefs(action.right);
  addTemplateRefs(action.pattern);
  addTemplateRefs(action.delimiter);
  addTemplateRefs(action.targetSelector);
  addTemplateRefs(action.channel);

  for (const variableName of [action.inputVariable, action.itemsVariable, action.leftVariable, action.rightVariable]) {
    if (typeof variableName === 'string' && variableName.trim() !== '') {
      names.add(variableName.trim());
    }
  }

  if (action.type === 'variable.get' && typeof action.variableName === 'string' && action.variableName.trim() !== '') {
    names.add(action.variableName.trim());
  }

  if (action.type === 'control.condition' && typeof action.inputValue === 'string') {
    for (const variableName of extractConditionVariables(action.inputValue)) {
      names.add(variableName);
    }
  }

  return [...names];
}

function collectProducedVariableNames(action: RpaNodeAction): string[] {
  const names = new Set<string>();
  const add = (value: string | undefined): void => {
    if (typeof value === 'string' && value.trim() !== '') {
      names.add(value.trim());
    }
  };

  add(action.outputVariable ?? action.responseVariable);
  add(action.resultVariable);
  add(action.firstValueVariable);
  add(action.countVariable);
  add(action.loadedCountVariable);
  add(action.pageCountVariable);
  add(action.dismissedCountVariable);
  add(action.statusVariable);
  add(action.jsonVariable);
  add(action.stderrVariable);
  add(action.appendVariable ?? action.appendOutputVariable);

  if (action.type === 'control.foreach') {
    add(action.itemVariable);
    add(action.indexVariable);
  }

  if (action.type === 'control.repeat_until') {
    add(action.indexVariable);
  }

  if (action.type === 'variable.set' || action.type === 'variable.assign' || action.type === 'variable.input') {
    add(action.variableName);
  }

  if (action.type === 'variable.clipboard') {
    add('clipboard_text');
  }

  if (action.type === 'control.try') {
    add(action.errorVariable);
  }

  if (OUTPUT_ONLY_NODE_TYPES.has(action.type)) {
    add(action.outputVariable ?? action.responseVariable);
  }

  return [...names];
}

/** Strips `${var.x}` refs and string literals first, then extracts bare identifiers, excluding keywords/numeric literals. */
function extractConditionVariables(expression: string): string[] {
  const stripped = expression
    .replace(TEMPLATE_VARIABLE_PATTERN, ' ')
    .replace(/(["']).*?\1/g, ' ')
    .replace(/==|!=|>=|<=|>|</g, ' ');
  const names = new Set<string>();

  for (const match of stripped.matchAll(BARE_VARIABLE_PATTERN)) {
    const value = match[1]?.trim();
    if (value === undefined || value.length === 0) {
      continue;
    }
    const lowerValue = value.toLowerCase();
    if (CONDITION_KEYWORDS.has(lowerValue) || /^-?(?:\d+\.?\d*|\.\d+)$/.test(value)) {
      continue;
    }
    names.add(value);
  }

  return [...names];
}
