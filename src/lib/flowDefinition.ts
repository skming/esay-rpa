import { MarkerType, type Edge, type Node } from '@xyflow/react';

import { initialEdges, initialNodes, kindStyles } from '../data/studioData';
import { DEFAULT_ACTION_TYPE_BY_KIND, type NodeKind, type NodeStatus, type RpaNodeAction, type RpaNodeData, type RunLogLevel, type RuntimeVariable, type VariableCategory, type VariableScope } from '../types/rpa';

/** 当前流程定义文件格式版本，用于向后兼容校验。 */
export const FLOW_DEFINITION_VERSION = '1.0.0';

/** 将画布状态序列化为可持久化的流程定义对象，用于保存、导出、发送给后端执行。 */
export function buildFlowDefinition(
  nodes: Node<RpaNodeData>[] = initialNodes,
  edges: Edge[] = initialEdges,
  inputVariables: RuntimeVariable[] = [],
  name = '未命名流程'
): Record<string, unknown> {
  return {
    version: FLOW_DEFINITION_VERSION,
    name,
    inputVariables: inputVariables.map((variable) => ({
      category: variable.category ?? 'flow',
      name: variable.name,
      sensitive: variable.sensitive === true ? true : undefined,
      type: variable.type,
      value: variable.value,
      scope: variable.scope
    })),
    nodes: nodes.map((node) => {
      const action = node.data.action ?? inferActionFromNode(node);
      return {
        ...action,
        id: node.id,
        title: node.data.title,
        description: node.data.description,
        kind: node.data.kind,
        status: node.data.status,
        disabled: node.data.disabled === true ? true : undefined,
        breakpoint: node.data.breakpoint === true ? true : undefined,
        position: {
          x: Math.round(node.position.x),
          y: Math.round(node.position.y)
        }
      };
    }),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle ?? undefined,
      targetHandle: edge.targetHandle ?? undefined,
      label: typeof edge.label === 'string' ? edge.label : undefined
    })),
    exportedAt: new Date().toISOString()
  };
}

/** 将画布状态序列化为 JSON 字符串，供文件导出使用。@see buildFlowDefinition */
export function serializeFlowDefinition(
  nodes: Node<RpaNodeData>[] = initialNodes,
  edges: Edge[] = initialEdges,
  inputVariables: RuntimeVariable[] = [],
  name = '未命名流程'
): string {
  return JSON.stringify(buildFlowDefinition(nodes, edges, inputVariables, name), null, 2);
}

/** 从流程定义对象还原画布节点和边；解析失败（节点为空或格式错误）时返回 null。 */
export function restoreFlowCanvas(definition: Record<string, unknown>): { nodes: Node<RpaNodeData>[]; edges: Edge[] } | null {
  const rawNodes = definition.nodes;
  const rawEdges = definition.edges;
  if (!Array.isArray(rawNodes) || !Array.isArray(rawEdges)) {
    return null;
  }

  const nodes = rawNodes
    .map((rawNode, index) => restoreNode(rawNode, index))
    .filter((node): node is Node<RpaNodeData> => node !== null);
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = rawEdges
    .map((rawEdge, index) => restoreEdge(rawEdge, index, nodeIds))
    .filter((edge): edge is Edge => edge !== null);

  return nodes.length > 0 ? { edges, nodes } : null;
}

/** 从流程定义对象读取输入变量列表，格式校验失败的条目会被过滤掉。 */
export function readFlowInputVariables(definition: Record<string, unknown>): RuntimeVariable[] {
  const rawValue = definition.inputVariables;
  if (!Array.isArray(rawValue)) {
    return [];
  }
  return rawValue
    .map((item) => restoreRuntimeVariable(item))
    .filter((item): item is RuntimeVariable => item !== null);
}

function inferActionFromNode(node: Node<RpaNodeData>): { type: string } {
  if (node.id === 'start' || node.id === 'end') {
    return { type: node.id };
  }
  return { type: DEFAULT_ACTION_TYPE_BY_KIND[node.data.kind] };
}

function restoreNode(rawNode: unknown, index: number): Node<RpaNodeData> | null {
  if (rawNode === null || typeof rawNode !== 'object') {
    return null;
  }
  const node = rawNode as Record<string, unknown>;
  const id = typeof node.id === 'string' && node.id.trim() ? node.id : `node-${index + 1}`;
  const type = typeof node.type === 'string' && node.type.trim() ? node.type : 'rpa.step';
  const kind = readKind(node.kind, type);
  const title = typeof node.title === 'string' && node.title.trim() ? node.title : getDefaultTitle(type, id);
  const description = typeof node.description === 'string' ? node.description : '';
  const position = readPosition(node.position, index);
  const status = readStatus(node.status);

  return {
    id,
    type: id === 'start' || id === 'end' || type === 'start' || type === 'end' ? 'startEnd' : 'rpaStep',
    position,
    data: {
      title,
      description,
      kind,
      status,
      action: restoreAction(node, type),
      breakpoint: typeof node.breakpoint === 'boolean' ? node.breakpoint : undefined,
      disabled: typeof node.disabled === 'boolean' ? node.disabled : undefined
    }
  };
}

function restoreEdge(rawEdge: unknown, index: number, nodeIds: Set<string>): Edge | null {
  if (rawEdge === null || typeof rawEdge !== 'object') {
    return null;
  }
  const edge = rawEdge as Record<string, unknown>;
  const source = typeof edge.source === 'string' ? edge.source : '';
  const target = typeof edge.target === 'string' ? edge.target : '';
  if (!nodeIds.has(source) || !nodeIds.has(target)) {
    return null;
  }
  return {
    id: typeof edge.id === 'string' && edge.id.trim() ? edge.id : `e-${source}-${target}-${index}`,
    source,
    target,
    sourceHandle: typeof edge.sourceHandle === 'string' ? edge.sourceHandle : undefined,
    targetHandle: typeof edge.targetHandle === 'string' ? edge.targetHandle : undefined,
    type: 'smoothstep',
    label: typeof edge.label === 'string' ? edge.label : undefined,
    markerEnd: { type: MarkerType.ArrowClosed, color: '#94a3b8' },
    style: { stroke: '#94a3b8', strokeWidth: 1.5 }
  };
}

function restoreAction(node: Record<string, unknown>, type: string): RpaNodeAction {
  return {
    type,
    targetUrl: readOptionalString(node.targetUrl),
    url: readOptionalString(node.url),
    method: readHttpMethod(node.method),
    headers: readHeaders(node.headers),
    requestBody: readOptionalString(node.requestBody),
    message: readOptionalString(node.message),
    channel: readOptionalString(node.channel),
    defaultValue: readOptionalString(node.defaultValue),
    logLevel: readLogLevel(node.logLevel),
    scope: readVariableScope(node.scope),
    responseVariable: readOptionalString(node.responseVariable),
    statusVariable: readOptionalString(node.statusVariable),
    jsonVariable: readOptionalString(node.jsonVariable),
    resultVariable: readOptionalString(node.resultVariable),
    path: readOptionalString(node.path),
    scriptPath: readOptionalString(node.scriptPath),
    code: readOptionalString(node.code),
    filePath: readOptionalString(node.filePath),
    targetPath: readOptionalString(node.targetPath),
    column: readOptionalString(node.column),
    content: readOptionalString(node.content),
    rows: readRows(node.rows),
    selector: readOptionalString(node.selector),
    fetcher: node.fetcher === 'dynamic' || node.fetcher === 'stealthy' ? node.fetcher : node.fetcher === 'static' ? 'static' : undefined,
    extractMode: readExtractMode(node.extractMode),
    attribute: readOptionalString(node.attribute),
    adaptive: typeof node.adaptive === 'boolean' ? node.adaptive : undefined,
    autoSave: typeof node.autoSave === 'boolean' ? node.autoSave : undefined,
    continueOnError: typeof node.continueOnError === 'boolean' ? node.continueOnError : undefined,
    fillMode: node.fillMode === 'js' ? 'js' : node.fillMode === 'type' || node.fillMode === 'keyboard' ? 'type' : undefined,
    timeoutMs: typeof node.timeoutMs === 'number' && Number.isFinite(node.timeoutMs) ? node.timeoutMs : undefined,
    inputValue: readOptionalString(node.inputValue),
    inputVariable: readOptionalString(node.inputVariable),
    operation: readOptionalString(node.operation),
    pattern: readOptionalString(node.pattern),
    search: readOptionalString(node.search),
    replacement: readOptionalString(node.replacement),
    delimiter: readOptionalString(node.delimiter),
    left: readOptionalString(node.left),
    right: readOptionalString(node.right),
    leftVariable: readOptionalString(node.leftVariable),
    rightVariable: readOptionalString(node.rightVariable),
    operator: readOptionalString(node.operator),
    variableName: readOptionalString(node.variableName),
    value: readOptionalString(node.value),
    outputVariable: readOptionalString(node.outputVariable),
    appendVariable: readOptionalString(node.appendVariable),
    appendOutputVariable: readOptionalString(node.appendOutputVariable),
    appendMode: node.appendMode === 'record' || node.appendMode === 'values' ? node.appendMode : undefined,
    countVariable: readOptionalString(node.countVariable),
    loadedCountVariable: readOptionalString(node.loadedCountVariable),
    pageCountVariable: readOptionalString(node.pageCountVariable),
    urlTemplate: readOptionalString(node.urlTemplate),
    startPage: readOptionalNumber(node.startPage),
    pageStep: readOptionalNumber(node.pageStep),
    dismissedCountVariable: readOptionalString(node.dismissedCountVariable),
    firstValueVariable: readOptionalString(node.firstValueVariable),
    stderrVariable: readOptionalString(node.stderrVariable),
    delayMs: readOptionalNumber(node.delayMs),
    distance: readOptionalNumber(node.distance),
    index: readOptionalNumber(node.index),
    targetSelector: readOptionalString(node.targetSelector),
    checked: typeof node.checked === 'boolean' ? node.checked : undefined,
    itemsVariable: readOptionalString(node.itemsVariable),
    itemVariable: readOptionalString(node.itemVariable),
    indexVariable: readOptionalString(node.indexVariable),
    maxIterations: readOptionalNumber(node.maxIterations),
    retryCount: readOptionalNumber(node.retryCount),
    errorVariable: readOptionalString(node.errorVariable),
    flowId: readOptionalString(node.flowId),
    command: readOptionalString(node.command),
    humanTakeoverMessage: readOptionalString(node.humanTakeoverMessage),
    humanTakeoverResumeMode: node.humanTakeoverResumeMode === 'current_node' ? 'current_node' : node.humanTakeoverResumeMode === 'next_node' ? 'next_node' : undefined,
    fallbackSelectors: readOptionalString(node.fallbackSelectors),
    anchorText: readOptionalString(node.anchorText),
    outputSchema: readOutputSchema(node.outputSchema)
  };
}

function readKind(value: unknown, type: string): NodeKind {
  if (typeof value === 'string' && value in kindStyles) {
    return value as NodeKind;
  }
  const prefix = type.split('.')[0];
  return prefix in kindStyles ? (prefix as NodeKind) : 'control';
}

function readPosition(value: unknown, index: number): { x: number; y: number } {
  if (value !== null && typeof value === 'object') {
    const position = value as Record<string, unknown>;
    if (typeof position.x === 'number' && typeof position.y === 'number') {
      return { x: position.x, y: position.y };
    }
  }
  return { x: 500, y: 80 + index * 96 };
}

function readExtractMode(value: unknown): RpaNodeAction['extractMode'] {
  if (value === 'text' || value === 'html' || value === 'attribute' || value === 'count' || value === 'table') {
    return value;
  }
  return undefined;
}

function readStatus(value: unknown): NodeStatus {
  if (value === 'done' || value === 'running' || value === 'pending' || value === 'error' || value === 'skipped') {
    return value;
  }
  return 'pending';
}

function readOptionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

/** AI 生成的流程可能把 outputSchema 写成 JSON 数组，编辑器内统一保存为 JSON 字符串。 */
function readOutputSchema(value: unknown): string | undefined {
  if (Array.isArray(value)) {
    return value.length > 0 ? JSON.stringify(value) : undefined;
  }
  return readOptionalString(value);
}

function readOptionalNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function readHttpMethod(value: unknown): RpaNodeAction['method'] {
  if (value === 'GET' || value === 'POST' || value === 'PUT' || value === 'PATCH' || value === 'DELETE') {
    return value;
  }
  if (typeof value === 'string') {
    const normalized = value.toUpperCase();
    if (normalized === 'GET' || normalized === 'POST' || normalized === 'PUT' || normalized === 'PATCH' || normalized === 'DELETE') {
      return normalized;
    }
  }
  return undefined;
}

function readHeaders(value: unknown): RpaNodeAction['headers'] {
  if (typeof value === 'string') {
    return readOptionalString(value);
  }
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    const headers = Object.entries(value).reduce<Record<string, string>>((result, [key, raw]) => {
      if (typeof raw === 'string') {
        result[key] = raw;
      }
      return result;
    }, {});
    return Object.keys(headers).length > 0 ? headers : undefined;
  }
  return undefined;
}

function readRows(value: unknown): unknown[] | undefined {
  return Array.isArray(value) ? value : undefined;
}

function getDefaultTitle(type: string, id: string): string {
  if (id === 'start' || type === 'start') return '开始';
  if (id === 'end' || type === 'end') return '结束';
  if (type === 'browser.fetch') return '打开网页';
  if (type === 'browser.open') return '打开网页';
  if (type === 'browser.ensureLogin') return '确保已登录';
  if (type === 'browser.click' || type === 'ui.click') return '点击元素';
  if (type === 'browser.fill' || type === 'ui.fill') return '输入文本';
  if (type === 'browser.press') return '按下按键';
  if (type === 'browser.wait' || type === 'ui.wait') return '等待元素';
  if (type === 'browser.waitFor') return '等待条件';
  if (type === 'browser.extract' || type === 'ui.extract') return '获取文本';
  if (type === 'browser.dismiss') return '关闭弹窗';
  if (type === 'browser.clickLoadMore') return '点击加载更多';
  if (type === 'browser.paginateNext') return '下一页分页抓取';
  if (type === 'browser.screenshot' || type === 'ui.screenshot') return '页面截图';
  if (type === 'browser.scroll') return '滚动页面';
  if (type === 'browser.hover') return '悬停元素';
  if (type === 'browser.tab.switch') return '切换标签页';
  if (type === 'browser.tab.close') return '关闭标签页';
  if (type === 'ui.select') return '下拉选择';
  if (type === 'ui.check') return '复选框';
  if (type === 'ui.drag') return '拖拽操作';
  if (type === 'excel.read') return '读取 CSV';
  if (type === 'excel.write') return '写入 CSV';
  if (type === 'file.read') return '读取文件';
  if (type === 'file.write') return '写入文件';
  if (type === 'file.copy') return '复制文件';
  if (type === 'file.move') return '移动文件';
  if (type === 'file.delete') return '删除文件';
  if (type === 'file.list') return '遍历文件夹';
  if (type === 'script.python') return '执行 Python';
  if (type === 'script.javascript') return '执行 JavaScript';
  if (type === 'data.json.parse') return 'JSON 解析';
  if (type === 'data.string.transform') return '字符串处理';
  if (type === 'data.regex.match') return '正则匹配';
  if (type === 'data.list.map') return '列表处理';
  if (type === 'data.math.compute') return '数字运算';
  if (type === 'control.foreach') return '遍历列表';
  if (type === 'control.repeat_until') return '重复直到';
  if (type === 'control.condition') return '条件判断';
  if (type === 'control.delay') return '等待延时';
  if (type === 'control.break') return '中断循环';
  if (type === 'control.noop') return '流程控制';
  if (type === 'variable.set') return '赋值变量';
  if (type === 'variable.get') return '获取变量';
  if (type === 'variable.input') return '输入弹窗';
  if (type === 'variable.log') return '输出日志';
  if (type === 'variable.notify') return '消息通知';
  if (type === 'variable.clipboard') return '剪贴板';
  if (type === 'control.retry') return '重试机制';
  if (type === 'control.try') return '异常处理';
  if (type === 'control.subprocess') return '子流程';
  if (type === 'control.human_takeover') return '人工接管';
  if (type === 'script.shell') return '执行 Shell';
  if (type === 'script.websocket') return 'WebSocket';
  if (type === 'excel.save') return '保存工作簿';
  if (type === 'excel.addrow') return '新增数据行';
  if (type === 'excel.deleterow') return '删除数据行';
  if (type === 'excel.filter') return '筛选排序';
  if (type === 'browser.select') return '下拉选择';
  if (type === 'browser.check') return '复选框操作';
  if (type === 'file.compress') return '压缩解压';
  if (type === 'file.rename') return '重命名文件';
  if (type === 'file.watch') return '监听文件夹';
  if (type === 'data.convert') return '类型转换';
  if (type === 'data.encrypt') return '加密解密';
  return '自动化步骤';
}

function readLogLevel(value: unknown): RunLogLevel | undefined {
  if (value === 'info' || value === 'success' || value === 'running' || value === 'warn' || value === 'error') {
    return value;
  }
  return undefined;
}

function readVariableScope(value: unknown): VariableScope | undefined {
  if (value === '全局' || value === '循环' || value === '局部') {
    return value;
  }
  return undefined;
}

function restoreRuntimeVariable(rawValue: unknown): RuntimeVariable | null {
  if (rawValue === null || typeof rawValue !== 'object') {
    return null;
  }
  const variable = rawValue as Record<string, unknown>;
  const category = readVariableCategory(variable.category) ?? 'flow';
  const name = readOptionalString(variable.name);
  const sensitive = typeof variable.sensitive === 'boolean' ? variable.sensitive : false;
  const type = readRuntimeVariableType(variable.type);
  const value = typeof variable.value === 'string' ? variable.value : undefined;
  const scope = readVariableScope(variable.scope);
  if (name === undefined || type === undefined || value === undefined || scope === undefined) {
    return null;
  }
  return { category, name, sensitive, type, value, scope };
}

function readRuntimeVariableType(value: unknown): RuntimeVariable['type'] | undefined {
  if (value === 'String' || value === 'Integer' || value === 'Boolean' || value === 'List' || value === 'Dict') {
    return value;
  }
  return undefined;
}

function readVariableCategory(value: unknown): VariableCategory | undefined {
  if (value === 'flow' || value === 'environment' || value === 'credential') {
    return value;
  }
  return undefined;
}
