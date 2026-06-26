import { GitCompareArrows, Plus, RotateCcw, Trash2 } from 'lucide-react';
import type { ReactElement } from 'react';
import { useMemo } from 'react';

import { diffFlowSnapshots, type FlowDiffItem, type FlowDiffSummary, type FlowDiffType } from '../../lib/flowDiff';
import type { FlowSnapshot, FlowVersionSnapshot } from '../../types/electron';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';

export function VersionDiffDialog({
  baseFlow,
  onOpenChange,
  onRollback,
  open,
  targetSnapshot
}: {
  baseFlow: FlowSnapshot | null;
  onOpenChange: (open: boolean) => void;
  onRollback: (version: string) => Promise<void>;
  open: boolean;
  targetSnapshot: FlowVersionSnapshot | null;
}): ReactElement {
  const diff = useMemo(() => (baseFlow !== null && targetSnapshot !== null ? diffFlowSnapshots(baseFlow, targetSnapshot) : null), [baseFlow, targetSnapshot]);

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="w-[720px]">
        <DialogHeader>
          <DialogTitle className="inline-flex items-center gap-2">
            <GitCompareArrows className="h-4 w-4 text-blue-600" strokeWidth={1.5} />
            版本差异
          </DialogTitle>
          <DialogDescription>{baseFlow !== null && targetSnapshot !== null ? `${baseFlow.version} 对比 ${targetSnapshot.version}` : '选择历史版本后查看流程定义差异'}</DialogDescription>
        </DialogHeader>

        <DialogBody>
        {baseFlow === null || targetSnapshot === null || diff === null ? (
          <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-[12px] text-slate-500">暂无可对比的版本。</div>
        ) : (
          <div className="grid gap-3">
            <VersionDiffMetrics diff={diff} />
            <div className="max-h-[360px] overflow-auto rounded-xl border border-slate-200 bg-slate-50 p-2">
              {diff.items.length === 0 ? (
                <div className="rounded-lg bg-white px-4 py-8 text-center text-[12px] text-slate-500 shadow-xs">两个版本的节点与连线定义一致。</div>
              ) : (
                <div className="grid gap-2">
                  {diff.items.map((item) => (
                    <VersionDiffRow item={item} key={item.id} />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
        </DialogBody>

        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="outline">
            关闭
          </Button>
          <Button
            disabled={baseFlow === null || targetSnapshot === null}
            onClick={() => {
              if (targetSnapshot !== null) {
                void onRollback(targetSnapshot.version).then(() => onOpenChange(false));
              }
            }}
            variant="primary"
          >
            <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
            回退为新版本
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function VersionDiffMetrics({ diff }: { diff: FlowDiffSummary }): ReactElement {
  return (
    <div className="grid grid-cols-3 gap-2">
      <DiffMetric label="节点新增" value={diff.nodeAdded} variant="emerald" />
      <DiffMetric label="节点变更" value={diff.nodeChanged} variant="blue" />
      <DiffMetric label="节点移除" value={diff.nodeRemoved} variant="red" />
      <DiffMetric label="连线新增" value={diff.edgeAdded} variant="emerald" />
      <DiffMetric label="连线变更" value={diff.edgeChanged} variant="blue" />
      <DiffMetric label="连线移除" value={diff.edgeRemoved} variant="red" />
    </div>
  );
}

function DiffMetric({ label, value, variant }: { label: string; value: number; variant: 'blue' | 'emerald' | 'red' }): ReactElement {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2">
      <div className="text-[10px] text-slate-400">{label}</div>
      <Badge className="mt-1 text-[11px]" variant={value > 0 ? variant : 'default'}>
        {value}
      </Badge>
    </div>
  );
}

function VersionDiffRow({ item }: { item: FlowDiffItem }): ReactElement {
  const Icon = getDiffIcon(item.type);
  return (
    <div className="grid grid-cols-[20px_1fr_auto] gap-2 rounded-lg bg-white p-2.5 shadow-xs">
      <Icon className={getDiffIconClass(item.type)} strokeWidth={1.5} />
      <div className="min-w-0">
        <div className="truncate text-[12px] font-semibold text-slate-700">{item.title}</div>
        <div className="mt-1 grid gap-1 text-[10px] text-slate-500">
          {item.before !== undefined && <span className="truncate rounded bg-red-50 px-1.5 py-1 text-red-700">原：{item.before}</span>}
          {item.after !== undefined && <span className="truncate rounded bg-emerald-50 px-1.5 py-1 text-emerald-700">新：{item.after}</span>}
        </div>
      </div>
      <Badge variant={getDiffBadgeVariant(item.type)}>{getDiffTypeLabel(item.type)}</Badge>
    </div>
  );
}

function getDiffIcon(type: FlowDiffType): typeof Plus {
  if (type === 'removed') {
    return Trash2;
  }
  if (type === 'changed') {
    return GitCompareArrows;
  }
  return Plus;
}

function getDiffIconClass(type: FlowDiffType): string {
  if (type === 'removed') {
    return 'mt-0.5 h-4 w-4 text-red-500';
  }
  if (type === 'changed') {
    return 'mt-0.5 h-4 w-4 text-blue-500';
  }
  return 'mt-0.5 h-4 w-4 text-emerald-500';
}

function getDiffBadgeVariant(type: FlowDiffType): 'blue' | 'emerald' | 'red' {
  if (type === 'removed') {
    return 'red';
  }
  if (type === 'changed') {
    return 'blue';
  }
  return 'emerald';
}

function getDiffTypeLabel(type: FlowDiffType): string {
  if (type === 'removed') {
    return '移除';
  }
  if (type === 'changed') {
    return '变更';
  }
  return '新增';
}
