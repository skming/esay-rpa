import type { Node } from '@xyflow/react';

import type { RpaNodeData } from '../types/rpa';

/** A single input or output variable/config field shown in the I/O summary panel. */
export type NodeIoField = {
  name: string;
  type: string;
  description: string;
};

export type NodeIoSummary = {
  inputs: NodeIoField[];
  outputs: NodeIoField[];
};

/** Matches `${var.variableName}` template expressions inside string config values. */
const VARIABLE_PATTERN = /\$\{var\.([A-Za-z_][A-Za-z0-9_]*)\}/g;

/**
 * Derives the input variable dependencies and output variable names for a node
 * by inspecting its action config. Used in the property panel I/O tab.
 */
export function buildNodeIoSummary(node: Node<RpaNodeData>): NodeIoSummary {
  const action = node.data.action;
  if (action === undefined) {
    return { inputs: [], outputs: [] };
  }

  const inputs = new Map<string, NodeIoField>();
  const outputs = new Map<string, NodeIoField>();
  const actionType = action.type;

  const addInput = (name: string, type: string, description: string): void => {
    if (!name.trim()) {
      return;
    }
    inputs.set(name, { name, type, description });
  };

  const addOutput = (name: string | undefined, type: string, description: string): void => {
    if (typeof name !== 'string' || name.trim() === '') {
      return;
    }
    outputs.set(name, { description, name, type });
  };

  const addTemplateInputs = (value: string | undefined, description: string): void => {
    if (typeof value !== 'string' || value.trim() === '') {
      return;
    }
    for (const variableName of extractTemplateVariables(value)) {
      addInput(variableName, '变量', description);
    }
  };

  if (typeof action.targetUrl === 'string' && action.targetUrl.trim()) {
    addInput('targetUrl', '配置', action.targetUrl);
    addTemplateInputs(action.targetUrl, 'URL 模板变量');
  }
  if (typeof action.url === 'string' && action.url.trim()) {
    addInput('url', '配置', action.url);
    addTemplateInputs(action.url, '请求地址模板变量');
  }
  if (typeof action.selector === 'string' && action.selector.trim()) {
    addInput('selector', '配置', action.selector);
    addTemplateInputs(action.selector, '选择器模板变量');
  }
  addTemplateInputs(action.inputValue, '输入值模板变量');
  addTemplateInputs(action.requestBody, '请求体模板变量');
  addTemplateInputs(action.message, '消息模板变量');
  addTemplateInputs(action.content, '内容模板变量');
  addTemplateInputs(action.defaultValue, '默认值模板变量');
  addTemplateInputs(action.left, '左操作数模板变量');
  addTemplateInputs(action.right, '右操作数模板变量');

  if (typeof action.inputVariable === 'string' && action.inputVariable.trim()) {
    addInput(action.inputVariable, '变量', '输入变量');
  }
  if (typeof action.itemsVariable === 'string' && action.itemsVariable.trim()) {
    addInput(action.itemsVariable, '列表', '循环遍历变量');
  }
  if (typeof action.variableName === 'string' && action.variableName.trim() && actionType === 'variable.get') {
    addInput(action.variableName, '变量', '读取变量');
  }

  if (actionType === 'browser.fetch' || actionType === 'browser.extract' || actionType === 'browser.clickLoadMore' || actionType === 'browser.paginateNext' || actionType === 'ui.extract') {
    addOutput(action.outputVariable ?? action.responseVariable, 'List', '提取结果列表');
    addOutput(action.firstValueVariable, 'String', '首个提取值');
    addOutput(action.countVariable ?? action.statusVariable, 'Integer', '命中数量');
    addOutput(action.loadedCountVariable, 'Integer', '加载后 DOM 数量');
    addOutput(action.pageCountVariable, 'Integer', '访问页数');
  }

  if (actionType === 'browser.dismiss') {
    addOutput(action.outputVariable ?? action.responseVariable, 'String', '弹窗处理结果');
    addOutput(action.dismissedCountVariable ?? action.countVariable, 'Integer', '关闭弹窗数量');
  }

  if (actionType === 'browser.fill' || actionType === 'ui.fill' || actionType === 'browser.press' || actionType === 'browser.click' || actionType === 'ui.click' || actionType === 'browser.wait' || actionType === 'ui.wait' || actionType === 'browser.screenshot' || actionType === 'ui.screenshot' || actionType === 'browser.tab.close' || actionType === 'browser.tab.switch' || actionType === 'browser.scroll' || actionType === 'browser.select' || actionType === 'ui.select' || actionType === 'browser.check' || actionType === 'ui.check' || actionType === 'browser.drag' || actionType === 'ui.drag') {
    addOutput(action.outputVariable ?? action.responseVariable, 'String', '浏览器动作输出');
  }

  if (actionType === 'http.request') {
    addOutput(action.responseVariable ?? action.outputVariable, 'String', 'HTTP 响应内容');
    addOutput(action.statusVariable, 'Integer', 'HTTP 状态码');
    addOutput(action.jsonVariable, 'Dict', 'HTTP JSON 解析结果');
  }

  if (actionType.startsWith('file.') || actionType.startsWith('excel.')) {
    addOutput(action.outputVariable ?? action.responseVariable, actionType.endsWith('.list') || actionType.endsWith('.read') ? 'List' : 'String', '文件/表格输出');
    addOutput(action.countVariable ?? action.statusVariable, 'Integer', '文件/表格计数');
    addOutput(action.firstValueVariable, 'String', '首个结果');
  }

  if (actionType.startsWith('script.')) {
    addOutput(action.outputVariable ?? action.responseVariable, 'String', '脚本标准输出');
    addOutput(action.statusVariable, 'Integer', '脚本退出码');
    addOutput(action.stderrVariable, 'String', '脚本错误输出');
  }

  if (actionType === 'control.foreach') {
    addOutput(action.itemVariable, 'Dict', '当前循环项');
    addOutput(action.indexVariable, 'Integer', '当前循环索引');
  }

  if (actionType === 'control.delay') {
    addOutput(action.outputVariable ?? action.responseVariable, 'Integer', '实际延时毫秒数');
  }

  if (actionType === 'control.retry') {
    addOutput(action.outputVariable ?? action.responseVariable, 'Integer', '实际重试次数');
  }

  if (actionType === 'control.try') {
    addOutput(action.errorVariable, 'String', '捕获到的异常信息');
    addOutput(action.outputVariable ?? action.responseVariable, 'String', '异常处理结果');
  }

  if (actionType === 'control.subprocess') {
    addOutput(action.outputVariable ?? action.responseVariable, 'Dict', '子流程输出');
    addOutput(action.statusVariable, 'Integer', '子流程退出状态');
  }

  if (actionType === 'script.websocket') {
    addOutput(action.outputVariable ?? action.responseVariable, 'String', 'WebSocket 消息');
    addOutput(action.statusVariable, 'Integer', '连接状态码');
  }

  if (actionType.startsWith('variable.')) {
    addOutput(action.outputVariable ?? action.responseVariable, 'String', '变量动作输出');
    if ((actionType === 'variable.set' || actionType === 'variable.assign' || actionType === 'variable.input') && typeof action.variableName === 'string') {
      addOutput(action.variableName, '变量', '写入变量');
    }
  }

  if (actionType.startsWith('data.')) {
    addOutput(action.outputVariable ?? action.responseVariable, 'String', '数据处理结果');
    addOutput(action.countVariable ?? action.statusVariable, 'Integer', '数据结果计数');
    addOutput(action.firstValueVariable, 'String', '首个结果');
  }

  return {
    inputs: [...inputs.values()],
    outputs: [...outputs.values()]
  };
}

function extractTemplateVariables(value: string): string[] {
  const matches = new Set<string>();
  for (const match of value.matchAll(VARIABLE_PATTERN)) {
    const variableName = match[1]?.trim();
    if (variableName !== undefined && variableName.length > 0) {
      matches.add(variableName);
    }
  }
  return [...matches];
}
