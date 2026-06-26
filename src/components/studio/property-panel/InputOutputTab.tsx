import type { Node } from '@xyflow/react';
import type { ReactElement } from 'react';

import { buildNodeVariableDiagnostics, getIoFieldStatus } from '../../../lib/nodeVariableDiagnostics';
import { buildNodeIoSummary } from '../../../lib/nodeIoSummary';
import type { RpaNodeData, RuntimeVariable } from '../../../types/rpa';
import { Badge } from '../../ui/badge';
import { PanelSection } from './PanelSection';

export function InputOutputTab({
  flowNodes,
  node,
  variables
}: {
  flowNodes: Node<RpaNodeData>[];
  node: Node<RpaNodeData>;
  variables: RuntimeVariable[];
}): ReactElement {
  const summary = buildNodeIoSummary(node);
  const diagnostics = buildNodeVariableDiagnostics(node, variables.map((variable) => variable.name), flowNodes);

  return (
    <>
      <PanelSection title="输入参数">
        {summary.inputs.length === 0 ? <EmptyRow text="当前节点没有显式输入依赖" /> : summary.inputs.map((row) => <IoRow description={row.description} key={row.name} name={row.name} status={getIoFieldStatus(diagnostics.inputIssues, row.name)} type={row.type} />)}
      </PanelSection>
      <PanelSection title="输出变量">
        {summary.outputs.length === 0 ? <EmptyRow text="当前节点不会写入输出变量" /> : summary.outputs.map((row) => <IoRow description={row.description} key={row.name} name={row.name} status={getIoFieldStatus(diagnostics.outputIssues, row.name)} type={row.type} />)}
      </PanelSection>
    </>
  );
}

function EmptyRow({ text }: { text: string }): ReactElement {
  return <div className="rounded-md border border-dashed border-slate-200 bg-slate-50 px-2 py-1.5 text-[11px] leading-4 text-slate-400">{text}</div>;
}

function IoRow({
  description,
  name,
  status,
  type
}: {
  description: string;
  name: string;
  status: { note?: string; tone?: 'default' | 'warn' | 'error' };
  type: string;
}): ReactElement {
  return (
    <div
      className={`rounded-md px-2 py-1.5 text-[11px] leading-4 ${status.tone === 'error' ? 'border border-red-200 bg-red-50' : status.tone === 'warn' ? 'border border-amber-200 bg-amber-50' : 'bg-slate-50'
        }`}
    >
      <div className="flex items-center gap-2">
        <Badge variant={getBadgeVariant(type)}>{type}</Badge>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-blue-700">{name}</span>
        <span className="max-w-22.5 truncate font-mono text-[10px] text-slate-400">{description}</span>
      </div>
      {status.note !== undefined && <div className={`mt-1 text-[10px] leading-4 ${status.tone === 'error' ? 'text-red-700' : 'text-amber-700'}`}>{status.note}</div>}
    </div>
  );
}

function getBadgeVariant(type: string): 'amber' | 'blue' | 'emerald' | 'red' | 'violet' | 'default' {
  const variants: Record<string, 'amber' | 'blue' | 'emerald' | 'red' | 'violet' | 'default'> = {
    Boolean: 'amber',
    Dict: 'red',
    Integer: 'violet',
    List: 'emerald',
    String: 'blue',
    变量: 'violet',
    配置: 'default'
  };

  return variants[type] ?? 'default';
}
