import { ArrowLeft, ArrowRight, GitCompareArrows, Minus, MoveRight, Pencil, Plus, RotateCcw } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ReactElement } from 'react';
import { useMemo, useState } from 'react';

import { diffFlowSnapshots, type FlowDiffField, type FlowDiffItem, type FlowDiffSummary, type FlowDiffType } from '../../lib/flowDiff';
import { cn } from '../../lib/utils';
import type { FlowSnapshot, FlowVersionSnapshot } from '../../types/electron';
import { Badge } from '../ui/badge';
import { Button, IconButton } from '../ui/button';
import { DialogBody, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';

type DiffFilter = FlowDiffType | 'all';

export function VersionDiffView({
  baseFlow,
  onBack,
  onRollback,
  targetSnapshot
}: {
  baseFlow: FlowSnapshot | null;
  onBack: () => void;
  /** 按快照的 savedAt（唯一）标识回退目标，而不是 version（多条快照常共享同一个 version 字符串）。*/
  onRollback: (savedAt: string) => Promise<void>;
  targetSnapshot: FlowVersionSnapshot | null;
}): ReactElement {
  const [filter, setFilter] = useState<DiffFilter>('all');

  // 历史快照在前、当前流程在后：读作「自这个版本以来改了什么」，与「回退」撤销的内容一致
  const diff = useMemo(() => (baseFlow !== null && targetSnapshot !== null ? diffFlowSnapshots(targetSnapshot, baseFlow) : null), [baseFlow, targetSnapshot]);

  const visibleItems = diff === null ? [] : diff.items.filter((item) => filter === 'all' || item.type === filter);

  return (
    <>
      <DialogHeader>
        <div className="grid gap-1">
          <DialogTitle className="inline-flex items-center gap-2">
            <IconButton className="-ml-1.5" label="返回版本历史" onClick={onBack}>
              <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.5} />
            </IconButton>
            <GitCompareArrows className="h-4 w-4 text-blue-600" strokeWidth={1.5} />
            版本差异
          </DialogTitle>
          <DialogDescription>
            {baseFlow !== null && targetSnapshot !== null ? (
              <span className="inline-flex items-center gap-1.5">
                自
                <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600">{targetSnapshot.version}</span>
                <MoveRight className="h-3 w-3 text-slate-400" strokeWidth={1.5} />
                <span className="rounded bg-blue-50 px-1.5 py-0.5 font-mono text-[10px] text-blue-700">{baseFlow.version}</span>
                以来的改动
              </span>
            ) : (
              '选择历史版本后查看流程定义差异'
            )}
          </DialogDescription>
        </div>
      </DialogHeader>

      <DialogBody>
        {baseFlow === null || targetSnapshot === null || diff === null ? (
          <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-[12px] text-slate-500">暂无可对比的版本。</div>
        ) : (
          <div className="grid gap-3">
            <VersionDiffMetrics diff={diff} filter={filter} onFilterChange={setFilter} />
            <div className="max-h-90 overflow-auto rounded-xl border border-slate-200 bg-slate-50 p-2">
              {diff.items.length === 0 ? (
                <div className="rounded-lg bg-white px-4 py-8 text-center text-[12px] text-slate-500 shadow-xs">两个版本的节点与连线定义一致。</div>
              ) : visibleItems.length === 0 ? (
                <div className="rounded-lg bg-white px-4 py-8 text-center text-[12px] text-slate-500 shadow-xs">当前筛选下没有条目。</div>
              ) : (
                <div className="grid gap-2">
                  {visibleItems.map((item) => (
                    <VersionDiffRow item={item} key={item.id} />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </DialogBody>

      <DialogFooter>
        <Button onClick={onBack} variant="outline">
          关闭
        </Button>
        <Button
          disabled={baseFlow === null || targetSnapshot === null}
          onClick={() => {
            if (targetSnapshot !== null) {
              void onRollback(targetSnapshot.savedAt).then(onBack);
            }
          }}
          variant="primary"
        >
          <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
          回退为新版本
        </Button>
      </DialogFooter>
    </>
  );
}

function VersionDiffMetrics({ diff, filter, onFilterChange }: { diff: FlowDiffSummary; filter: DiffFilter; onFilterChange: (filter: DiffFilter) => void }): ReactElement {
  const added = diff.nodeAdded + diff.edgeAdded;
  const changed = diff.nodeChanged + diff.edgeChanged;
  const removed = diff.nodeRemoved + diff.edgeRemoved;

  return (
    <div className="grid gap-2">
      <div className="grid grid-cols-4 gap-2">
        <DiffFilterTab active={filter === 'all'} count={added + changed + removed} label="全部" onClick={() => onFilterChange('all')} type={null} />
        <DiffFilterTab active={filter === 'added'} count={added} label="新增" onClick={() => onFilterChange('added')} type="added" />
        <DiffFilterTab active={filter === 'changed'} count={changed} label="变更" onClick={() => onFilterChange('changed')} type="changed" />
        <DiffFilterTab active={filter === 'removed'} count={removed} label="移除" onClick={() => onFilterChange('removed')} type="removed" />
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-0.5 text-[10px] text-slate-500">
        <span>
          节点 <DiffCount type="added" value={diff.nodeAdded} /> <DiffCount type="changed" value={diff.nodeChanged} /> <DiffCount type="removed" value={diff.nodeRemoved} />
        </span>
        <span className="text-slate-300">|</span>
        <span>
          连线 <DiffCount type="added" value={diff.edgeAdded} /> <DiffCount type="changed" value={diff.edgeChanged} /> <DiffCount type="removed" value={diff.edgeRemoved} />
        </span>
        {diff.layoutOnly > 0 && (
          <>
            <span className="text-slate-300">|</span>
            {/* 位置不计入变更，但用户在画布上确实看到节点挪了，不说明会以为漏报 */}
            <span>{diff.layoutOnly} 个节点仅调整了画布位置</span>
          </>
        )}
      </div>
    </div>
  );
}

function DiffCount({ type, value }: { type: FlowDiffType; value: number }): ReactElement {
  return (
    <span className={cn('font-mono', value > 0 ? DIFF_TEXT_CLASS[type] : 'text-slate-500')}>
      {DIFF_SIGN[type]}
      {value}
    </span>
  );
}

function DiffFilterTab({ active, count, label, onClick, type }: { active: boolean; count: number; label: string; onClick: () => void; type: FlowDiffType | null }): ReactElement {
  return (
    <button
      className={cn(
        'rounded-lg border px-3 py-2 text-left transition-colors',
        active ? 'border-blue-300 bg-blue-50' : 'border-slate-200 bg-white hover:border-slate-300'
      )}
      onClick={onClick}
      type="button"
    >
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className={cn('mt-1 font-mono text-[13px] font-semibold', count > 0 && type !== null ? DIFF_TEXT_CLASS[type] : 'text-slate-700')}>{count}</div>
    </button>
  );
}

function VersionDiffRow({ item }: { item: FlowDiffItem }): ReactElement {
  const Icon = DIFF_ICON[item.type];
  return (
    <div className="grid grid-cols-[20px_1fr] items-start gap-2 rounded-lg bg-white p-2.5 shadow-xs">
      <Icon className={cn('mt-0.5 h-4 w-4', DIFF_TEXT_CLASS[item.type])} strokeWidth={1.5} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant={DIFF_BADGE_VARIANT[item.type]}>
            {item.scope === 'node' ? '节点' : '连线'}
            {DIFF_TYPE_LABEL[item.type]}
          </Badge>
          <span className="min-w-0 break-all text-[12px] font-semibold text-slate-700">{item.title}</span>
          <span className="font-mono text-[10px] text-slate-500">{item.entityId}</span>
        </div>
        {item.subtitle !== undefined && <div className="mt-0.5 font-mono text-[10px] text-slate-500">{item.subtitle}</div>}
        {item.fields.length > 0 && (
          <div className="mt-1.5 grid gap-1">
            {item.fields.map((field) => (
              <DiffFieldRow field={field} key={field.key} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function DiffFieldRow({ field }: { field: FlowDiffField }): ReactElement {
  // 单行值左右并排能一眼看出改了哪一段；多行值（脚本）并排会挤成两条窄柱，改为上下堆叠
  const stacked = field.multiline;
  return (
    <div className="rounded-md bg-slate-50 px-2 py-1.5">
      <div className="text-[10px] font-semibold text-slate-500">{field.label}</div>
      <div className={cn('mt-1 gap-1.5', stacked ? 'grid' : 'flex flex-wrap items-center')}>
        {field.before !== undefined && <DiffValue stacked={stacked} text={field.before} tone="before" />}
        {field.before !== undefined && field.after !== undefined && !stacked && <ArrowRight className="h-3 w-3 shrink-0 text-slate-500" strokeWidth={1.5} />}
        {field.after !== undefined && <DiffValue stacked={stacked} text={field.after} tone="after" />}
        {field.before === undefined && field.after !== undefined && stacked === false && <span className="text-[10px] text-slate-600">（新增字段）</span>}
        {field.after === undefined && field.before !== undefined && stacked === false && <span className="text-[10px] text-slate-600">（该字段已删除）</span>}
      </div>
    </div>
  );
}

function DiffValue({ stacked, text, tone }: { stacked: boolean; text: string; tone: 'after' | 'before' }): ReactElement {
  return (
    <span
      className={cn(
        'min-w-0 rounded px-1.5 py-1 font-mono text-[10px] break-all',
        tone === 'before' ? 'bg-red-50 text-red-700' : 'bg-emerald-50 text-emerald-700',
        stacked && 'max-h-32 overflow-auto whitespace-pre-wrap'
      )}
    >
      {/* 改前/改后此前只靠红绿两种底色区分，色觉障碍下这条 diff 读不出方向；
          堆叠模式连中间那支箭头都没有，只剩上下顺序。−/+ 是 diff 的通用记号，
          读屏播报的是后面那条中文——符号在语音里念出来分不清是运算还是标记。 */}
      <span aria-hidden="true" className="mr-1 font-semibold">{tone === 'before' ? '−' : '+'}</span>
      <span className="sr-only">{tone === 'before' ? '改前 ' : '改后 '}</span>
      {text}
    </span>
  );
}

// 用常量表而非函数返回组件：后者会让 React Compiler 无法确认组件身份稳定
const DIFF_ICON: Record<FlowDiffType, LucideIcon> = {
  added: Plus,
  changed: Pencil,
  removed: Minus
};

const DIFF_TEXT_CLASS: Record<FlowDiffType, string> = {
  added: 'text-emerald-700',
  changed: 'text-blue-600',
  removed: 'text-red-600'
};

const DIFF_BADGE_VARIANT: Record<FlowDiffType, 'blue' | 'emerald' | 'red'> = {
  added: 'emerald',
  changed: 'blue',
  removed: 'red'
};

const DIFF_TYPE_LABEL: Record<FlowDiffType, string> = {
  added: '新增',
  changed: '变更',
  removed: '移除'
};

const DIFF_SIGN: Record<FlowDiffType, string> = {
  added: '+',
  changed: '~',
  removed: '-'
};
