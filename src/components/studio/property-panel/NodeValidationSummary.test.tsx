import type { Node } from '@xyflow/react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import type { RpaNodeData } from '../../../types/rpa';
import { NodeValidationSummary } from './NodeValidationSummary';

const { issues } = vi.hoisted(() => ({
  issues: Array.from({ length: 6 }, (_, index) => ({
    nodeId: 'node-1',
    severity: 'error' as const,
    message: `配置问题 ${index + 1}`,
  })),
}));

vi.mock('../../../lib/nodeVariableDiagnostics', () => ({
  buildNodeVariableDiagnostics: () => ({ inputIssues: [], outputIssues: [] }),
}));

vi.mock('../../../lib/runtimeVariables', () => ({
  mergeRuntimeVariables: () => [],
}));

vi.mock('../../../lib/runValidation', () => ({
  validateNodeConfigurationInFlow: () => issues,
}));

vi.mock('../../../stores/useFlowVariableStore', () => ({
  useFlowVariableStore: (
    selector: (state: { addNamedInputVariable: () => void; inputVariables: [] }) => unknown,
  ) => selector({ addNamedInputVariable: vi.fn(), inputVariables: [] }),
}));

describe('NodeValidationSummary', () => {
  it('keeps validation issues after the fourth discoverable', () => {
    const node = {
      id: 'node-1',
      position: { x: 0, y: 0 },
      data: {
        title: '测试节点',
        description: '验证配置',
        kind: 'browser',
        status: 'pending',
      },
    } as Node<RpaNodeData>;

    const html = renderToStaticMarkup(
      <NodeValidationSummary
        flowEdges={[]}
        flowNodes={[node]}
        inputVariables={[]}
        node={node}
        runtimeVariables={[]}
      />,
    );

    expect(html).toContain('还有 2 项');
    expect(html).toContain('配置问题 6');
  });
});
