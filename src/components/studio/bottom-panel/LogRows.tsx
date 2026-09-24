import { ChevronRight } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useRef, useState } from 'react';

import { cn } from '../../../lib/utils';
import type { RunLogEntry } from '../../../types/rpa';
import { CopyButton } from '../../ui/copy-button';
import { Table, TableBody, TableCell, TableRow } from '../../ui/table';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../../ui/tooltip';
import { getLogTone } from './bottomPanelUtils';

function findScrollParent(element: HTMLElement | null): HTMLElement | null {
  let current = element?.parentElement ?? null;
  while (current !== null) {
    const overflowY = window.getComputedStyle(current).overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll') return current;
    current = current.parentElement;
  }
  return null;
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
  // 只在用户本来就贴着底部时才跟随新日志：运行中往回翻查旧日志时不该被拽走
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    const container = findScrollParent(endRef.current);
    if (container === null) return;
    const handleScroll = (): void => {
      stickToBottomRef.current = container.scrollHeight - container.scrollTop - container.clientHeight < 40;
    };
    container.addEventListener('scroll', handleScroll, { passive: true });
    return () => container.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    if (!stickToBottomRef.current) return;
    endRef.current?.scrollIntoView({ block: 'nearest' });
  }, [rows.length]);

  return (
    <TooltipProvider delayDuration={300}>
      <Table className="table-fixed">
        <TableBody>
          {rows.map((row) => (
            <LogRow key={row.id} nodeTitleById={nodeTitleById} onJumpToNode={onJumpToNode} row={row} />
          ))}
        </TableBody>
      </Table>
      <div ref={endRef} />
    </TooltipProvider>
  );
}

// 长 detail 之前只藏在悬停 tooltip 里，鼠标移开就没了、也没法逐段看：改成点击整行消息就地展开，
// 完整上下文原样换行铺开，仍保留 tooltip 快速预览与右侧复制。每行各自维护展开态，故拆成独立组件。
function LogRow({
  nodeTitleById,
  onJumpToNode,
  row,
}: {
  nodeTitleById: Record<string, string>;
  onJumpToNode: (nodeId: string) => void;
  row: RunLogEntry;
}): ReactElement {
  const [expanded, setExpanded] = useState(false);
  const tone = getLogTone(row.level);
  const canJump = typeof row.nodeId === 'string' && row.nodeId.length > 0;
  const nodeTitle = canJump ? nodeTitleById[row.nodeId as string] : undefined;
  const hasDetail = row.detail !== undefined && row.detail.length > 0;
  const fullText = hasDetail ? `${row.message} · ${row.detail}` : row.message;

  return (
    <TableRow className={cn('border-0', tone.row, tone.text)}>
      <TableCell className="w-24 py-1 pl-2 align-top font-mono text-[11px] tabular-nums text-slate-500">
        {row.time}
      </TableCell>
      <TableCell className="w-3.5 px-0 py-1 align-top">
        <span className={cn('mt-1 block h-1.5 w-1.5 rounded-full', tone.dot)} />
      </TableCell>
      <TableCell className="py-1 align-top font-mono text-[11px]">
        {hasDetail ? (
          <button
            aria-expanded={expanded}
            aria-label={expanded ? '收起日志详情' : '展开日志详情'}
            className="flex w-full min-w-0 items-start gap-1 text-left"
            onClick={() => setExpanded((value) => !value)}
            type="button"
          >
            <ChevronRight className={cn('mt-[3px] h-3 w-3 shrink-0 text-slate-400 transition-transform', expanded && 'rotate-90')} strokeWidth={1.5} />
            {expanded ? (
              <span className="min-w-0 flex-1">
                <span className="block">{row.message}</span>
                <span className="mt-0.5 block whitespace-pre-wrap break-words text-slate-500">{row.detail}</span>
              </span>
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="block min-w-0 flex-1 truncate">
                    {row.message}
                    <span className="ml-2 text-slate-500">{row.detail}</span>
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-120 break-all leading-5" side="top">
                  {fullText}
                </TooltipContent>
              </Tooltip>
            )}
          </button>
        ) : (
          <div className="flex items-start gap-1">
            <span className="mt-[3px] h-3 w-3 shrink-0" />
            <span className="block min-w-0 flex-1 truncate">{row.message}</span>
          </div>
        )}
      </TableCell>
      <TableCell className="w-36 py-1 align-top">
        {canJump ? (
          <Tooltip>
            <TooltipTrigger asChild>
              {/* h-5 = 20px，低于 24px 目标下限，靠 SC 2.5.8 的间距豁免成立：
                  同行的 CopyButton 是 h-6，行高被撑到 32px，同列相邻行的
                  24px 判定圆互不相交。改行距或去掉 CopyButton 时要重算。 */}
              <button
                aria-label={`定位到节点 ${nodeTitle ?? row.nodeId}`}
                className="flex h-5 max-w-full items-center rounded-full border border-slate-200 bg-slate-100 px-1.5 text-[10px] font-medium text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-200/70 hover:text-slate-800"
                onClick={() => onJumpToNode(row.nodeId as string)}
                type="button"
              >
                <span className="truncate">{nodeTitle ?? `节点 ${row.nodeId}`}</span>
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-80" side="top">
              <div className="font-medium text-slate-800">{nodeTitle ?? '未知节点'}</div>
              <div className="mt-0.5 break-all font-mono text-[10px] text-slate-500">{row.nodeId}</div>
            </TooltipContent>
          </Tooltip>
        ) : (
          <span className="text-[10px] text-slate-500">未绑定节点</span>
        )}
      </TableCell>
      <TableCell className="w-8 py-1 pr-2 align-top">
        <div className="flex items-center justify-end">
          <CopyButton text={fullText} />
        </div>
      </TableCell>
    </TableRow>
  );
}
