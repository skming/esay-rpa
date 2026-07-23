import { Check, Copy, ExternalLink } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useRef, useState } from 'react';

import { cn } from '../../../lib/utils';
import type { RunLogEntry } from '../../../types/rpa';
import { Button } from '../../ui/button';
import { Badge } from '../../ui/badge';
import { Table, TableBody, TableCell, TableRow } from '../../ui/table';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../../ui/tooltip';
import { getLogTone } from './bottomPanelUtils';

function CopyButton({ text }: { text: string }): ReactElement {
  const [copied, setCopied] = useState(false);

  const handleCopy = (): void => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <Button className="h-6 w-6 px-0" onClick={handleCopy} title="复制" variant="ghost">
      {copied ? <Check className="h-3 w-3 text-emerald-500" strokeWidth={2} /> : <Copy className="h-3 w-3" strokeWidth={1.5} />}
    </Button>
  );
}

export function LogRows({
  nodeTitleById,
  onJumpToNode,
  rows
}: {
  nodeTitleById: Record<string, string>;
  onJumpToNode: (nodeId: string) => void;
  rows: RunLogEntry[];
}): ReactElement {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' });
  }, [rows.length]);

  return (
    <TooltipProvider delayDuration={300}>
      <Table className="table-fixed">
        <TableBody>
          {rows.map((row) => {
            const tone = getLogTone(row.level);
            const canJump = typeof row.nodeId === 'string' && row.nodeId.length > 0;
            const nodeTitle = canJump ? nodeTitleById[row.nodeId as string] : undefined;
            const fullText = row.detail !== undefined ? `${row.message} · ${row.detail}` : row.message;
            return (
              <TableRow
                className={cn('border-0', tone.row, tone.text)}
                key={row.id}
              >
                <TableCell className="w-24 py-1 pl-2 font-mono text-[11px] tabular-nums text-slate-500">
                  {row.time}
                </TableCell>
                <TableCell className="w-3.5 py-1 px-0">
                  <span className={cn('block h-1.5 w-1.5 rounded-full', tone.dot)} />
                </TableCell>
                <TableCell className="py-1 font-mono text-[11px]">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="min-w-0 cursor-default truncate block">
                        {row.message}
                        {row.detail !== undefined && <span className="ml-2 text-slate-500">{row.detail}</span>}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-120 break-all leading-5" side="top">
                      {fullText}
                    </TooltipContent>
                  </Tooltip>
                </TableCell>
                <TableCell className="w-30 py-1">
                  {canJump ? (
                    <Badge className="max-w-full truncate justify-start px-1.5 text-[10px]" variant="default">
                      {nodeTitle === undefined ? `节点 ${row.nodeId}` : `${nodeTitle} · ${row.nodeId}`}
                    </Badge>
                  ) : (
                    <span className="text-[10px] text-slate-500">未绑定节点</span>
                  )}
                </TableCell>
                <TableCell className="w-14 py-1 pr-2">
                  <div className="flex items-center justify-end">
                    <CopyButton text={fullText} />
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          className="h-6 w-6 px-0"
                          disabled={!canJump}
                          onClick={() => { if (canJump) onJumpToNode(row.nodeId as string); }}
                          variant="ghost"
                        >
                          <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.5} />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent side="top">定位到节点</TooltipContent>
                    </Tooltip>
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <div ref={endRef} />
    </TooltipProvider>
  );
}
