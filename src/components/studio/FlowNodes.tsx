import { AlertCircle, Ban, CheckCircle2, Circle, Copy, Dot, Loader2, Pencil, Trash2 } from 'lucide-react';
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import type { LucideIcon } from 'lucide-react';
import type { MouseEvent, ReactElement } from 'react';

import { kindStyles } from '../../data/studioData';
import { cn } from '../../lib/utils';
import type { ContextMenuAction, NodeStatus, RpaNodeData } from '../../types/rpa';

// 状态用顶部通栏（颜色+运行中呼吸动画）加文字 pill 双重展示，方便隔着画布一眼判断
const NODE_STATUS: Record<NodeStatus, {
  label: string; icon: LucideIcon; bar: string; pill: string; spin?: boolean;
}> = {
  running: {
    label: '运行中', icon: Loader2, spin: true,
    bar: 'bg-running-strip animate-shimmer',
    pill: 'bg-live-soft text-live-ink ring-1 ring-[var(--color-live-line)]'
  },
  done: {
    label: '完成', icon: CheckCircle2,
    bar: 'bg-emerald-500',
    pill: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200'
  },
  error: {
    label: '失败', icon: AlertCircle,
    bar: 'bg-red-500',
    pill: 'bg-red-50 text-red-600 ring-1 ring-red-200'
  },
  pending: {
    label: '待运行', icon: Circle,
    bar: 'bg-slate-200',
    pill: 'bg-slate-100 text-slate-500 ring-1 ring-slate-200/70'
  },
  skipped: {
    label: '跳过', icon: Ban,
    bar: 'bg-slate-200',
    pill: 'bg-slate-100 text-slate-500 ring-1 ring-slate-200/70'
  },
};

function NodeStatusPill({ status }: { status: NodeStatus }): ReactElement {
  const s = NODE_STATUS[status];
  const Icon = s.icon;
  return (
    <span
      aria-atomic="true"
      aria-label={`节点状态：${s.label}`}
      className={cn(
        'inline-flex shrink-0 items-center justify-center gap-1 rounded-full px-2 py-1 text-[10px] font-medium leading-none',
        s.pill,
      )}
      role="status"
      title={s.label}
    >
      <Icon className={cn('h-3 w-3', s.spin === true && 'animate-spin')} strokeWidth={2} />
      <span>{s.label}</span>
    </span>
  );
}

export function RpaStepNode({ data, selected }: NodeProps<Node<RpaNodeData>>): ReactElement {
  const style = kindStyles[data.kind];
  const Icon = style.icon;

  return (
    <div className="group relative w-60">
      <Handle className="rpa-handle" position={Position.Top} type="target" />
      <div
        className={cn(
          'rpa-node relative overflow-hidden rounded-[14px] border bg-white shadow-sm transition-[box-shadow,border-color] duration-200 ease-out',
          'hover:border-slate-300 hover:shadow-md',
          data.status === 'done' && 'border-emerald-200/80',
          data.status === 'running' && 'border-live-line running-glow shadow-running',
          data.status === 'pending' && 'border-slate-200/80',
          data.status === 'error' && 'border-red-200/80',
          data.status === 'skipped' && 'border-slate-100 bg-slate-50/60 opacity-60',
          data.disabled && 'opacity-40 grayscale',
        )}
        style={selected ? {
          borderColor: style.accent,
          boxShadow: `0 0 0 3px ${style.accent}38, 0 4px 20px rgba(15,23,42,0.12)`,
        } : undefined}
      >
        <div aria-hidden="true" className={cn('h-1 w-full', NODE_STATUS[data.status].bar)} />

        {data.breakpoint && (
          <span
            className="absolute left-2.5 top-2.5 z-(--z-sticky) grid h-2.5 w-2.5 place-items-center rounded-full bg-red-500 ring-2 ring-white"
            title="断点已启用"
          >
            <Dot className="h-2.5 w-2.5 text-white" strokeWidth={3} />
          </span>
        )}

        {data.validationSeverity !== undefined && data.validationCount !== undefined && (
          <span
            className={cn(
              'absolute right-2.5 top-2.5 z-(--z-sticky) inline-flex min-w-4.5 items-center justify-center rounded-full border px-1 py-0.5 text-[9px] font-bold leading-none ring-2 ring-white',
              data.validationSeverity === 'error'
                ? 'border-amber-200 bg-amber-50 text-amber-700'
                : 'border-slate-200 bg-slate-50 text-slate-500',
            )}
            aria-label={data.validationSeverity === 'error' ? `${data.validationCount} 个运行错误` : `${data.validationCount} 个运行提醒`}
            title={data.validationSeverity === 'error' ? '当前节点存在运行错误' : '当前节点存在运行提醒'}
          >
            {data.validationCount}
          </span>
        )}

        <div className="flex items-center gap-3 px-3 pb-3 pt-3.5">
          <div
            className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border transition-transform duration-150 group-hover:scale-105"
            style={{ background: style.bg, borderColor: `${style.accent}22`, color: style.accent }}
          >
            <Icon className="h-4.5 w-4.5" strokeWidth={1.5} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-1.5">
              <span className="truncate text-[12px] font-semibold text-slate-800">{data.title}</span>
              {data.badge !== undefined && (
                <span
                  className="shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-semibold leading-none"
                  style={{ background: style.pill, color: style.text }}
                >
                  {data.badge}
                </span>
              )}
            </div>
            <div className="mt-1 truncate font-mono text-[10px] leading-none text-slate-500">
              {data.description}
            </div>
          </div>
          <NodeStatusPill status={data.status} />
        </div>
      </div>
      <Handle className="rpa-handle" position={Position.Bottom} type="source" />

      {selected && (
        <div
          className="absolute left-1/2 top-full z-(--z-raised) mt-2.5 -translate-x-1/2"
          onPointerDown={(e) => e.stopPropagation()}
        >
          <div className="flex items-center gap-0.5 rounded-xl border border-slate-200/60 bg-white/95 px-1.5 py-1 shadow-[0_8px_28px_rgba(15,23,42,0.12)] backdrop-blur-md">
            <NodeAction action="edit" className="text-accent-strong hover:bg-accent-soft" icon={Pencil} label="编辑" onAction={data.onAction} />
            <NodeAction action="duplicate" className="text-slate-500 hover:bg-slate-100" icon={Copy} label="复制" onAction={data.onAction} />
            <NodeAction
              action="disable"
              className="text-slate-500 hover:bg-slate-100"
              icon={data.disabled ? Ban : Circle}
              label={data.disabled ? '启用' : '禁用'}
              onAction={data.onAction}
            />
            <span className="mx-0.5 h-4 w-px bg-slate-200" />
            <NodeAction action="delete" className="text-red-500 hover:bg-red-50" icon={Trash2} label="删除" onAction={data.onAction} />
          </div>
        </div>
      )}
    </div>
  );
}

export function StartEndNode({ data, selected }: NodeProps<Node<RpaNodeData>>): ReactElement {
  const isStart = data.title === '开始';

  return (
    <div
      className={cn(
        'relative grid h-8 w-28 place-items-center rounded-full border text-[11px] font-semibold shadow-sm transition-[border-color,background-color,color,box-shadow,filter,transform] duration-200',
        isStart
          ? 'border-emerald-500/30 bg-linear-to-b from-emerald-500 to-green-600 text-white shadow-[0_4px_14px_rgba(16,185,129,0.22)] hover:brightness-105'
          : 'border-slate-200/80 bg-slate-50 text-slate-500 hover:bg-slate-100 hover:text-slate-700',
        selected && 'ring-2 ring-accent-soft ring-offset-1 scale-105',
      )}
    >
      {!isStart && <Handle className="rpa-handle" position={Position.Top} type="target" />}
      {data.title}
      {isStart && <Handle className="rpa-handle" position={Position.Bottom} type="source" />}
    </div>
  );
}

function NodeAction({
  action, className, icon: Icon, label, onAction,
}: {
  action: ContextMenuAction;
  className: string;
  icon: LucideIcon;
  label: string;
  onAction?: (action: ContextMenuAction) => void;
}): ReactElement {
  const handleClick = (event: MouseEvent<HTMLButtonElement>): void => {
    event.stopPropagation();
    onAction?.(action);
  };

  return (
    <button
      className={cn(
        'flex h-7 w-7 items-center justify-center rounded-lg transition-[background-color,color,transform] duration-150 active:scale-90',
        className,
      )}
      aria-label={label}
      onClick={handleClick}
      title={label}
      type="button"
    >
      <Icon className="h-3.5 w-3.5" strokeWidth={1.5} />
    </button>
  );
}
