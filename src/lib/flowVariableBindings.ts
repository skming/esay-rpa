import type { Node } from '@xyflow/react';

import type { RpaNodeAction, RpaNodeData } from '../types/rpa';

const TEMPLATE_VARIABLE_PATTERN = /\$\{var\.([^}]+)\}/g;
const DIRECT_VARIABLE_FIELDS = new Set([
  'appendOutputVariable',
  'appendVariable',
  'countVariable',
  'dismissedCountVariable',
  'errorVariable',
  'firstValueVariable',
  'indexVariable',
  'inputVariable',
  'itemVariable',
  'itemsVariable',
  'jsonVariable',
  'leftVariable',
  'loadedCountVariable',
  'outputVariable',
  'pageCountVariable',
  'responseVariable',
  'resultVariable',
  'rightVariable',
  'saveAs',
  'statusVariable',
  'stderrVariable',
  'variableName',
]);
const VARIABLE_ARRAY_FIELDS = new Set(['inputVariables', 'outputVariables']);

export function countFlowVariableUsages(
  nodes: Node<RpaNodeData>[],
  variableNames: string[],
): Record<string, number> {
  const counts = Object.fromEntries(variableNames.map((name) => [name, 0]));
  for (const node of nodes) {
    const action = node.data.action;
    if (action === undefined) continue;
    for (const name of variableNames) {
      if (actionUsesVariable(action, name)) counts[name] += 1;
    }
  }
  return counts;
}

export function renameFlowVariableReferences(
  nodes: Node<RpaNodeData>[],
  previousName: string,
  nextName: string,
): Node<RpaNodeData>[] {
  if (previousName === nextName) return nodes;

  let flowChanged = false;
  const renamed = nodes.map((node) => {
    const action = node.data.action;
    if (action === undefined) return node;
    const nextAction = renameAction(action, previousName, nextName);
    if (nextAction === action) return node;
    flowChanged = true;
    return { ...node, data: { ...node.data, action: nextAction } };
  });
  return flowChanged ? renamed : nodes;
}

function actionUsesVariable(action: RpaNodeAction, variableName: string): boolean {
  const actionType = action.type;
  const visit = (value: unknown, fieldName?: string): boolean => {
    if (typeof value === 'string') {
      if (fieldName !== undefined && DIRECT_VARIABLE_FIELDS.has(fieldName) && value.trim() === variableName) {
        return true;
      }
      if (fieldName !== undefined && isConditionField(actionType, fieldName) && containsUnquotedToken(value, variableName)) {
        return true;
      }
      for (const match of value.matchAll(TEMPLATE_VARIABLE_PATTERN)) {
        if (referenceTargetsVariable(match[1] ?? '', variableName)) return true;
      }
      return false;
    }
    if (Array.isArray(value)) {
      if (fieldName !== undefined && VARIABLE_ARRAY_FIELDS.has(fieldName)) {
        return value.some((item) => typeof item === 'string' && item.trim() === variableName);
      }
      return value.some((item) => visit(item));
    }
    if (value === null || typeof value !== 'object') return false;
    return Object.entries(value).some(([key, entry]) => visit(entry, key));
  };
  return visit(action);
}

function renameAction(action: RpaNodeAction, previousName: string, nextName: string): RpaNodeAction {
  let changed = false;
  const actionType = action.type;

  const visit = (value: unknown, fieldName?: string): unknown => {
    if (typeof value === 'string') {
      let renamed: string;
      if (fieldName !== undefined && DIRECT_VARIABLE_FIELDS.has(fieldName) && value.trim() === previousName) {
        renamed = nextName;
      } else {
        renamed = renameTemplateReferences(value, previousName, nextName);
        if (fieldName !== undefined && isConditionField(actionType, fieldName)) {
          renamed = renameUnquotedToken(renamed, previousName, nextName);
        }
      }
      if (renamed !== value) changed = true;
      return renamed;
    }
    if (Array.isArray(value)) {
      const renamed = value.map((item) => {
        if (fieldName !== undefined && VARIABLE_ARRAY_FIELDS.has(fieldName) && typeof item === 'string' && item.trim() === previousName) {
          changed = true;
          return nextName;
        }
        return visit(item);
      });
      return renamed;
    }
    if (value === null || typeof value !== 'object') return value;
    return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, visit(entry, key)]));
  };

  const renamed = visit(action) as RpaNodeAction;
  return changed ? renamed : action;
}

function renameTemplateReferences(value: string, previousName: string, nextName: string): string {
  return value.replace(TEMPLATE_VARIABLE_PATTERN, (full, reference: string) => {
    if (!referenceTargetsVariable(reference, previousName)) return full;
    return `\${var.${nextName}${reference.slice(previousName.length)}}`;
  });
}

function referenceTargetsVariable(reference: string, variableName: string): boolean {
  return reference === variableName || reference.startsWith(`${variableName}.`);
}

function isConditionField(actionType: string, fieldName: string): boolean {
  return fieldName === 'condition'
    || fieldName === 'expression'
    || (actionType === 'control.condition' && fieldName === 'inputValue');
}

function containsUnquotedToken(value: string, target: string): boolean {
  let found = false;
  mapUnquotedTokens(value, (token) => {
    if (token === target) found = true;
    return token;
  });
  return found;
}

function renameUnquotedToken(value: string, previousName: string, nextName: string): string {
  return mapUnquotedTokens(value, (token) => token === previousName ? nextName : token);
}

function mapUnquotedTokens(value: string, transform: (token: string) => string): string {
  let result = '';
  let quote: '"' | "'" | null = null;
  let index = 0;
  while (index < value.length) {
    const character = value[index];
    if (quote !== null) {
      result += character;
      if (character === '\\' && index + 1 < value.length) {
        result += value[index + 1];
        index += 2;
        continue;
      }
      if (character === quote) quote = null;
      index += 1;
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      result += character;
      index += 1;
      continue;
    }
    if (/[A-Za-z_]/.test(character)) {
      let end = index + 1;
      while (end < value.length && /[A-Za-z0-9_.-]/.test(value[end])) end += 1;
      result += transform(value.slice(index, end));
      index = end;
      continue;
    }
    result += character;
    index += 1;
  }
  return result;
}
