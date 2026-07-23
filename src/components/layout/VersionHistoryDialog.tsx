import { GitCompareArrows, History, RotateCcw, ScrollText } from 'lucide-react';
import type { ReactElement } from 'react';
import { useMemo, useState } from 'react';

import { cn } from '../../lib/utils';
import type { FlowSnapshot } from '../../types/electron';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../ui/dialog';
import { VersionDiffView } from './VersionDiffDialog';

export function VersionHistoryDialog({
  currentFlow,
  onOpenChange,
  onRestoreSnapshot,
  open
}: {
  currentFlow: FlowSnapshot | null;
  onOpenChange: (open: boolean) => void;
  onRestoreSnapshot: (savedAt: string) => Promise<void>;
  open: boolean;
}): ReactElement {
  // 用 savedAt 而非 version 做 key：同一流程的历史快照常共享同一个 version 字符串
  const [diffSnapshotKey, setDiffSnapshotKey] = useState<string | null>(null);

  const snapshots = useMemo(
    () => [...(currentFlow?.snapshots ?? [])].sort((left, right) => new Date(right.savedAt).getTime() - new Date(left.savedAt).getTime()),
    [currentFlow]
  );

  const diffSnapshot = snapshots.find((s) => s.savedAt === diffSnapshotKey) ?? null;

  const activeFlowName = currentFlow?.name ?? '未命名流程';
  const snapshotCount = snapshots.length;

  return (
    <Dialog
      onOpenChange={(nextOpen) => {
        // 差异视图内按 ESC/点遮罩先退回列表，而不是直接关闭整个弹框
        if (!nextOpen && diffSnapshotKey !== null) {
          setDiffSnapshotKey(null);
          return;
        }
        onOpenChange(nextOpen);
      }}
      open={open}
    >
      <DialogContent className={diffSnapshotKey !== null ? 'w-180' : 'w-160'}>
        {diffSnapshotKey !== null ? (
          <VersionDiffView baseFlow={currentFlow} onBack={() => setDiffSnapshotKey(null)} onRollback={onRestoreSnapshot} targetSnapshot={diffSnapshot} />
        ) : (
          <>
            <DialogHeader>
              <DialogTitle className="inline-flex items-center gap-2">
                <History className="h-4 w-4 text-blue-600" strokeWidth={1.5} />
                版本历史
              </DialogTitle>
              <DialogDescription>
                {activeFlowName} · 共 {snapshotCount} 个历史快照
              </DialogDescription>
            </DialogHeader>

            <DialogBody>
            {currentFlow === null ? (
              <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center">
                <ScrollText className="mx-auto h-7 w-7 text-slate-300" strokeWidth={1.5} />
                <div className="mt-2 text-[12px] font-semibold text-slate-600">尚未打开流程</div>
                <div className="mt-1 text-[11px] text-slate-500">打开一个已保存的流程后可查看版本历史。</div>
              </div>
            ) : (
              <div className="grid gap-3">
                <div className="grid grid-cols-3 gap-2">
                  <HistoryMetric label="最新版本" value={currentFlow.version} />
                  <HistoryMetric label="历史快照" value={String(snapshotCount)} />
                  <HistoryMetric label="当前状态" value={getFlowStatusLabel(currentFlow.status)} />
                </div>
                {snapshotCount === 0 ? (
                  <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center">
                    <ScrollText className="mx-auto h-7 w-7 text-slate-300" strokeWidth={1.5} />
                    <div className="mt-2 text-[12px] font-semibold text-slate-600">暂无版本快照</div>
                    <div className="mt-1 text-[11px] text-slate-500">每次保存流程时，旧版本会自动存入历史记录。</div>
                  </div>
                ) : (
                  <div className="max-h-90 overflow-auto rounded-xl border border-slate-200 bg-slate-50 p-2">
                    <div className="relative grid grid-cols-[20px_1fr_auto] gap-2 rounded-lg bg-white p-2.5 shadow-xs">
                      <div className="relative flex justify-center">
                        <span className={cn('mt-1 h-2.5 w-2.5 rounded-full ring-4', 'bg-blue-500 ring-blue-100')} />
                        {snapshotCount > 0 && <span className="absolute top-5 h-[calc(100%+10px)] w-px bg-slate-200" />}
                      </div>
                      <div className="min-w-0">
                        <div className="flex min-w-0 items-center gap-2">
                          <Badge variant="blue">当前</Badge>
                          <Badge className="font-mono" variant="default">{currentFlow.version}</Badge>
                        </div>
                        <div className="mt-1 flex min-w-0 items-center gap-2 text-[10px] text-slate-500">
                          <span className="font-mono" title={formatHistoryTime(currentFlow.updatedAt)}>{formatRelativeTime(currentFlow.updatedAt)}</span>
                          <span>·</span>
                          <span className="truncate">{currentFlow.description ?? '当前活跃版本'}</span>
                        </div>
                        <div className="mt-2 flex items-center gap-2 text-[10px] text-slate-500">
                          <span className="rounded bg-slate-50 px-1.5 py-1">节点 {countDefinitionItems(currentFlow.definition, 'nodes')}</span>
                          <span className="rounded bg-slate-50 px-1.5 py-1">连线 {countDefinitionItems(currentFlow.definition, 'edges')}</span>
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        <Button disabled variant="outline">
                          <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
                          当前版本
                        </Button>
                      </div>
                    </div>
                    {snapshots.map((snapshot, index) => (
                      <div className="relative grid grid-cols-[20px_1fr_auto] gap-2 rounded-lg bg-white p-2.5 shadow-xs" key={snapshot.savedAt}>
                        <div className="relative flex justify-center">
                          <span className={cn('mt-1 h-2.5 w-2.5 rounded-full ring-4', 'bg-slate-300 ring-slate-100')} />
                          {index < snapshots.length - 1 && <span className="absolute top-5 h-[calc(100%+10px)] w-px bg-slate-200" />}
                        </div>
                        <div className="min-w-0">
                          <div className="flex min-w-0 items-center gap-2">
                            <Badge variant="default">历史</Badge>
                            <Badge className="font-mono" variant="default">{snapshot.version}</Badge>
                          </div>
                          <div className="mt-1 flex min-w-0 items-center gap-2 text-[10px] text-slate-500">
                            <span className="font-mono" title={formatHistoryTime(snapshot.savedAt)}>{formatRelativeTime(snapshot.savedAt)}</span>
                            <span>·</span>
                            <span className="truncate">{snapshot.description ?? '历史版本快照'}</span>
                          </div>
                          <div className="mt-2 flex items-center gap-2 text-[10px] text-slate-500">
                            <span className="rounded bg-slate-50 px-1.5 py-1">节点 {countDefinitionItems(snapshot.definition, 'nodes')}</span>
                            <span className="rounded bg-slate-50 px-1.5 py-1">连线 {countDefinitionItems(snapshot.definition, 'edges')}</span>
                          </div>
                        </div>
                        <div className="flex items-center gap-1">
                          <Button onClick={() => void onRestoreSnapshot(snapshot.savedAt).then(() => onOpenChange(false))} variant="outline">
                            <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
                            打开此版本
                          </Button>
                          <Button onClick={() => setDiffSnapshotKey(snapshot.savedAt)} variant="ghost">
                            <GitCompareArrows className="h-3.5 w-3.5" strokeWidth={1.5} />
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </DialogBody>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function HistoryMetric({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2">
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className="mt-1 truncate text-[12px] font-semibold text-slate-700">{value}</div>
    </div>
  );
}

function getFlowStatusLabel(status: FlowSnapshot['status']): string {
  const labels: Record<FlowSnapshot['status'], string> = {
    active: '启用',
    paused: '已暂停',
    disabled: '已禁用',
    archived: '归档',
    draft: '草稿',
  };
  return labels[status] ?? '草稿';
}

function formatHistoryTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

const RELATIVE_TIME_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 60 * 60],
  ['month', 30 * 24 * 60 * 60],
  ['day', 24 * 60 * 60],
  ['hour', 60 * 60],
  ['minute', 60],
];

const relativeTimeFormatter = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' });

/** 折叠态摘要用相对时间（"3 小时前"）更容易一眼扫读；精确时间放在 title 里 hover 查看。*/
function formatRelativeTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const diffSeconds = (date.getTime() - Date.now()) / 1000;
  if (Math.abs(diffSeconds) < 30) {
    return '刚刚';
  }
  for (const [unit, secondsInUnit] of RELATIVE_TIME_UNITS) {
    if (Math.abs(diffSeconds) >= secondsInUnit) {
      return relativeTimeFormatter.format(Math.round(diffSeconds / secondsInUnit), unit);
    }
  }
  return relativeTimeFormatter.format(Math.round(diffSeconds), 'second');
}

function countDefinitionItems(definition: Record<string, unknown>, key: 'edges' | 'nodes'): number {
  return Array.isArray(definition[key]) ? definition[key].length : 0;
}
