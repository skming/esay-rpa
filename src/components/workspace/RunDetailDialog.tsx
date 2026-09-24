import { AlertCircle, CheckCircle2, ChevronRight, Clock3, DatabaseZap, FileJson, FolderOpen, Loader2, ScrollText, XCircle } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useState } from 'react';

import { formatElapsedTime } from '../../lib/time';
import { cn } from '../../lib/utils';
import type { ArtifactSnapshot, BackendTaskLogEntry, RunDetail, TaskSnapshot } from '../../types/electron';
import type { RunLogLevel } from '../../types/rpa';
import { getLogTone } from '../studio/bottom-panel/bottomPanelUtils';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { CopyButton } from '../ui/copy-button';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';

// 复用底部面板的日志配色；后端 level 是自由字符串，未知值退到 info 的中性色但标签原样展示，不替它编中文名
const LOG_LEVEL_LABELS: Record<RunLogLevel, string> = {
  error: '错误', warn: '警告', input: '输入', success: '成功', running: '执行', info: '信息',
};
function logLevelTone(level: string): ReturnType<typeof getLogTone> {
  return getLogTone((level in LOG_LEVEL_LABELS ? level : 'info') as RunLogLevel);
}
function logLevelLabel(level: string): string {
  return LOG_LEVEL_LABELS[level as RunLogLevel] ?? level;
}

export function RunDetailDialog({
  onOpenArtifact,
  onOpenChange,
  onLoadDetail,
  open,
  run: listedRun,
}: {
  onOpenArtifact?: (artifact: ArtifactSnapshot) => void;
  onOpenChange: (open: boolean) => void;
  onLoadDetail: (taskId: string) => Promise<RunDetail>;
  open: boolean;
  run: TaskSnapshot | null;
}): ReactElement | null {
  const [detail, setDetail] = useState<{ taskId: string; run: TaskSnapshot; logs: BackendTaskLogEntry[] } | null>(null);
  const [loadError, setLoadError] = useState<{ taskId: string; message: string } | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const taskId = listedRun?.taskId;

  useEffect(() => {
    if (!open || taskId === undefined) return;
    let current = true;
    void onLoadDetail(taskId).then(({ run, logs }) => {
      if (current) {
        setDetail({ taskId, run, logs });
        setLoadError(null);
      }
    }).catch((error: unknown) => {
      if (current) {
        setDetail(null);
        setLoadError({ taskId, message: error instanceof Error ? error.message : String(error) });
      }
    });
    return () => { current = false; };
  }, [open, taskId, reloadKey, onLoadDetail]);

  if (listedRun === null) return null;
  const loaded = detail?.taskId === listedRun.taskId ? detail : null;
  const run = loaded?.run ?? listedRun;
  const logs = loaded?.logs ?? [];
  const errorLogCount = logs.reduce((count, log) => (log.level === 'error' ? count + 1 : count), 0);
  const error = loadError?.taskId === listedRun.taskId ? loadError.message : null;
  const loading = loaded === null && error === null;

  const statusIcon = {
    running: <Loader2 className="h-4 w-4 animate-spin text-blue-500" strokeWidth={1.5} />,
    success: <CheckCircle2 className="h-4 w-4 text-emerald-500" strokeWidth={1.5} />,
    error: <XCircle className="h-4 w-4 text-red-500" strokeWidth={1.5} />,
    stopped: <XCircle className="h-4 w-4 text-amber-500" strokeWidth={1.5} />,
    queued: <Loader2 className="h-4 w-4 text-slate-400" strokeWidth={1.5} />,
    awaiting_confirmation: <Loader2 className="h-4 w-4 text-amber-500" strokeWidth={1.5} />,
  }[run.status];

  const statusLabel = { running: '运行中', success: '成功', error: '失败', stopped: '已停止', queued: '排队', awaiting_confirmation: '等待操作' }[run.status];
  const statusVariant = { success: 'emerald', error: 'red', running: 'blue', queued: 'amber', stopped: 'default', awaiting_confirmation: 'amber' }[run.status] as 'emerald' | 'red' | 'blue' | 'amber' | 'default';
  const handleOpenChange = (nextOpen: boolean): void => {
    if (!nextOpen) {
      setDetail(null);
      setLoadError(null);
    }
    onOpenChange(nextOpen);
  };

  return (
    <Dialog onOpenChange={handleOpenChange} open={open}>
      <DialogContent className="flex max-h-[min(82vh,700px)] w-200 max-w-[calc(100vw-32px)] flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {statusIcon}
            运行详情 · {run.flowName}
          </DialogTitle>
        </DialogHeader>

        <DialogBody className="min-h-0 flex-1 space-y-5 overflow-y-auto">
          {loading && <p className="text-[11px] text-slate-500">正在读取完整执行记录…</p>}
          {error && <p className="rounded-md bg-red-50 px-3 py-2 text-[11px] text-red-700">读取最新记录失败：{error}</p>}
          {run.error !== null && run.error !== undefined && (
            <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2.5">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" strokeWidth={1.5} />
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-semibold text-red-700">执行失败</div>
                <p className="mt-0.5 whitespace-pre-wrap break-words text-[11px] text-red-700">{run.error}</p>
              </div>
              <CopyButton className="shrink-0 text-red-400 hover:text-red-700" text={run.error} title="复制错误" />
            </div>
          )}
          <div className="grid grid-cols-2 gap-2">
            <InfoRow label="任务 ID" value={run.taskId} mono />
            <InfoRow label="状态">
              <Badge variant={statusVariant}>{statusLabel}</Badge>
            </InfoRow>
            <InfoRow label="运行模式" value={run.mode === 'debug' ? '调试' : '正常运行'} />
            <InfoRow label="耗时">
              <span className="inline-flex items-center gap-1 font-mono text-[11px] text-slate-700">
                <Clock3 className="h-3 w-3 text-slate-400" strokeWidth={1.5} />
                {formatElapsedTime(run.progress.elapsedMs)}
              </span>
            </InfoRow>
            <InfoRow label="进度" value={`${run.progress.currentStep} / ${run.progress.totalSteps} 步（${run.progress.percent}%）`} />
            <InfoRow label="更新时间" value={formatDateTime(run.updatedAt)} />
          </div>

          {(run.variables?.length ?? 0) > 0 && (
            <div>
              <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-slate-600">
                <DatabaseZap className="h-3.5 w-3.5 text-blue-500" strokeWidth={1.5} />
                变量快照（{run.variables?.length ?? 0} 个）
              </div>
              <div className="max-h-35 space-y-1 overflow-auto rounded-md border border-slate-200 bg-slate-50 p-2">
                {(run.variables ?? []).map((v) => (
                  <div className="flex items-center justify-between text-[11px]" key={v.name}>
                    <span className="font-mono text-blue-700">{v.name}</span>
                    <span className="ml-2 max-w-70 truncate font-mono text-slate-500">
                      {v.sensitive ? '••••••••' : v.value}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {(run.artifacts?.length ?? 0) > 0 && (
            <div>
              <div className="mb-1.5 flex items-center justify-between gap-2 text-[11px] font-semibold text-slate-600">
                <div className="flex items-center gap-1.5">
                  <FileJson className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.5} />
                  产物（{run.artifacts?.length ?? 0} 个）
                </div>
              </div>
              <div className="max-h-25 space-y-1 overflow-auto rounded-md border border-slate-200 bg-slate-50 p-2">
                {(run.artifacts ?? []).map((a) => (
                  <div className="group flex items-center justify-between gap-2 rounded px-1 py-0.5 text-[11px] transition-colors hover:bg-slate-100" key={a.artifactId}>
                    <span className="min-w-0 flex-1 truncate font-mono text-slate-700">{a.filename}</span>
                    <span className="shrink-0 text-slate-500">{formatBytes(a.sizeBytes)}</span>
                    {onOpenArtifact && <button aria-label={`打开 ${a.filename} 所在位置`} className="rounded p-1 text-slate-500 hover:text-slate-800" onClick={() => onOpenArtifact(a)} type="button"><FolderOpen className="h-3.5 w-3.5" strokeWidth={1.5} /></button>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {loaded && <div>
            <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-slate-600">
              <ScrollText className="h-3.5 w-3.5 text-slate-500" strokeWidth={1.5} />
              执行日志（{logs.length} 条{errorLogCount > 0 ? ` · ${errorLogCount} 错误` : ''}）
            </div>
            {logs.length === 0 ? (
              <p className="rounded-md bg-slate-50 px-3 py-3 text-[11px] text-slate-500">没有日志记录</p>
            ) : (
              <div className="max-h-64 divide-y divide-slate-100 overflow-auto rounded-md border border-slate-200">
                {logs.map((log) => <RunLogRow key={log.id} log={log} />)}
              </div>
            )}
          </div>}
        </DialogBody>

        <DialogFooter>
          <Button onClick={() => { setDetail(null); setLoadError(null); setReloadKey((value) => value + 1); }} variant="outline">刷新记录</Button>
          <Button onClick={() => handleOpenChange(false)} variant="primary">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// 长 detail（多为完整异常栈）默认折叠，避免和顶部错误摘要重复刷屏；点击就地展开看全文，右侧可整条复制。
function RunLogRow({ log }: { log: BackendTaskLogEntry }): ReactElement {
  const [expanded, setExpanded] = useState(false);
  const tone = logLevelTone(log.level);
  const hasDetail = log.detail !== null && log.detail !== undefined && log.detail.length > 0;
  const fullText = hasDetail ? `${log.message}\n${log.detail}` : log.message;
  return (
    <div className={cn('grid grid-cols-[68px_56px_minmax(0,1fr)_auto] items-start gap-2 px-3 py-1.5 text-[11px]', tone.row)}>
      <time className="font-mono tabular-nums text-slate-500">{formatDateTime(log.time).slice(11)}</time>
      <span className={cn('flex items-center gap-1.5 font-medium', tone.text)}>
        <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', tone.dot)} />
        {logLevelLabel(log.level)}
      </span>
      <div className="min-w-0">
        {hasDetail ? (
          <>
            <button
              aria-expanded={expanded}
              aria-label={expanded ? '收起日志详情' : '展开日志详情'}
              className="flex w-full min-w-0 items-start gap-1 text-left text-slate-700"
              onClick={() => setExpanded((value) => !value)}
              type="button"
            >
              <ChevronRight className={cn('mt-0.5 h-3 w-3 shrink-0 text-slate-400 transition-transform', expanded && 'rotate-90')} strokeWidth={1.5} />
              <span className="min-w-0 flex-1 break-words">{log.message}</span>
            </button>
            {expanded && <pre className="mt-1 ml-4 whitespace-pre-wrap break-words font-mono text-[11px] text-slate-500">{log.detail}</pre>}
          </>
        ) : (
          <span className="block min-w-0 break-words text-slate-700">{log.message}</span>
        )}
      </div>
      <div className="flex items-start">{hasDetail && <CopyButton text={fullText} title="复制日志" />}</div>
    </div>
  );
}

function InfoRow({ children, label, mono, value }: {
  children?: ReactElement;
  label: string;
  mono?: boolean;
  value?: string;
}): ReactElement {
  return (
    <div className="flex items-center justify-between rounded-md bg-slate-50 px-3 py-2 text-[11px]">
      <span className="shrink-0 text-slate-500">{label}</span>
      {children ?? (
        <span className={mono ? 'max-w-40 truncate font-mono text-slate-700' : 'font-medium text-slate-800'}>{value ?? '--'}</span>
      )}
    </div>
  );
}

function formatDateTime(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function p(n: number): string { return String(n).padStart(2, '0'); }

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
