import type { Node } from '@xyflow/react';
import { describe, expect, it } from 'vitest';

import { countFlowVariableUsages, renameFlowVariableReferences } from './flowVariableBindings';
import type { RpaNodeAction, RpaNodeData } from '../types/rpa';

function node(id: string, action: RpaNodeAction): Node<RpaNodeData> {
  return {
    id,
    position: { x: 0, y: 0 },
    data: { action, description: '', kind: 'browser', status: 'pending', title: id },
  };
}

describe('flow variable bindings', () => {
  it('counts template, structured, and condition references by node', () => {
    const nodes = [
      node('open', { type: 'browser.open', targetUrl: 'https://example.com?q=${var.keyword}' }),
      node('condition', { type: 'control.condition', inputValue: 'keyword == "keyword"' }),
      node('extract', { type: 'browser.extract', outputVariable: 'keyword' }),
    ];

    expect(countFlowVariableUsages(nodes, ['keyword', 'unused'])).toEqual({ keyword: 3, unused: 0 });
  });

  it('renames all explicit bindings while leaving quoted condition values unchanged', () => {
    const nodes = [
      node('open', {
        type: 'browser.open',
        targetUrl: 'https://example.com/${var.keyword}?source=${var.keyword.source}',
      }),
      node('condition', { type: 'control.condition', inputValue: 'keyword == "keyword"' }),
      node('script', {
        type: 'script.python',
        inputVariables: ['keyword'],
        outputVariables: ['result'],
      } as RpaNodeAction),
      node('set', { type: 'variable.set', variableName: 'keyword', value: '${var.keyword}' }),
    ];

    const renamed = renameFlowVariableReferences(nodes, 'keyword', 'search_term');

    expect(renamed[0]?.data.action?.targetUrl).toBe('https://example.com/${var.search_term}?source=${var.search_term.source}');
    expect(renamed[1]?.data.action?.inputValue).toBe('search_term == "keyword"');
    expect((renamed[2]?.data.action as RpaNodeAction & { inputVariables?: string[] }).inputVariables).toEqual(['search_term']);
    expect(renamed[3]?.data.action).toMatchObject({ variableName: 'search_term', value: '${var.search_term}' });
  });

  it('preserves node identity when the variable is unused', () => {
    const nodes = [node('open', { type: 'browser.open', targetUrl: 'https://example.com' })];
    expect(renameFlowVariableReferences(nodes, 'missing', 'renamed')).toBe(nodes);
  });
});
