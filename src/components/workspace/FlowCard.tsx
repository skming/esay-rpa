import { Archive, CirclePause, CirclePlay, Download, History, MoreHorizontal, Play, PowerOff, SquarePen, TimerReset, Trash2 } from 'lucide-react';
import type { ReactElement } from 'react';

import { FlowStatusBadge } from './FlowStatusBadge';
import { FlowThumbnail } from './FlowThumbnail';
import { FlowIdChip } from './FlowListTable';
import type { FlowListItem } from '../../lib/taskCenter';
import { formatRelativeTime, formatScheduleHint } from '../../lib/taskCenter';
import type { FlowStatus } from '../../types/electron';
import { cn } from '../../lib/utils';
import { Button, IconButton } from '../ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '../ui/dropdown-menu';

export function FlowCard({
  item,
  onArchive,
  onDelete,
  onEdit,
  onExport,
  onHistory,
  onRun,
  onSchedule,
  onSetStatus,
}: {
  item: FlowListItem;
  onArchive: (flowId: string) => void;
  onDelete: (flowId: string) => void;
  onEdit: (flowId: string) => void;
  onExport: (flowId: string) => void;
  onHistory: (flowId: string) => void;
  onRun: (flowId: string) => void;
  onSchedule: (flowId: string) => void;
  onSetStatus: (flowId: string, status: FlowStatus) => void;
}): ReactElement {
  const disabled = item.state === 'disabled';
  const paused = item.state === 'paused';

  return (
    <div
      className={cn(
        'group flex w-full flex-col gap-3 rounded-md border border-rule bg-surface p-4',
        'transition-colors duration-150 hover:border-rule-2',
        disabled && 'opacity-50',
        paused && 'opacity-70',
      )}
    >
      {/* 缩略图 + 菜单 */}
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <FlowThumbnail flow={item.flow} />
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <IconButton
              className="h-7 w-7 opacity-0 transition-opacity duration-150 group-hover:opacity-100"
              label="流程操作"
            >
              <MoreHorizontal className="h-3.5 w-3.5" strokeWidth={1.5} />
            </IconButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-40">
            <DropdownMenuItem onSelect={() => onEdit(item.flow.flowId)}>
              <SquarePen className="mr-2 h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />
              编辑
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onRun(item.flow.flowId)}>
              <Play className="mr-2 h-3.5 w-3.5 text-emerald-500" strokeWidth={1.5} />
              立即运行
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onSchedule(item.flow.flowId)}>
              <TimerReset className="mr-2 h-3.5 w-3.5 text-amber-500" strokeWidth={1.5} />
              调度设置
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onHistory(item.flow.flowId)}>
              <History className="mr-2 h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />
              运行历史
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onExport(item.flow.flowId)}>
              <Download className="mr-2 h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />
              导出 JSON
            </DropdownMenuItem>
            {item.state !== 'running' && (
              <>
                <DropdownMenuSeparator />
                {(item.state === 'draft' || item.state === 'disabled' || item.state === 'paused') && (
                  <DropdownMenuItem onSelect={() => onSetStatus(item.flow.flowId, 'active')}>
                    <CirclePlay className="mr-2 h-3.5 w-3.5 text-emerald-500" strokeWidth={1.5} />
                    {item.state === 'draft' ? '发布启用' : '恢复启用'}
                  </DropdownMenuItem>
                )}
                {(item.state === 'published' || item.state === 'scheduled' || item.state === 'failed') && (
                  <DropdownMenuItem onSelect={() => onSetStatus(item.flow.flowId, 'paused')}>
                    <CirclePause className="mr-2 h-3.5 w-3.5 text-amber-500" strokeWidth={1.5} />
                    暂停
                  </DropdownMenuItem>
                )}
                {item.state !== 'disabled' && (
                  <DropdownMenuItem onSelect={() => onSetStatus(item.flow.flowId, 'disabled')}>
                    <PowerOff className="mr-2 h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />
                    禁用
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
              </>
            )}
            <DropdownMenuItem onSelect={() => onArchive(item.flow.flowId)}>
              <Archive className="mr-2 h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />
              归档
            </DropdownMenuItem>
            <DropdownMenuItem
              className="text-red-600 focus:text-red-600"
              onSelect={() => onDelete(item.flow.flowId)}
            >
              <Trash2 className="mr-2 h-3.5 w-3.5" strokeWidth={1.5} />
              删除
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* 名称 + 状态 */}
      <div className="min-w-0">
        <button
          className="min-w-0 w-full text-left"
          disabled={disabled}
          onClick={() => onEdit(item.flow.flowId)}
          type="button"
        >
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-[13.5px] font-semibold text-ink">{item.flow.name}</span>
            <span className="shrink-0 rounded border border-rule-2 bg-paper-sunk px-1.5 py-0.5 font-mono text-[9px] tabular-nums text-ink-3">
              {item.flow.version}
            </span>
          </div>
        </button>
        <div className="mt-1.5 flex items-center justify-between gap-2">
          <FlowIdChip flowId={item.flow.flowId} />
        </div>
        <div className="mt-1.5 flex items-center justify-between gap-2">
          <span className="truncate text-[11px] text-ink-4">
            {formatRelativeTime(item.flow.updatedAt)}
          </span>
          <FlowStatusBadge state={item.state} />
        </div>
      </div>

      {/* 调度信息 + 成功率 */}
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="truncate text-ink-4">{formatScheduleHint(item.nextRunAt)}</span>
        <span
          className={cn(
            'font-mono font-semibold tabular-nums',
            item.successRate === null ? 'text-ink-4' :
              item.successRate < 90 ? 'text-amber-700' : 'text-emerald-700',
          )}
        >
          {item.successRate === null ? '--' : `${item.successRate}%`}
        </span>
      </div>

      {/* 状态条 */}
      {item.state === 'running' && (
        <div className="h-0.75 overflow-hidden rounded-full bg-live-soft">
          <div className="h-full w-2/3 rounded-full bg-live" />
        </div>
      )}
      {item.state === 'failed' && (
        <div className="rounded-md border border-red-200/70 bg-red-50/50 px-2.5 py-1.5 text-[11px] font-medium text-red-600">
          上次运行失败
        </div>
      )}
      {item.state === 'paused' && (
        <div className="flex items-center justify-between rounded-md border border-amber-200/70 bg-amber-50/50 px-2.5 py-1.5">
          <span className="text-[11px] font-medium text-amber-700">已暂停</span>
          <button
            className="text-[11px] font-medium text-amber-700 underline-offset-2 hover:underline"
            onClick={() => onSetStatus(item.flow.flowId, 'active')}
            type="button"
          >
            恢复
          </button>
        </div>
      )}

      {/* 操作按钮 */}
      {item.state !== 'running' && item.state !== 'failed' && item.state !== 'paused' && (
        <div className="flex justify-end gap-1.5 pt-0.5">
          <Button
            className="h-7 rounded-md px-2.5 text-[11px]"
            disabled={disabled}
            onClick={() => onRun(item.flow.flowId)}
            variant="ledger"
          >
            <Play className="h-3 w-3" strokeWidth={1.5} />
            运行
          </Button>
          <Button
            className="h-7 rounded-md px-3 text-[11px]"
            onClick={() => onEdit(item.flow.flowId)}
            variant="primary"
          >
            编辑
          </Button>
        </div>
      )}
    </div>
  );
}
