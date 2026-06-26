import { describe, expect, it } from 'vitest';

import { cloneFlowTemplate, flowTemplates } from './flowTemplates';
import { getBlockingRunIssue, validateRunConfiguration } from './runValidation';

describe('flowTemplates', () => {
  it('所有场景模板都应具备可执行节点并通过运行前硬错误校验', () => {
    expect(flowTemplates.map((template) => template.id)).toEqual([
      'static-list',
      'popup-safe-list',
      'login-browser',
      'search-results',
      'api-json',
      'pagination-list',
      'list-detail-browser',
      'next-button-pagination',
      'infinite-scroll',
      'click-load-more',
      'csv-loop-detail'
    ]);

    for (const template of flowTemplates) {
      const snapshot = cloneFlowTemplate(template);
      const result = validateRunConfiguration(snapshot.nodes, snapshot.edges, {
        availableVariableNames: snapshot.variables.map((variable) => variable.name),
        scope: 'full'
      });

      expect(getBlockingRunIssue(result), template.id).toBeNull();
    }
  });

  it('复制模板时不复用节点和变量对象引用', () => {
    const template = flowTemplates[0];
    const first = cloneFlowTemplate(template);
    const second = cloneFlowTemplate(template);

    expect(first.nodes[0]).not.toBe(second.nodes[0]);
    expect(first.nodes[0]?.data).not.toBe(second.nodes[0]?.data);
    expect(first.edges[0]).not.toBe(second.edges[0]);
  });
});
