import { CheckCircle2, Clock3, DatabaseZap, FileJson, FolderOpen, Loader2, XCircle } from 'lucide-react';
import type { ReactElement } from 'react';

import { formatElapsedTime } from '../../lib/time';
import type { ArtifactSnapshot, TaskSnapshot } from '../../types/electron';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';

export function RunDetailDialog({
  onOpenArtifact,
  onOpenChange,
  open,
  run,
}: {
  onOpenArtifact?: (artifact: ArtifactSnapshot) => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  run: TaskSnapshot | null;
}): ReactElement | null {
  if (run === null) return null;

  const statusIcon = {
    running: <Loader2 className="h-4 w-4 animate-spin text-blue-500" strokeWidth={1.5} />,
    success: <CheckCircle2 className="h-4 w-4 text-emerald-500" strokeWidth={1.5} />,
    error: <XCircle className="h-4 w-4 text-red-500" strokeWidth={1.5} />,
    stopped: <XCircle className="h-4 w-4 text-amber-500" strokeWidth={1.5} />,
    queued: <Loader2 className="h-4 w-4 text-slate-400" strokeWidth={1.5} />,
  }[run.status];

  const statusLabel = { running: '运行中', success: '成功', error: '失败', stopped: '已停止', queued: '排队' }[run.status];
  const statusVariant = { success: 'emerald', error: 'red', running: 'blue', queued: 'amber', stopped: 'default' }[run.status] as 'emerald' | 'red' | 'blue' | 'amber' | 'default';

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="w-150">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {statusIcon}
            运行详情 · {run.flowName}
          </DialogTitle>
        </DialogHeader>

        <DialogBody className="space-y-4">
          {/* 基本信息 */}
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
            {run.error !== null && run.error !== undefined && (
              <div className="col-span-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-700">
                <span className="font-semibold">错误：</span>{run.error}
              </div>
            )}
          </div>

          {/* 变量快照 */}
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

          {/* 产物列表 */}
          {(run.artifacts?.length ?? 0) > 0 && (
            <div>
              <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-slate-600">
                <FileJson className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.5} />
                产物（{run.artifacts?.length ?? 0} 个）
              </div>
              <div className="max-h-25 space-y-1 overflow-auto rounded-md border border-slate-200 bg-slate-50 p-2">
                {(run.artifacts ?? []).map((a) => (
                  <div className="group flex items-center justify-between gap-2 rounded px-1 py-0.5 text-[11px] transition-colors hover:bg-slate-100" key={a.artifactId}>
                    <span className="min-w-0 flex-1 truncate font-mono text-slate-700">{a.filename}</span>
                    <span className="shrink-0 text-slate-400">{formatBytes(a.sizeBytes)}</span>
                    {onOpenArtifact !== undefined && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            aria-label={`在文件夹中打开 ${a.filename}`}
                            className="shrink-0 rounded p-0.5 text-slate-300 opacity-0 transition-all group-hover:opacity-100 hover:bg-slate-200 hover:text-slate-600"
                            onClick={() => onOpenArtifact(a)}
                            type="button"
                          >
                            <FolderOpen className="h-3.5 w-3.5" strokeWidth={1.5} />
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side="left">在文件夹中显示</TooltipContent>
                      </Tooltip>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </DialogBody>

        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="primary">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
