import { CheckCircle2, Copy, ExternalLink } from 'lucide-react';
import type { ReactElement } from 'react';
import { useState } from 'react';

import type { RunLogEntry } from '../../../types/rpa';
import { Button } from '../../ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../../ui/tooltip';
import { PanelEmptyState } from './PanelEmptyState';

export function ErrorRows({ onJumpToNode, rows }: { onJumpToNode: (nodeId: string) => void; rows: RunLogEntry[] }): ReactElement {
  if (rows.length === 0) {
    return <PanelEmptyState icon={CheckCircle2} text="暂无错误信息" tone="text-emerald-500" />;
  }

  return (
    <TooltipProvider delayDuration={300}>
      <div className="min-w-140 space-y-1.5">
        {rows.map((row) => (
          <ErrorRow key={row.id} onJumpToNode={onJumpToNode} row={row} />
        ))}
      </div>
    </TooltipProvider>
  );
}

function ErrorRow({ onJumpToNode, row }: { onJumpToNode: (nodeId: string) => void; row: RunLogEntry }): ReactElement {
  const [copied, setCopied] = useState(false);

  const handleCopy = (): void => {
    const text = [row.message, row.detail].filter(Boolean).join('\n');
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="flex gap-2 rounded-lg border border-red-100 bg-red-50 px-2 py-2 text-[11px]">
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex items-center gap-2 text-[10px] text-red-400">
          <span className="font-mono tabular-nums">{row.time}</span>
          {row.nodeId !== undefined && <span className="font-mono">节点 {row.nodeId}</span>}
        </div>
        <div className="select-text wrap-break-word font-semibold text-red-700">{row.message}</div>
        {row.detail !== undefined
          ? <pre className="select-text whitespace-pre-wrap break-all font-mono text-[10px] text-red-500">{row.detail}</pre>
          : <div className="font-mono text-[10px] text-red-300">无更多错误上下文</div>
        }
      </div>
      <div className="flex shrink-0 flex-col gap-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              className="h-7 w-7 border-red-200 bg-white px-0 text-red-500 hover:bg-red-100"
              onClick={handleCopy}
              variant="outline"
            >
              <Copy className="h-3.5 w-3.5" strokeWidth={1.5} />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">{copied ? '已复制' : '复制错误'}</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              className="h-7 w-7 border-red-200 bg-white px-0 text-red-500 hover:bg-red-100"
              disabled={row.nodeId === undefined}
              onClick={() => { if (row.nodeId !== undefined) onJumpToNode(row.nodeId); }}
              variant="outline"
            >
              <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.5} />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">定位到节点</TooltipContent>
        </Tooltip>
      </div>
    </div>
  );
}
