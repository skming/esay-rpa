import type { Node } from '@xyflow/react';

import { isSafeVariableName } from './variableNaming';
import type { RpaNodeAction, RpaNodeData } from '../types/rpa';

/** A single variable warning or error attached to a specific field of a node's action config. */
export type NodeVariableIssue = {
  /** If set, points to the upstream node that already defined this variable (used for override warnings). */
  definedByNodeId?: string;
  definedByNodeTitle?: string;
  fieldName: string;
  message: string;
  severity: 'error' | 'warn';
  variableName: string;
};

export type NodeVariableIoStatus = {
  note?: string;
  tone?: 'default' | 'warn' | 'error';
};

export type NodeVariableDiagnostics = {
  inputIssues: NodeVariableIssue[];
  outputIssues: NodeVariableIssue[];
};

const TEMPLATE_VARIABLE_PATTERN = /\$\{var\.([A-Za-z_][A-Za-z0-9_.-]{0,119})\}/g;
const BARE_VARIABLE_PATTERN = /\b([A-Za-z_][A-Za-z0-9_.-]{0,119})\b/g;
const CONDITION_KEYWORDS = new Set(['true', 'false', 'null', 'none', 'and', 'or', 'not', 'yes', 'no']);

/**
 * Produces input/output variable diagnostics for a single node.
 * Input issues flag undefined or malformed variable references; output issues
 * warn when a node would overwrite a variable already set by an upstream node.
 */
export function buildNodeVariableDiagnostics(
  node: Node<RpaNodeData>,
  availableVariableNames: string[],
  flowNodes: Node<RpaNodeData>[] = []
): NodeVariableDiagnostics {
  const action = node.data.action;
  if (action === undefined) {
    return { inputIssues: [], outputIssues: [] };
  }

  const upstreamDefinitions = collectUpstreamDefinitions(flowNodes, node.id);
  const available = new Set(availableVariableNames.map((name) => name.trim()).filter((name) => name.length > 0));
  for (const variableName of upstreamDefinitions.keys()) {
    available.add(variableName);
  }
  const inputIssues = collectReferencedVariables(action).flatMap<NodeVariableIssue>((entry) => {
    if (!isSafeVariableName(entry.variableName)) {
      return [
        {
          fieldName: entry.fieldName,
          message: `变量“${entry.variableName}”命名不合法`,
          severity: 'error' as const,
          variableName: entry.variableName
        }
      ];
    }
    if (!isVariableAvailable(entry.variableName, available)) {
      return [
        {
          fieldName: entry.fieldName,
          message: `变量“${entry.variableName}”当前不存在`,
          severity: 'error' as const,
          variableName: entry.variableName
        }
      ];
    }
    return [];
  });

  const outputIssues = collectProducedVariables(action).flatMap<NodeVariableIssue>((entry) => {
    if (!isSafeVariableName(entry.variableName)) {
      return [
        {
          fieldName: entry.fieldName,
          message: `输出变量“${entry.variableName}”命名不合法`,
          severity: 'error' as const,
          variableName: entry.variableName
        }
      ];
    }
    if (isVariableAvailable(entry.variableName, available)) {
      const definedBy = upstreamDefinitions.get(entry.variableName);
      return [
        {
          definedByNodeId: definedBy?.nodeId,
          definedByNodeTitle: definedBy?.nodeTitle,
          fieldName: entry.fieldName,
          message:
            definedBy === undefined
              ? `输出变量“${entry.variableName}”会覆盖已有值`
              : `输出变量“${entry.variableName}”会覆盖上游节点“${definedBy.nodeTitle}”的结果`,
          severity: 'warn' as const,
          variableName: entry.variableName
        }
      ];
    }
    return [];
  });

  return { inputIssues, outputIssues };
}

function isVariableAvailable(name: string, available: Set<string>): boolean {
  if (available.has(name)) {
    return true;
  }
  const rootName = name.split('.')[0];
  return rootName !== undefined && available.has(rootName);
}

/** Maps a variable name to its display tone and tooltip message for the I/O panel. */
export function getIoFieldStatus(issues: NodeVariableIssue[], name: string): NodeVariableIoStatus {
  const matched = issues.find((issue) => issue.variableName === name);
  if (matched === undefined) {
    return {};
  }
  return {
    note: matched.message,
    tone: matched.severity === 'error' ? 'error' : 'warn'
  };
}

function collectUpstreamDefinitions(flowNodes: Node<RpaNodeData>[], currentNodeId: string): Map<string, { nodeId: string; nodeTitle: string }> {
  const definitions = new Map<string, { nodeId: string; nodeTitle: string }>();
  for (const flowNode of flowNodes) {
    if (flowNode.id === currentNodeId) {
      break;
    }
    if (flowNode.id === 'start' || flowNode.id === 'end' || flowNode.data.action === undefined) {
      continue;
    }
    for (const entry of collectProducedVariables(flowNode.data.action)) {
      definitions.set(entry.variableName, {
        nodeId: flowNode.id,
        nodeTitle: flowNode.data.title
      });
    }
  }
  return definitions;
}

function collectReferencedVariables(action: RpaNodeAction): Array<{ fieldName: string; variableName: string }> {
  const entries: Array<{ fieldName: string; variableName: string }> = [];
  const addDirect = (fieldName: string, value: string | undefined): void => {
    if (typeof value === 'string' && value.trim() !== '') {
      entries.push({ fieldName, variableName: value.trim() });
    }
  };
  const addTemplate = (fieldName: string, value: string | undefined): void => {
    if (typeof value !== 'string' || value.trim() === '') {
      return;
    }
    for (const match of value.matchAll(TEMPLATE_VARIABLE_PATTERN)) {
      const variableName = match[1]?.trim();
      if (variableName !== undefined && variableName.length > 0) {
        entries.push({ fieldName, variableName });
      }
    }
  };

  addTemplate('targetUrl', action.targetUrl);
  addTemplate('url', action.url);
  addTemplate('selector', action.selector);
  addTemplate('inputValue', action.inputValue);
  addTemplate('requestBody', action.requestBody);
  addTemplate('message', action.message);
  addTemplate('content', action.content);
  addTemplate('defaultValue', action.defaultValue);
  addTemplate('left', action.left);
  addTemplate('right', action.right);
  addTemplate('pattern', action.pattern);
  addTemplate('delimiter', action.delimiter);
  addTemplate('targetSelector', action.targetSelector);
  addTemplate('channel', action.channel);

  addDirect('inputVariable', action.inputVariable);
  addDirect('itemsVariable', action.itemsVariable);
  addDirect('leftVariable', action.leftVariable);
  addDirect('rightVariable', action.rightVariable);

  if (action.type === 'variable.get') {
    addDirect('variableName', action.variableName);
  }
  if (action.type === 'control.condition' && typeof action.inputValue === 'string') {
    for (const variableName of extractConditionVariables(action.inputValue)) {
      entries.push({ fieldName: 'inputValue', variableName });
    }
  }

  return dedupeEntries(entries);
}

function collectProducedVariables(action: RpaNodeAction): Array<{ fieldName: string; variableName: string }> {
  const entries: Array<{ fieldName: string; variableName: string }> = [];
  const add = (fieldName: string, value: string | undefined): void => {
    if (typeof value === 'string' && value.trim() !== '') {
      entries.push({ fieldName, variableName: value.trim() });
    }
  };

  add('responseVariable', action.outputVariable ?? action.responseVariable);
  add('resultVariable', action.resultVariable);
  add('firstValueVariable', action.firstValueVariable);
  add('countVariable', action.countVariable);
  add('loadedCountVariable', action.loadedCountVariable);
  add('pageCountVariable', action.pageCountVariable);
  add('dismissedCountVariable', action.dismissedCountVariable);
  add('statusVariable', action.statusVariable);
  add('jsonVariable', action.jsonVariable);
  add('stderrVariable', action.stderrVariable);
  add('appendVariable', action.appendVariable ?? action.appendOutputVariable);

  if (action.type === 'control.foreach') {
    add('itemVariable', action.itemVariable);
    add('indexVariable', action.indexVariable);
  }
  if (action.type === 'variable.set' || action.type === 'variable.assign' || action.type === 'variable.input') {
    add('variableName', action.variableName);
  }

  return dedupeEntries(entries);
}

function dedupeEntries(entries: Array<{ fieldName: string; variableName: string }>): Array<{ fieldName: string; variableName: string }> {
  const seen = new Set<string>();
  return entries.filter((entry) => {
    const key = `${entry.fieldName}:${entry.variableName}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

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
