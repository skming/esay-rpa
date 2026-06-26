import type { Node } from '@xyflow/react';
import type { ReactElement } from 'react';

import { buildNodeExecutionSummary } from '../../../lib/nodeExecutionSummary';
import type { RpaNodeData } from '../../../types/rpa';
import { Badge } from '../../ui/badge';

export function NodeExecutionSummary({ node }: { node: Node<RpaNodeData> }): ReactElement | null {
  const summary = buildNodeExecutionSummary(node);
  if (summary === null) {
    return null;
  }

  return (
    <div className="mb-3 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium leading-none text-slate-700">{summary.title}</span>
        <Badge variant="blue">{summary.rows.length} 项</Badge>
      </div>
      <div className="mt-1.5 space-y-1">
        {summary.rows.slice(0, 6).map((row) => (
          <div className="rounded border border-white bg-white px-2 py-1 text-[10px] leading-4" key={`${row.name}-${row.description}`}>
            <span className="text-slate-400">{row.type}</span>
            <div className="mt-0.5 truncate font-mono text-slate-700" title={row.description}>{row.description}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
