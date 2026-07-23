import type { Node } from '@xyflow/react';

import type { NodeIoField } from './nodeIoSummary';
import type { RpaNodeData } from '../types/rpa';

export type NodeExecutionSummary = {
  title: string;
  rows: NodeIoField[];
};

/** Returns null when the node has no action or no displayable fields. */
export function buildNodeExecutionSummary(node: Node<RpaNodeData>): NodeExecutionSummary | null {
  const action = node.data.action;
  if (action === undefined) {
    return null;
  }

  const rows: NodeIoField[] = [];
  const addRow = (name: string, type: string, description: string | undefined): void => {
    if (typeof description !== 'string' || description.trim() === '') {
      return;
    }
    rows.push({ name, type, description: description.trim() });
  };

  const actionType = action.type;

  if (actionType === 'browser.open' || actionType === 'browser.tab.open') {
    addRow('targetUrl', '目标网址', action.targetUrl ?? action.url);
  }

  if (actionType === 'browser.ensureLogin') {
    addRow('targetUrl', '目标网址', action.targetUrl);
    addRow('selector', '已登录特征', action.selector);
    addRow('targetSelector', '未登录特征', action.targetSelector);
  }

  if (actionType === 'browser.fetch') {
    addRow('targetUrl', '目标网址', action.targetUrl ?? action.url);
    addRow('selector', '选择器', action.selector);
    addRow('fetcher', '抓取模式', action.fetcher);
    addRow('extractMode', '提取方式', action.extractMode);
    addRow('attribute', '属性名', action.extractMode === 'attribute' ? action.attribute : undefined);
  }

  if (actionType === 'browser.extract' || actionType === 'ui.extract') {
    addRow('selector', '选择器', action.selector);
    addRow('extractMode', '提取方式', action.extractMode);
    addRow('attribute', '属性名', action.extractMode === 'attribute' ? action.attribute : undefined);
  }

  if (actionType === 'browser.extract' || actionType === 'ui.extract' || actionType === 'browser.clickLoadMore' || actionType === 'browser.paginateNext') {
    addRow('outputSchema', '输出字段', action.outputSchema);
  }

  if (actionType === 'browser.fill' || actionType === 'ui.fill') {
    addRow('selector', '目标元素', action.selector);
    addRow('inputValue', '输入内容', action.inputValue);
  }

  if (actionType === 'browser.click' || actionType === 'ui.click' || actionType === 'browser.wait' || actionType === 'ui.wait') {
    addRow('selector', '目标元素', action.selector);
  }

  if (actionType === 'http.request') {
    addRow('method', '请求方法', action.method);
    addRow('url', '请求地址', action.url ?? action.targetUrl);
    addRow('requestBody', '请求体', action.requestBody);
  }

  if (actionType === 'file.copy' || actionType === 'file.move') {
    addRow('path', '源路径', action.path);
    addRow('targetPath', '目标路径', action.targetPath);
  }

  if (actionType === 'file.read' || actionType === 'file.write' || actionType === 'file.delete' || actionType === 'file.list') {
    addRow('path', actionType === 'file.list' ? '目录路径' : '文件路径', action.path);
    addRow('pattern', '匹配规则', action.pattern);
    addRow('content', '写入内容', action.content);
  }

  if (actionType === 'excel.read' || actionType === 'excel.write') {
    addRow('path', 'CSV 路径', action.path);
    addRow('column', '读取列名', action.column);
  }

  if (actionType === 'script.python' || actionType === 'script.javascript') {
    if (action.path ?? action.scriptPath) {
      addRow('path', '脚本路径', action.path ?? action.scriptPath);
    } else if (action.code) {
      addRow('code', '内联代码', (action.code as string).split('\n').slice(0, 2).join(' | ') + '…');
    }
  }

  if (actionType === 'control.condition') {
    addRow('inputValue', '条件表达式', action.inputValue);
  }

  if (actionType === 'control.foreach') {
    addRow('itemsVariable', '遍历变量', action.itemsVariable ?? action.responseVariable);
    addRow('itemVariable', '当前项变量', action.itemVariable);
    addRow('indexVariable', '索引变量', action.indexVariable);
    addRow('maxIterations', '最大迭代次数', typeof action.maxIterations === 'number' ? String(action.maxIterations) : undefined);
  }

  if (actionType === 'control.repeat_until') {
    addRow('condition', '退出条件', action.condition ?? action.inputValue);
    addRow('indexVariable', '轮数变量', action.indexVariable);
    addRow('maxIterations', '最大轮数', typeof action.maxIterations === 'number' ? String(action.maxIterations) : undefined);
  }

  if (actionType === 'control.delay') {
    addRow('delayMs', '延时毫秒', typeof action.delayMs === 'number' ? `${action.delayMs} ms` : undefined);
    addRow('responseVariable', '输出变量', action.responseVariable ?? action.outputVariable);
  }

  if (actionType === 'control.break' || actionType === 'control.noop') {
    addRow('description', actionType === 'control.break' ? '中断说明' : '控制说明', node.data.description);
  }

  if (actionType === 'control.human_takeover') {
    addRow('humanTakeoverMessage', '提示信息', action.humanTakeoverMessage);
    addRow('humanTakeoverResumeMode', '恢复方式', action.humanTakeoverResumeMode === 'current_node' ? '重试当前节点' : '继续下一节点');
  }

  if (actionType === 'variable.set' || actionType === 'variable.assign' || actionType === 'variable.step') {
    addRow('variableName', '变量名', action.variableName);
    addRow('value', '变量值', action.value ?? action.defaultValue);
    addRow('scope', '变量作用域', action.scope);
    addRow('outputVariable', '输出变量', action.outputVariable ?? action.responseVariable);
  }

  if (actionType === 'variable.get') {
    addRow('variableName', '读取变量', action.variableName);
    addRow('responseVariable', '输出变量', action.responseVariable ?? action.outputVariable);
  }

  if (actionType === 'variable.input') {
    addRow('message', '弹窗提示', action.message);
    addRow('defaultValue', '默认值', action.defaultValue);
    addRow('variableName', '保存变量', action.variableName);
    addRow('scope', '变量作用域', action.scope);
  }

  if (actionType === 'variable.log') {
    addRow('message', '日志内容', action.message);
    addRow('logLevel', '日志级别', action.logLevel);
  }

  if (actionType === 'variable.notify') {
    addRow('channel', '通知通道', action.channel);
    addRow('message', '通知内容', action.message);
    addRow('responseVariable', '输出变量', action.responseVariable ?? action.outputVariable);
  }

  if (actionType === 'variable.clipboard') {
    addRow('content', '剪贴板内容', action.content);
    addRow('responseVariable', '输出变量', action.responseVariable ?? action.outputVariable);
  }

  if (actionType === 'data.json.parse' || actionType === 'data.list.map') {
    addRow('inputVariable', '输入变量', action.inputVariable);
  }

  if (actionType === 'data.string.transform' || actionType === 'data.regex.match') {
    addRow('inputValue', '输入值', action.inputValue);
  }

  if (actionType === 'data.string.transform' || actionType === 'data.list.map') {
    addRow('operation', '处理方式', action.operation);
    addRow('delimiter', '分隔符', action.delimiter);
  }

  if (actionType === 'data.regex.match') {
    addRow('pattern', '正则表达式', action.pattern);
  }

  if (actionType === 'data.math.compute') {
    addRow('left', '左操作数', action.left);
    addRow('operator', '运算方式', action.operator ?? action.operation);
    addRow('right', '右操作数', action.right);
  }

  if (actionType.startsWith('data.')) {
    addRow('responseVariable', '输出变量', action.responseVariable ?? action.outputVariable);
    addRow('statusVariable', '计数变量', action.statusVariable ?? action.countVariable);
    addRow('firstValueVariable', '首值变量', action.firstValueVariable);
  }

  if (actionType === 'data.convert') {
    addRow('inputValue', '输入值', action.inputValue);
    addRow('operation', '转换方式', action.operation);
  }

  if (actionType === 'data.encrypt') {
    addRow('inputValue', '输入内容', action.inputValue);
    addRow('operation', '加密方式', action.operation);
  }

  if (actionType === 'control.retry') {
    addRow('retryCount', '重试次数', typeof action.retryCount === 'number' ? `${action.retryCount} 次` : typeof action.maxIterations === 'number' ? `${action.maxIterations} 次` : undefined);
    addRow('delayMs', '重试间隔', typeof action.delayMs === 'number' ? `${action.delayMs} ms` : undefined);
  }

  if (actionType === 'control.try') {
    addRow('errorVariable', '异常变量', action.errorVariable);
  }

  if (actionType === 'control.subprocess') {
    addRow('flowId', '子流程 ID', action.flowId);
  }

  if (actionType === 'script.shell') {
    addRow('command', 'Shell 命令', action.command ?? action.path);
  }

  if (actionType === 'script.websocket') {
    addRow('url', 'WebSocket 地址', action.url);
    addRow('message', '发送消息', action.message);
  }

  if (actionType === 'excel.addrow') {
    addRow('path', '文件路径', action.path);
  }

  if (actionType === 'excel.deleterow') {
    addRow('path', '文件路径', action.path);
    addRow('index', '行索引', typeof action.index === 'number' ? String(action.index) : undefined);
  }

  if (actionType === 'excel.save') {
    addRow('path', '文件路径', action.path);
  }

  if (actionType === 'excel.filter') {
    addRow('path', '文件路径', action.path);
    addRow('column', '筛选列', action.column);
    addRow('operation', '筛选方式', action.operation);
    addRow('pattern', '筛选条件', action.pattern);
  }

  if (actionType === 'file.compress') {
    addRow('path', '源路径', action.path);
    addRow('targetPath', '输出路径', action.targetPath);
    addRow('operation', '操作方式', action.operation);
  }

  if (actionType === 'file.rename') {
    addRow('path', '源路径', action.path);
    addRow('targetPath', '新路径', action.targetPath);
  }

  if (actionType === 'file.watch') {
    addRow('path', '监听目录', action.path);
    addRow('pattern', '匹配规则', action.pattern);
  }

  addRow('timeoutMs', '超时', typeof action.timeoutMs === 'number' ? `${action.timeoutMs} ms` : undefined);

  if (rows.length === 0) {
    return null;
  }

  return {
    title: '最终执行摘要',
    rows
  };
}
