import { MarkerType, type Edge, type Node, type XYPosition } from '@xyflow/react';

import type { NodeKind, RpaNodeData } from '../types/rpa';

export type ComponentDragPayload = {
  nodeType: NodeKind;
  label: string;
};

const nodeSize = {
  height: 84,
  width: 240
};

const defaultEdgeStyle = { stroke: '#94a3b8', strokeWidth: 1.5 };
const defaultMarker = { type: MarkerType.ArrowClosed, color: '#94a3b8' };

export function parseComponentDragPayload(rawPayload: string): ComponentDragPayload | null {
  try {
    const payload = JSON.parse(rawPayload) as Partial<ComponentDragPayload>;
    if (isNodeKind(payload.nodeType) && typeof payload.label === 'string' && payload.label.trim()) {
      return { label: payload.label.trim(), nodeType: payload.nodeType };
    }
  } catch {
    return null;
  }
  return null;
}

/** Snaps the drop position to a 10 px grid. */
export function createFlowNode(payload: ComponentDragPayload, position: XYPosition, _index?: number): Node<RpaNodeData> {
  return {
    id: `n_${crypto.randomUUID()}`,
    type: 'rpaStep',
    position: {
      x: Math.round(position.x / 10) * 10,
      y: Math.round(position.y / 10) * 10
    },
    data: createNodeData(payload)
  };
}

export function createNodeData(payload: ComponentDragPayload): RpaNodeData {
  return {
    title: payload.label,
    description: getDefaultDescription(payload),
    kind: payload.nodeType,
    status: 'pending',
    action: getDefaultAction(payload)
  };
}

export function createFlowEdge(source: string, target: string, label?: string): Edge {
  return {
    id: `e_${crypto.randomUUID()}`,
    source,
    target,
    type: 'smoothstep',
    label,
    markerEnd: defaultMarker,
    style: defaultEdgeStyle
  };
}

/** Rejects self-loops, edges into 'start', edges from 'end', and duplicates. */
export function canConnectEdge(edges: Edge[], source?: string | null, target?: string | null): source is string {
  if (typeof source !== 'string' || typeof target !== 'string') {
    return false;
  }
  if (source === target || source === 'end' || target === 'start') {
    return false;
  }
  return !edges.some((edge) => edge.source === source && edge.target === target);
}

/** Splices a new node between `sourceNodeId` and its successor, re-routing the outgoing edge through it. */
export function insertNodeAfter(nodes: Node<RpaNodeData>[], edges: Edge[], sourceNodeId: string, payload: ComponentDragPayload): { edges: Edge[]; node: Node<RpaNodeData> } | null {
  const sourceNode = nodes.find((node) => node.id === sourceNodeId);
  if (sourceNode === undefined) {
    return null;
  }
  const outgoingEdge = edges.find((edge) => edge.source === sourceNodeId);
  const node = createFlowNode(
    payload,
    {
      x: sourceNode.position.x,
      y: sourceNode.position.y + nodeSize.height + 30
    },
    nodes.length + 1
  );
  const rewiredEdges = edges.filter((edge) => edge.id !== outgoingEdge?.id);
  const nextEdges = [...rewiredEdges, createFlowEdge(sourceNodeId, node.id)];
  if (outgoingEdge !== undefined) {
    nextEdges.push(createFlowEdge(node.id, outgoingEdge.target, typeof outgoingEdge.label === 'string' ? outgoingEdge.label : undefined));
  }
  return { edges: nextEdges, node };
}

/** Mirror of `insertNodeAfter`: splices the new node between `targetNodeId`'s predecessor and itself. */
export function insertNodeBefore(nodes: Node<RpaNodeData>[], edges: Edge[], targetNodeId: string, payload: ComponentDragPayload): { edges: Edge[]; node: Node<RpaNodeData> } | null {
  const targetNode = nodes.find((node) => node.id === targetNodeId);
  if (targetNode === undefined) {
    return null;
  }
  const incomingEdge = edges.find((edge) => edge.target === targetNodeId);
  const node = createFlowNode(
    payload,
    {
      x: targetNode.position.x,
      y: targetNode.position.y - nodeSize.height - 30
    },
    nodes.length + 1
  );
  const rewiredEdges = edges.filter((edge) => edge.id !== incomingEdge?.id);
  const nextEdges = [...rewiredEdges, createFlowEdge(node.id, targetNodeId)];
  if (incomingEdge !== undefined) {
    nextEdges.push(createFlowEdge(incomingEdge.source, node.id, typeof incomingEdge.label === 'string' ? incomingEdge.label : undefined));
  }
  return { edges: nextEdges, node };
}

/** Removes a node and rewires its predecessor to its successor; 'start'/'end' are protected. */
export function deleteNodeAndReconnect(nodes: Node<RpaNodeData>[], edges: Edge[], nodeId: string): Edge[] {
  if (nodeId === 'start' || nodeId === 'end') {
    return edges;
  }
  const nodeExists = nodes.some((node) => node.id === nodeId);
  if (!nodeExists) {
    return edges;
  }
  const incomingEdge = edges.find((edge) => edge.target === nodeId);
  const outgoingEdge = edges.find((edge) => edge.source === nodeId);
  const retainedEdges = edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId);
  if (incomingEdge === undefined || outgoingEdge === undefined || incomingEdge.source === outgoingEdge.target) {
    return retainedEdges;
  }
  return [...retainedEdges, createFlowEdge(incomingEdge.source, outgoingEdge.target, typeof incomingEdge.label === 'string' ? incomingEdge.label : undefined)];
}

// getDefaultDescription/getDefaultAction 按 label 文案匹配组件面板项，新增或改名组件时需同步这两处，否则拖入的节点会缺省为通用占位配置。
function getDefaultDescription(payload: ComponentDragPayload): string {
  if (payload.nodeType === 'browser') return payload.label === '打开网页' ? 'https://example.com/' : 'CSS 选择器 · 等待 30s';
  if (payload.nodeType === 'excel') return payload.label === '导出 CSV' ? '${var.output_prefix}.csv' : 'data/orders.csv · Sheet1';
  if (payload.nodeType === 'control') {
    if (payload.label === '条件判断') return 'condition == true';
    if (isLoopComponent(payload.label)) return 'excel_rows → current_row';
    if (payload.label === '重复直到') return '直到条件成立 · 上限 50 轮';
    if (payload.label === '等待延时') return '等待 1000ms';
    if (payload.label === '中断循环') return '跳出当前循环';
    if (payload.label === '重试机制') return '最多 3 次 · 间隔 2s';
    if (payload.label === '异常处理') return 'try → except → caught_error';
    if (payload.label === '子流程') return '调用子流程';
    return '流程控制';
  }
  if (payload.nodeType === 'script') {
    if (payload.label === 'HTTP 请求' || payload.label === '调用 API') return 'GET https://api.example.com/data';
    if (payload.label === '执行 JavaScript') return 'scripts/transform.js';
    if (payload.label === '执行 Shell') return 'echo ${var.input}';
    if (payload.label === 'WebSocket') return 'ws://localhost:8080';
    return 'scripts/data_clean.py';
  }
  if (payload.nodeType === 'variable') return getVariableDescription(payload.label);
  if (payload.nodeType === 'file') {
    if (payload.label === '写入文件') return '${var.output_prefix}.txt';
    if (payload.label === '复制/移动') return 'data/input.txt → archive/input.txt';
    if (payload.label === '删除文件') return 'temp/remove.txt';
    if (payload.label === '遍历文件夹') return 'data/*.txt → file_paths';
    if (payload.label === '压缩解压') return 'data/input/ → archives/output.zip';
    if (payload.label === '重命名') return 'data/old.txt → data/new.txt';
    if (payload.label === '监听变化') return 'data/*.csv · 等待变更';
    return 'data/input.txt';
  }
  if (payload.nodeType === 'data') return getDataDescription(payload.label);
  if (payload.nodeType === 'ui') return getUiDescription(payload.label);
  return '界面控件';
}

function getDefaultAction(payload: ComponentDragPayload): RpaNodeData['action'] {
  if (payload.nodeType === 'browser' && payload.label === '打开网页') {
    return {
      type: 'browser.open',
      targetUrl: 'https://example.com/',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'browser' && payload.label === '确保已登录') {
    return {
      type: 'browser.ensureLogin',
      targetUrl: 'https://example.com/',
      firstValueVariable: 'login_status',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'browser' && payload.label === '输入文本') {
    return {
      type: 'browser.fill',
      selector: '#username',
      inputValue: '${var.username}',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'browser' && payload.label === '点击元素') {
    return {
      type: 'browser.click',
      selector: '#submit',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'browser' && payload.label === '等待元素') {
    return {
      type: 'browser.wait',
      selector: '#content',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'browser' && payload.label === '页面截图') {
    return {
      type: 'browser.screenshot',
      outputVariable: 'screenshot_url',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'browser' && payload.label === '滚动页面') {
    return {
      type: 'browser.scroll',
      distance: 800,
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'browser' && payload.label === '悬停元素') {
    return {
      type: 'browser.hover',
      selector: '',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'browser' && payload.label === '切换标签页') {
    return {
      type: 'browser.tab.switch',
      index: 0,
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'browser' && payload.label === '关闭标签页') {
    return {
      type: 'browser.tab.close',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'browser' && payload.label === '获取文本') {
    return {
      type: 'browser.extract',
      selector: '.result',
      outputVariable: 'browser_texts',
      firstValueVariable: 'browser_text',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'ui') {
    return getDefaultUiAction(payload.label);
  }
  if (payload.nodeType === 'script' && (payload.label === 'HTTP 请求' || payload.label === '调用 API')) {
    return {
      type: 'http.request',
      url: 'https://api.example.com/data',
      method: 'GET',
      headers: { accept: 'application/json' },
      responseVariable: 'http_response',
      statusVariable: 'http_status',
      jsonVariable: 'http_json',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'script' && payload.label === '执行 JavaScript') {
    return {
      type: 'script.javascript',
      path: 'scripts/transform.js',
      outputVariable: 'script_stdout',
      statusVariable: 'script_exit_code',
      stderrVariable: 'script_stderr',
      timeoutMs: 60_000
    };
  }
  if (payload.nodeType === 'script' && payload.label === '执行 Python') {
    return {
      type: 'script.python',
      path: 'scripts/data_clean.py',
      outputVariable: 'script_stdout',
      statusVariable: 'script_exit_code',
      stderrVariable: 'script_stderr',
      timeoutMs: 60_000
    };
  }
  if (payload.nodeType === 'script' && payload.label === '执行 Shell') {
    return {
      type: 'script.shell',
      command: 'echo ${var.input}',
      outputVariable: 'shell_output',
      statusVariable: 'shell_exit_code',
      stderrVariable: 'shell_stderr',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'script' && payload.label === 'WebSocket') {
    return {
      type: 'script.websocket',
      url: 'ws://localhost:8080',
      message: '${var.ws_message}',
      outputVariable: 'ws_response',
      statusVariable: 'ws_status',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'excel' && (payload.label === '打开工作簿' || payload.label === '读取单元格' || payload.label === '获取行数')) {
    return {
      type: 'excel.read',
      path: 'data/orders.csv',
      column: payload.label === '获取行数' ? undefined : 'order_id',
      outputVariable: 'excel_rows',
      firstValueVariable: 'first_order_id',
      countVariable: 'row_count',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'excel' && (payload.label === '写入单元格' || payload.label === '导出 CSV')) {
    return {
      type: 'excel.write',
      path: '${var.output_prefix}.csv',
      rows: [['order_id', 'status'], ['${var.first_order_id}', 'done']],
      outputVariable: 'excel_output_path',
      countVariable: 'excel_output_rows',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'excel' && payload.label === '新增数据行') {
    return {
      type: 'excel.addrow',
      path: 'data/orders.csv',
      rows: [['${var.order_id}', '${var.status}']],
      outputVariable: 'excel_row_count',
      countVariable: 'excel_row_count',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'excel' && payload.label === '保存文件') {
    return {
      type: 'excel.save',
      path: '${var.output_prefix}.csv',
      outputVariable: 'excel_save_path',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'excel' && payload.label === '删除数据行') {
    return {
      type: 'excel.deleterow',
      path: 'data/orders.csv',
      index: 0,
      outputVariable: 'excel_row_count',
      countVariable: 'excel_row_count',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'excel' && payload.label === '筛选/排序') {
    return {
      type: 'excel.filter',
      path: 'data/orders.csv',
      column: 'status',
      operation: 'filter',
      pattern: 'done',
      outputVariable: 'filtered_rows',
      countVariable: 'filtered_count',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'file' && payload.label === '写入文件') {
    return {
      type: 'file.write',
      path: '${var.output_prefix}.txt',
      content: '${var.first_order_id}',
      outputVariable: 'file_path',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'file' && payload.label === '复制/移动') {
    return {
      type: 'file.copy',
      path: 'data/input.txt',
      targetPath: 'archive/input.txt',
      outputVariable: 'copied_path',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'file' && payload.label === '删除文件') {
    return {
      type: 'file.delete',
      path: 'temp/remove.txt',
      outputVariable: 'deleted_path',
      countVariable: 'deleted_count',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'file' && payload.label === '遍历文件夹') {
    return {
      type: 'file.list',
      path: 'data',
      pattern: '*.txt',
      outputVariable: 'file_paths',
      countVariable: 'file_count',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'file' && payload.label === '压缩解压') {
    return {
      type: 'file.compress',
      path: 'data/input/',
      targetPath: 'archives/output.zip',
      operation: 'compress',
      outputVariable: 'archive_path',
      timeoutMs: 60_000
    };
  }
  if (payload.nodeType === 'file' && payload.label === '重命名') {
    return {
      type: 'file.rename',
      path: 'data/old_name.txt',
      targetPath: 'data/new_name.txt',
      outputVariable: 'renamed_path',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'file' && payload.label === '监听变化') {
    return {
      type: 'file.watch',
      path: 'data/',
      pattern: '*.csv',
      outputVariable: 'changed_files',
      countVariable: 'changed_count',
      timeoutMs: 300_000
    };
  }
  if (payload.nodeType === 'file') {
    return {
      type: 'file.read',
      path: 'data/input.txt',
      outputVariable: 'file_content',
      firstValueVariable: 'file_text',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'data') {
    return getDefaultDataAction(payload.label);
  }
  if (payload.nodeType === 'control' && payload.label === '条件判断') {
    return {
      type: 'control.condition',
      inputValue: 'condition == true',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'control' && payload.label === '重复直到') {
    return {
      type: 'control.repeat_until',
      inputValue: 'panel_month == target_month',
      indexVariable: 'repeat_index',
      maxIterations: 50,
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'control' && isLoopComponent(payload.label)) {
    return {
      type: 'control.foreach',
      itemsVariable: 'excel_rows',
      itemVariable: 'current_row',
      indexVariable: 'loop_index',
      maxIterations: 1000,
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'control' && payload.label === '等待延时') {
    return {
      type: 'control.delay',
      delayMs: 1000,
      outputVariable: 'delay_ms',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'control' && payload.label === '中断循环') {
    return {
      type: 'control.break',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'control' && payload.label === '重试机制') {
    return {
      type: 'control.retry',
      retryCount: 3,
      delayMs: 2000,
      outputVariable: 'retry_count',
      timeoutMs: 300_000
    };
  }
  if (payload.nodeType === 'control' && payload.label === '异常处理') {
    return {
      type: 'control.try',
      errorVariable: 'caught_error',
      outputVariable: 'try_result',
      timeoutMs: 300_000
    };
  }
  if (payload.nodeType === 'control' && payload.label === '子流程') {
    return {
      type: 'control.subprocess',
      flowId: '',
      outputVariable: 'subprocess_result',
      statusVariable: 'subprocess_status',
      timeoutMs: 300_000
    };
  }
  if (payload.nodeType === 'control' && payload.label === '人工接管') {
    return {
      type: 'control.human_takeover',
      humanTakeoverMessage: '',
      humanTakeoverResumeMode: 'next_node' as const,
      timeoutMs: 0
    };
  }
  if (payload.nodeType === 'control') {
    return {
      type: 'control.noop',
      timeoutMs: 30_000
    };
  }
  if (payload.nodeType === 'variable') {
    return getDefaultVariableAction(payload.label);
  }
  return { type: `${payload.nodeType}.step`, timeoutMs: 30_000 };
}

function isNodeKind(value: unknown): value is NodeKind {
  return value === 'browser' || value === 'excel' || value === 'ui' || value === 'file' || value === 'data' || value === 'script' || value === 'control' || value === 'variable';
}

function isLoopComponent(label: string): boolean {
  return label === '循环' || label === '遍历列表' || label === '遍历数据表' || label === '遍历文件夹';
}

function getDataDescription(label: string): string {
  if (label === 'JSON 解析' || label === '数据表操作') return 'http_response → parsed_json';
  if (label === '正则匹配') return 'pattern: (\\d+)';
  if (label === '列表处理') return 'excel_rows → unique_rows';
  if (label === '数字运算') return 'left + right';
  return '${var.input} → output';
}

function getUiDescription(label: string): string {
  if (label === '点击控件') return '#submit';
  if (label === '输入文字') return '#username ← ${var.username}';
  if (label === '获取属性') return '.result → ui_text';
  if (label === '等待控件') return '#content · 30s';
  if (label === '截图控件') return '#panel';
  if (label === '下拉选择') return 'select[name=status]';
  if (label === '复选框') return 'input[type=checkbox]';
  if (label === '拖拽操作') return '#source → #target';
  return '界面控件';
}

function getVariableDescription(label: string): string {
  if (label === '赋值变量') return 'result_status = done';
  if (label === '获取变量') return 'result_status → status_value';
  if (label === '输入弹窗') return '请输入参数 → user_input';
  if (label === '输出日志') return 'info · ${var.result_status}';
  if (label === '消息通知') return '企业微信 · 流程完成';
  if (label === '剪贴板') return '${var.result_status} → clipboard_text';
  return '变量/消息';
}

function getDefaultVariableAction(label: string): RpaNodeData['action'] {
  if (label === '赋值变量') {
    return {
      type: 'variable.set',
      variableName: 'result_status',
      value: 'done',
      scope: '全局',
      outputVariable: 'result_status',
      timeoutMs: 30_000
    };
  }
  if (label === '获取变量') {
    return {
      type: 'variable.get',
      variableName: 'result_status',
      outputVariable: 'status_value',
      timeoutMs: 30_000
    };
  }
  if (label === '输入弹窗') {
    return {
      type: 'variable.input',
      variableName: 'user_input',
      message: '请输入运行参数',
      defaultValue: '',
      scope: '全局',
      timeoutMs: 30_000
    };
  }
  if (label === '输出日志') {
    return {
      type: 'variable.log',
      message: '处理结果: ${var.result_status}',
      logLevel: 'info',
      timeoutMs: 30_000
    };
  }
  if (label === '消息通知') {
    return {
      type: 'variable.notify',
      channel: '企业微信',
      message: '流程执行完成: ${var.result_status}',
      outputVariable: 'notification_message',
      timeoutMs: 30_000
    };
  }
  if (label === '剪贴板') {
    return {
      type: 'variable.clipboard',
      content: '${var.result_status}',
      outputVariable: 'clipboard_text',
      timeoutMs: 30_000
    };
  }
  return {
    type: 'variable.set',
    variableName: 'result_status',
    value: 'done',
    scope: '全局',
    timeoutMs: 30_000
  };
}

function getDefaultUiAction(label: string): RpaNodeData['action'] {
  if (label === '点击控件') {
    return { type: 'ui.click', selector: '#submit', timeoutMs: 30_000 };
  }
  if (label === '输入文字') {
    return { type: 'ui.fill', selector: '#username', inputValue: '${var.username}', timeoutMs: 30_000 };
  }
  if (label === '获取属性' || label === '列表操作') {
    return { type: 'ui.extract', selector: '.result', outputVariable: 'ui_values', firstValueVariable: 'ui_value', timeoutMs: 30_000 };
  }
  if (label === '等待控件') {
    return { type: 'ui.wait', selector: '#content', timeoutMs: 30_000 };
  }
  if (label === '截图控件') {
    return { type: 'ui.screenshot', selector: '#panel', outputVariable: 'ui_screenshot', timeoutMs: 30_000 };
  }
  if (label === '下拉选择') {
    return { type: 'ui.select', selector: 'select[name=status]', inputValue: 'done', outputVariable: 'selected_value', timeoutMs: 30_000 };
  }
  if (label === '复选框') {
    return { type: 'ui.check', selector: 'input[type=checkbox]', checked: true, outputVariable: 'checked_value', timeoutMs: 30_000 };
  }
  if (label === '拖拽操作') {
    return { type: 'ui.drag', selector: '#source', targetSelector: '#target', outputVariable: 'drop_target', timeoutMs: 30_000 };
  }
  return { type: 'ui.wait', selector: '#content', timeoutMs: 30_000 };
}

function getDefaultDataAction(label: string): RpaNodeData['action'] {
  if (label === 'JSON 解析' || label === '数据表操作') {
    return {
      type: 'data.json.parse',
      inputVariable: 'http_response',
      outputVariable: 'parsed_json',
      countVariable: 'parsed_count',
      timeoutMs: 30_000
    };
  }
  if (label === '字符串处理') {
    return {
      type: 'data.string.transform',
      inputValue: '${var.input_text}',
      operation: 'trim',
      outputVariable: 'text_output',
      countVariable: 'text_count',
      timeoutMs: 30_000
    };
  }
  if (label === '类型转换') {
    return {
      type: 'data.convert',
      inputValue: '${var.input_text}',
      operation: 'to_int',
      outputVariable: 'converted_value',
      timeoutMs: 30_000
    };
  }
  if (label === '加密解密') {
    return {
      type: 'data.encrypt',
      inputValue: '${var.input_text}',
      operation: 'md5',
      outputVariable: 'encrypted_value',
      timeoutMs: 30_000
    };
  }
  if (label === '正则匹配') {
    return {
      type: 'data.regex.match',
      inputValue: '${var.input_text}',
      pattern: '(\\d+)',
      outputVariable: 'regex_matches',
      firstValueVariable: 'first_match',
      countVariable: 'match_count',
      timeoutMs: 30_000
    };
  }
  if (label === '列表处理') {
    return {
      type: 'data.list.map',
      inputVariable: 'excel_rows',
      operation: 'unique',
      outputVariable: 'unique_rows',
      countVariable: 'unique_count',
      timeoutMs: 30_000
    };
  }
  if (label === '数字运算') {
    return {
      type: 'data.math.compute',
      left: '${var.left}',
      right: '${var.right}',
      operator: 'add',
      outputVariable: 'math_result',
      countVariable: 'math_count',
      timeoutMs: 30_000
    };
  }
  return {
    type: 'data.string.transform',
    inputValue: '${var.input_text}',
    operation: 'trim',
    outputVariable: 'data_output',
    countVariable: 'data_count',
    timeoutMs: 30_000
  };
}
