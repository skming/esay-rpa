import { CircleAlert, GitCompareArrows, History, Loader2, RefreshCw, RotateCcw, ScrollText } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useMemo, useState } from 'react';

import type { FlowSnapshot, FlowVersionSnapshot } from '../../types/electron';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '../ui/alert-dialog';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../ui/dialog';
import { VersionDiffView } from './VersionDiffDialog';

export function VersionHistoryDialog({
  currentFlow,
  onLoadSnapshots,
  onOpenChange,
  onRestoreSnapshot,
  open,
}: {
  currentFlow: FlowSnapshot | null;
  onLoadSnapshots: () => Promise<FlowVersionSnapshot[]>;
  onOpenChange: (open: boolean) => void;
  onRestoreSnapshot: (snapshot: FlowVersionSnapshot) => Promise<boolean>;
  open: boolean;
}): ReactElement {
  const [diffSnapshotKey, setDiffSnapshotKey] = useState<string | null>(null);
  const [restoreTarget, setRestoreTarget] = useState<FlowVersionSnapshot | null>(null);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [historyResult, setHistoryResult] = useState<{
    error: string | null;
    key: string | null;
    snapshots: FlowVersionSnapshot[];
  }>({ error: null, key: null, snapshots: [] });
  const [restoring, setRestoring] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const flowId = currentFlow?.flowId ?? null;
  const requestKey = flowId === null ? null : `${flowId}:${reloadKey}`;

  useEffect(() => {
    if (!open || requestKey === null) return;
    let cancelled = false;
    void onLoadSnapshots()
      .then((items) => {
        if (!cancelled) setHistoryResult({ error: null, key: requestKey, snapshots: items });
      })
      .catch((error: unknown) => {
        if (!cancelled) setHistoryResult({
          error: error instanceof Error ? error.message : '版本历史加载失败',
          key: requestKey,
          snapshots: [],
        });
      });
    return () => { cancelled = true; };
  }, [onLoadSnapshots, open, requestKey]);

  const loading = open && requestKey !== null && historyResult.key !== requestKey;
  const loadError = historyResult.key === requestKey ? historyResult.error : null;

  const sortedSnapshots = useMemo(
    () => [...(historyResult.key === requestKey ? historyResult.snapshots : [])]
      .sort((left, right) => new Date(right.savedAt).getTime() - new Date(left.savedAt).getTime()),
    [historyResult, requestKey],
  );
  const diffSnapshot = sortedSnapshots.find((snapshot) => snapshot.savedAt === diffSnapshotKey) ?? null;
  const requestRestore = (snapshot: FlowVersionSnapshot): void => {
    setRestoreError(null);
    setRestoreTarget(snapshot);
  };

  const confirmRestore = async (): Promise<void> => {
    if (restoreTarget === null || restoring) return;
    setRestoring(true);
    try {
      const restored = await onRestoreSnapshot(restoreTarget);
      if (restored) {
        setRestoreTarget(null);
        onOpenChange(false);
      } else {
        setRestoreError('恢复失败，当前版本未发生变化。');
      }
    } catch (error) {
      setRestoreError(error instanceof Error ? error.message : '恢复失败，当前版本未发生变化。');
    } finally {
      setRestoring(false);
    }
  };

  return (
    <>
      <Dialog
        onOpenChange={(nextOpen) => {
          if (!nextOpen && diffSnapshotKey !== null) {
            setDiffSnapshotKey(null);
            return;
          }
          onOpenChange(nextOpen);
        }}
        open={open}
      >
        <DialogContent className={diffSnapshotKey !== null ? 'w-180' : 'w-150'}>
          {diffSnapshotKey !== null ? (
            <VersionDiffView
              baseFlow={currentFlow}
              onBack={() => setDiffSnapshotKey(null)}
              onRequestRollback={requestRestore}
              targetSnapshot={diffSnapshot}
            />
          ) : (
            <>
              <DialogHeader>
                <DialogTitle className="inline-flex items-center gap-2">
                  <History className="h-4 w-4 text-blue-600" strokeWidth={1.5} />
                  版本历史
                </DialogTitle>
                <DialogDescription>
                  {currentFlow === null
                    ? '打开已保存的流程后查看历史版本'
                    : `${currentFlow.name} · 当前 revision ${currentFlow.revision ?? 1} · ${sortedSnapshots.length} 个历史版本`}
                </DialogDescription>
              </DialogHeader>

              <DialogBody>
                {currentFlow === null ? (
                  <HistoryEmpty title="尚未打开流程" description="打开一个已保存的流程后可查看版本历史。" />
                ) : loading ? (
                  <div className="flex items-center justify-center gap-2 py-12 text-[12px] text-slate-500">
                    <Loader2 className="h-4 w-4 animate-spin text-accent" strokeWidth={1.8} />
                    正在加载版本历史
                  </div>
                ) : loadError !== null ? (
                  <div className="flex flex-col items-center gap-2 rounded-xl border border-red-100 bg-red-50 px-4 py-8 text-center">
                    <CircleAlert className="h-5 w-5 text-red-500" strokeWidth={1.7} />
                    <p className="text-[12px] text-red-700">{loadError}</p>
                    <Button onClick={() => setReloadKey((value) => value + 1)} variant="outline">
                      <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.5} />
                      重新加载
                    </Button>
                  </div>
                ) : (
                  <div className="max-h-105 overflow-auto rounded-xl border border-slate-200 bg-white">
                    <CurrentVersionRow flow={currentFlow} />
                    {sortedSnapshots.length === 0 ? (
                      <HistoryEmpty title="暂无历史版本" description="修改流程定义、变量或验收契约后，旧版本会自动保存在这里。" compact />
                    ) : sortedSnapshots.map((snapshot) => (
                      <SnapshotRow
                        key={snapshot.savedAt}
                        onCompare={() => setDiffSnapshotKey(snapshot.savedAt)}
                        onRestore={() => requestRestore(snapshot)}
                        snapshot={snapshot}
                      />
                    ))}
                  </div>
                )}
              </DialogBody>
            </>
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog onOpenChange={(nextOpen) => { if (!nextOpen && !restoring) setRestoreTarget(null); }} open={restoreTarget !== null}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>恢复 revision {restoreTarget?.revision ?? restoreTarget?.version}？</AlertDialogTitle>
            <AlertDialogDescription>
              将恢复该版本的流程定义、输入变量和验收契约，并保存为一个新的 revision；当前版本仍会保留在历史中。
            </AlertDialogDescription>
            {restoreError !== null && <p className="text-[11px] text-red-600">{restoreError}</p>}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={restoring}>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-blue-600 hover:bg-blue-700 active:bg-blue-800"
              disabled={restoring}
              onClick={(event) => {
                event.preventDefault();
                void confirmRestore();
              }}
            >
              {restoring && <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.8} />}
              恢复为新版本
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function CurrentVersionRow({ flow }: { flow: FlowSnapshot }): ReactElement {
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-3 border-b border-slate-100 bg-blue-50/45 px-3 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Badge variant="blue">当前</Badge>
          <span className="font-mono text-[11px] font-semibold text-slate-700">r{flow.revision ?? 1}</span>
          <span className="text-[10px] text-slate-500">{flow.version}</span>
        </div>
        <VersionMeta definition={flow.definition} savedAt={flow.updatedAt} />
      </div>
      <span className="text-[10px] text-slate-500">正在编辑</span>
    </div>
  );
}

function SnapshotRow({ onCompare, onRestore, snapshot }: {
  onCompare: () => void;
  onRestore: () => void;
  snapshot: FlowVersionSnapshot;
}): ReactElement {
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-3 border-b border-slate-100 px-3 py-3 last:border-b-0 hover:bg-slate-50/70">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] font-semibold text-slate-700">r{snapshot.revision ?? '—'}</span>
          <span className="text-[10px] text-slate-500">{snapshot.version}</span>
          {snapshot.description && <span className="truncate text-[10px] text-slate-500">· {snapshot.description}</span>}
        </div>
        <VersionMeta definition={snapshot.definition} savedAt={snapshot.savedAt} />
      </div>
      <div className="flex items-center gap-1">
        <Button onClick={onCompare} variant="ghost">
          <GitCompareArrows className="h-3.5 w-3.5" strokeWidth={1.5} />
          差异
        </Button>
        <Button onClick={onRestore} variant="outline">
          <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
          恢复
        </Button>
      </div>
    </div>
  );
}

function VersionMeta({ definition, savedAt }: { definition: Record<string, unknown>; savedAt: string }): ReactElement {
  return (
    <div className="mt-1.5 flex items-center gap-2 text-[10px] text-slate-500">
      <time dateTime={savedAt} title={formatHistoryTime(savedAt)}>{formatRelativeTime(savedAt)}</time>
      <span className="text-slate-300">·</span>
      <span>{countDefinitionItems(definition, 'nodes')} 个节点</span>
      <span>{countDefinitionItems(definition, 'edges')} 条连线</span>
    </div>
  );
}

function HistoryEmpty({ compact = false, description, title }: { compact?: boolean; description: string; title: string }): ReactElement {
  return (
    <div className={compact ? 'px-4 py-8 text-center' : 'rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center'}>
      <ScrollText className="mx-auto h-6 w-6 text-slate-300" strokeWidth={1.5} />
      <div className="mt-2 text-[12px] font-semibold text-slate-600">{title}</div>
      <div className="mt-1 text-[11px] text-slate-500">{description}</div>
    </div>
  );
}

function formatHistoryTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

const RELATIVE_TIME_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 60 * 60], ['month', 30 * 24 * 60 * 60], ['day', 24 * 60 * 60], ['hour', 60 * 60], ['minute', 60],
];
const relativeTimeFormatter = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' });

function formatRelativeTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diffSeconds = (date.getTime() - Date.now()) / 1000;
  if (Math.abs(diffSeconds) < 30) return '刚刚';
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
