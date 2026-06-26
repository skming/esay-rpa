import type { ReactElement } from 'react';

import type { FlowSnapshot } from '../../types/electron';

export function FlowThumbnail({ flow }: { flow: FlowSnapshot }): ReactElement {
  const nodeCount = readNodeCount(flow);
  const previewNodes = Array.from({ length: Math.min(Math.max(nodeCount, 2), 5) });

  return (
    <div className="relative h-10 overflow-hidden rounded-md border border-rule bg-paper-sunk">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_1px_1px,oklch(0.215_0.006_277/0.14)_1px,transparent_0)] bg-size-[10px_10px]" />
      <div className="absolute inset-x-3 top-2 flex items-center justify-between gap-1">
        {previewNodes.map((_, index) => (
          <div className="h-4 min-w-6 rounded border border-rule-2 bg-surface" key={index}>
            <div className="mx-1 mt-1 h-1 rounded bg-ink-4" />
          </div>
        ))}
      </div>
    </div>
  );
}

function readNodeCount(flow: FlowSnapshot): number {
  const nodes = flow.definition.nodes;
  return Array.isArray(nodes) ? nodes.length : 3;
}
