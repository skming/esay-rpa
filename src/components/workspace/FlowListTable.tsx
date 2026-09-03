import { Archive, Check, CirclePause, CirclePlay, Copy, Download, Hash, History, MoreHorizontal, Play, PowerOff, Square, SquarePen, TimerReset, Trash2 } from 'lucide-react';
import type { ReactElement } from 'react';
import { useState } from 'react';

import { FlowStatusBadge } from './FlowStatusBadge';
import { SURFACE } from './surfaces';
import type { FlowCardState, FlowListItem } from '../../lib/taskCenter';
import { formatRelativeTime, formatScheduleHint } from '../../lib/taskCenter';
import type { FlowStatus } from '../../types/electron';
import { cn } from '../../lib/utils';
import { Button } from '../ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '../ui/dropdown-menu';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';

export function FlowListTable({
  items,
  onArchive,
  onDelete,
  onEdit,
  onExport,
  onHistory,
  onRun,
  onSchedule,
  onSetStatus,
  onStop,
}: {
  items: FlowListItem[];
  onArchive: (flowId: string) => void;
  onDelete: (flowId: string) => void;
  onEdit: (flowId: string) => void;
  onExport: (flowId: string) => void;
  onHistory: (flowId: string) => void;
  onRun: (flowId: string) => void;
  onSchedule: (flowId: string) => void;
  onSetStatus: (flowId: string, status: FlowStatus) => void;
  onStop: () => void;
}): ReactElement {
  return (
    <div className={cn('overflow-hidden', SURFACE)}>
      <Table className="w-full min-w-0 table-fixed">
        <TableHeader className="bg-paper-sunk">
          <TableRow className="border-rule-2 hover:bg-transparent">
            <TableHead className="w-[30%] pl-5 text-[11px] font-medium text-ink-2 xl:w-[25%]">流程</TableHead>
            <TableHead className="w-[12%] text-[11px] font-medium text-ink-2 xl:w-[10%]">状态</TableHead>
            <TableHead className="w-[11%] text-[11px] font-medium text-ink-2 xl:w-[10%]">最近结果</TableHead>
            <TableHead className="w-[12%] text-[11px] font-medium text-ink-2 xl:w-[11%]">30天成功率</TableHead>
            <TableHead className="w-[14%] text-[11px] font-medium text-ink-2">下次触发</TableHead>
            <TableHead className="hidden text-[11px] font-medium text-ink-2 xl:table-cell xl:w-[12%]">最近修改</TableHead>
            <TableHead className="w-[21%] pr-5 text-right text-[11px] font-medium text-ink-2 xl:w-[18%]">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow
              className={cn(
                'border-rule hover:bg-paper',
                item.state === 'disabled' && 'opacity-50',
                item.state === 'paused' && 'opacity-65',
                item.state === 'failed' && 'bg-red-50/40 hover:bg-red-50/60',
              )}
              key={item.flow.flowId}
            >
              <TableCell className="pl-5">
                <div className="min-w-0">
                  <button className="min-w-0 text-left" onClick={() => onEdit(item.flow.flowId)} type="button">
                    <div className="truncate text-[12.5px] font-medium text-ink">{item.flow.name}</div>
                  </button>
                  <div className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] text-ink-3">
                    <span className="tabular-nums">{item.flow.version}</span>
                    <span>·</span>
                    <span className="truncate font-sans">{item.folderPath}</span>
                  </div>
                  <FlowIdChip className="mt-1 max-w-48" flowId={item.flow.flowId} />
                </div>
              </TableCell>
              <TableCell><FlowStatusBadge state={item.state} /></TableCell>
              <TableCell className="truncate text-[11px] text-ink-3">{formatLastRun(item.lastRunStatus)}</TableCell>
              <TableCell className={cn(
                'font-mono text-[11px] font-semibold tabular-nums',
                item.successRate === null ? 'text-ink-4' :
                  item.successRate < 90 ? 'text-amber-700' : 'text-emerald-700',
              )}>
                {item.successRate === null ? '--' : `${item.successRate}%`}
              </TableCell>
              <TableCell className="truncate font-mono text-[10px] text-ink-3">{formatScheduleHint(item.nextRunAt)}</TableCell>
              <TableCell className="hidden truncate text-[11px] text-ink-3 xl:table-cell">{formatRelativeTime(item.flow.updatedAt)}</TableCell>
              <TableCell className="pr-5">
                <div className="flex justify-end gap-1">
                  {item.state === 'running' ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button aria-label={`停止 ${item.flow.name}`} className="h-7 w-7 rounded-md px-0 text-red-600 hover:text-red-700" onClick={onStop} variant="subtle">
                          <Square className="h-3 w-3" fill="currentColor" strokeWidth={1.5} />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">停止运行</TooltipContent>
                    </Tooltip>
                  ) : (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          aria-label={`运行 ${item.flow.name}`}
                          className="h-7 w-7 rounded-md px-0"
                          disabled={item.state === 'disabled' || item.state === 'paused'}
                          onClick={() => onRun(item.flow.flowId)}
                          variant="subtle"
                        >
                          <Play className="h-3.5 w-3.5" strokeWidth={1.5} />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">运行</TooltipContent>
                    </Tooltip>
                  )}
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button aria-label={`设置 ${item.flow.name} 的调度`} className="h-7 w-7 rounded-md px-0" onClick={() => onSchedule(item.flow.flowId)} variant="subtle">
                        <TimerReset className="h-3.5 w-3.5" strokeWidth={1.5} />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">调度设置</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button aria-label={`编辑 ${item.flow.name}`} className="h-7 w-7 rounded-md px-0" onClick={() => onEdit(item.flow.flowId)} variant="primary">
                        <SquarePen className="h-3.5 w-3.5" strokeWidth={1.5} />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">编辑</TooltipContent>
                  </Tooltip>
                  <DropdownMenu>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <DropdownMenuTrigger asChild>
                          <Button aria-label={`${item.flow.name} 的更多操作`} className="h-7 w-7 rounded-md px-0" variant="subtle">
                            <MoreHorizontal className="h-3.5 w-3.5" strokeWidth={1.5} />
                          </Button>
                        </DropdownMenuTrigger>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">更多操作</TooltipContent>
                    </Tooltip>
                    <DropdownMenuContent align="end" className="w-36">
                      <DropdownMenuItem onSelect={() => onHistory(item.flow.flowId)}>
                        <History className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />运行历史
                      </DropdownMenuItem>
                      <DropdownMenuItem onSelect={() => onExport(item.flow.flowId)}>
                        <Download className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />导出 JSON
                      </DropdownMenuItem>
                      {item.state === 'running' && (
                        <>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem className="text-red-600 focus:text-red-600" onSelect={onStop}>
                            <Square className="mr-2 h-3.5 w-3.5" fill="currentColor" strokeWidth={1.5} />停止运行
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                        </>
                      )}
                      <StatusActions flowId={item.flow.flowId} onSetStatus={onSetStatus} state={item.state} />
                      <DropdownMenuItem onSelect={() => onArchive(item.flow.flowId)}>
                        <Archive className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />归档
                      </DropdownMenuItem>
                      <DropdownMenuItem className="text-red-600 focus:text-red-600" onSelect={() => onDelete(item.flow.flowId)}>
                        <Trash2 className="mr-2 h-3.5 w-3.5" strokeWidth={1.5} />删除任务
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function FlowIdChip({ flowId, className }: { flowId: string; className?: string }): ReactElement {
  const [copied, setCopied] = useState(false);
  const display = flowId.replace(/^local-\d+-?/, '');

  function copy(e: React.MouseEvent) {
    e.stopPropagation();
    void navigator.clipboard.writeText(flowId).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className={cn('group flex w-full min-w-0 items-center gap-1', className)}>
      <Hash className="h-2.5 w-2.5 shrink-0 text-ink-4" strokeWidth={2} />
      <span className="min-w-0 flex-1 truncate font-mono text-[10px] tabular-nums text-ink-3">{display}</span>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            aria-label={copied ? '已复制流程 ID' : '复制流程 ID'}
            className={cn(
              'shrink-0 rounded p-0.5 transition-colors',
              copied ? 'text-emerald-600' : 'text-ink-4 opacity-0 group-hover:opacity-100 hover:text-ink-2',
            )}
            onClick={copy}
            type="button"
          >
            {copied
              ? <Check className="h-3 w-3" strokeWidth={2.5} />
              : <Copy className="h-3 w-3" strokeWidth={2} />
            }
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="font-mono text-[10px]">
          {copied ? '已复制' : '点击复制'}
        </TooltipContent>
      </Tooltip>
    </div>
  );
}

function StatusActions({
  flowId,
  state,
  onSetStatus,
}: {
  flowId: string;
  state: FlowCardState;
  onSetStatus: (flowId: string, status: FlowStatus) => void;
}): ReactElement | null {
  if (state === 'running') return null;
  return (
    <>
      <DropdownMenuSeparator />
      {(state === 'draft' || state === 'disabled' || state === 'paused') && (
        <DropdownMenuItem onSelect={() => onSetStatus(flowId, 'active')}>
          <CirclePlay className="mr-2 h-3.5 w-3.5 text-emerald-500" strokeWidth={1.5} />
          {state === 'draft' ? '发布启用' : '恢复启用'}
        </DropdownMenuItem>
      )}
      {(state === 'published' || state === 'scheduled' || state === 'failed') && (
        <DropdownMenuItem onSelect={() => onSetStatus(flowId, 'paused')}>
          <CirclePause className="mr-2 h-3.5 w-3.5 text-amber-500" strokeWidth={1.5} />暂停
        </DropdownMenuItem>
      )}
      {state !== 'disabled' && (
        <DropdownMenuItem onSelect={() => onSetStatus(flowId, 'disabled')}>
          <PowerOff className="mr-2 h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />禁用
        </DropdownMenuItem>
      )}
      <DropdownMenuSeparator />
    </>
  );
}

function formatLastRun(status: string | null): string {
  const map: Record<string, string> = {
    success: '成功',
    error: '失败',
    running: '运行中',
    stopped: '已停止',
    queued: '排队中',
    paused_for_human: '等待操作',
  };
  return status !== null ? (map[status] ?? '--') : '--';
}
