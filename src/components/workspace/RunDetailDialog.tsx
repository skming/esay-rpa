import {
  AlertCircle,
  ChevronRight,
  Eye,
  FileJson,
  FolderOpen,
  Loader2,
  LocateFixed,
  LockKeyhole,
  RefreshCcw,
} from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useRef, useState } from 'react';

import { TASK_STATUS_META } from '../../lib/runPresentation';
import { formatDateTime, formatElapsedTime } from '../../lib/time';
import { cn } from '../../lib/utils';
import type {
  ArtifactContent,
  ArtifactSnapshot,
  BackendTaskLogEntry,
  RunDetail,
  TaskSnapshot,
} from '../../types/electron';
import type { RunLogLevel, RuntimeVariable } from '../../types/rpa';
import { ArtifactPreviewDialog } from '../studio/bottom-panel/ArtifactPreviewDialog';
import { getLogTone } from '../studio/bottom-panel/bottomPanelUtils';
import { Button, IconButton } from '../ui/button';
import { CopyButton } from '../ui/copy-button';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import { SearchField, StateTag, SurfaceEmpty } from './surfaces';

const LOG_LEVEL_LABELS: Record<RunLogLevel, string> = {
  error: '错误',
  warn: '警告',
  input: '输入',
  success: '成功',
  running: '执行',
  info: '信息',
};
const LOG_FILTERS = [
  { value: 'all', label: '全部' },
  { value: 'error', label: '错误' },
  { value: 'warn', label: '警告' },
] as const;
type DetailTab = 'result' | 'logs' | 'variables';
type LogFilter = (typeof LOG_FILTERS)[number]['value'];
type RunDetailDialogProps = {
  onOpenArtifact?: (artifact: ArtifactSnapshot) => void;
  onReadArtifact?: (taskId: string, artifactId: string) => Promise<ArtifactContent | null>;
  onOpenChange: (open: boolean) => void;
  onLoadDetail: (taskId: string) => Promise<RunDetail>;
  open: boolean;
  run: TaskSnapshot | null;
};

export function RunDetailDialog(props: RunDetailDialogProps): ReactElement | null {
  if (props.run === null) return null;
  return <RunDetailContent key={props.run.taskId} {...props} run={props.run} />;
}

function RunDetailContent({
  onOpenArtifact,
  onReadArtifact,
  onOpenChange,
  onLoadDetail,
  open,
  run: listedRun,
}: Omit<RunDetailDialogProps, 'run'> & { run: TaskSnapshot }): ReactElement {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);
  const [tab, setTab] = useState<DetailTab>(() =>
    listedRun.status === 'error' || listedRun.status === 'running' ? 'logs' : 'result',
  );
  const [filter, setFilter] = useState<LogFilter>('all');
  const [query, setQuery] = useState('');
  const [locateKey, setLocateKey] = useState(0);
  const [preview, setPreview] = useState<{ artifact: ArtifactSnapshot; content: ArtifactContent | null } | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewRequestRef = useRef(0);
  const bodyRef = useRef<HTMLDivElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const locatedRef = useRef<number | null>(null);
  const taskId = listedRun.taskId;

  useEffect(() => {
    if (!open) return;
    let current = true;
    void onLoadDetail(taskId)
      .then((next) => {
        if (current) {
          setDetail(next);
          setLoadError(null);
          setLoading(false);
        }
      })
      .catch((error: unknown) => {
        if (current) {
          setLoadError(error instanceof Error ? error.message : String(error));
          setLoading(false);
        }
      });
    return () => {
      current = false;
    };
  }, [open, taskId, reloadKey, onLoadDetail]);

  const run = detail?.run ?? listedRun;
  const logs = detail?.logs ?? [];
  const variables = run.variables ?? [];
  const artifacts = run.artifacts ?? [];
  const status = TASK_STATUS_META[run.status];
  const errorLogs = logs.filter((log) => log.level === 'error');
  const errorCount = errorLogs.length;
  const warningCount = logs.filter((log) => log.level === 'warn').length;
  const lastErrorId = errorLogs.at(-1)?.id;
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleLogs = logs.filter(
    (log) =>
      (filter === 'all' || log.level === filter) &&
      (!normalizedQuery ||
        `${log.message} ${log.detail ?? ''} ${log.nodeId ?? ''}`.toLocaleLowerCase().includes(normalizedQuery)),
  );

  useEffect(() => {
    if (!open || loading || tab !== 'logs' || locatedRef.current === locateKey) return;
    const body = bodyRef.current;
    const target = errorRef.current;
    if (body !== null && target !== null) {
      body.scrollTop = Math.max(0, target.offsetTop - 12);
      locatedRef.current = locateKey;
    }
  }, [open, loading, tab, detail, locateKey]);

  const refresh = (): void => {
    setLoading(true);
    setLoadError(null);
    setReloadKey((key) => key + 1);
  };
  const locateError = (): void => {
    setFilter('all');
    setQuery('');
    setLocateKey((key) => key + 1);
  };
  const closePreview = (): void => {
    previewRequestRef.current += 1;
    setPreview(null);
  };
  const handleOpenChange = (nextOpen: boolean): void => {
    if (!nextOpen) {
      closePreview();
      setPreviewError(null);
    }
    onOpenChange(nextOpen);
  };
  const handlePreview = (artifact: ArtifactSnapshot): void => {
    if (onReadArtifact === undefined) return;
    const request = ++previewRequestRef.current;
    setPreviewError(null);
    setPreview({ artifact, content: null });
    void onReadArtifact(artifact.taskId, artifact.artifactId)
      .then((content) => {
        if (previewRequestRef.current !== request) return;
        setPreview(content === null ? null : { artifact, content });
        if (content === null) setPreviewError('读取产物失败，请重试。');
      })
      .catch(() => {
        if (previewRequestRef.current !== request) return;
        setPreview(null);
        setPreviewError('读取产物失败，请重试。');
      });
  };

  return (
    <>
      <Dialog onOpenChange={handleOpenChange} open={open}>
        <DialogContent className="h-[min(82dvh,720px)] max-h-[calc(100dvh-32px)] w-225 max-w-[calc(100vw-24px)]">
          <DialogHeader className="gap-3 px-4 pb-3 pt-4 pr-12 sm:px-5 sm:pr-12">
            <DialogTitle className="min-w-0 truncate text-[14px] font-semibold leading-5" title={run.flowName}>
              运行详情 · {run.flowName}
            </DialogTitle>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-ink-3">
              <StateTag state={status.tone} label={status.label} />
              <span>{run.mode === 'debug' ? '调试' : '正常运行'}</span>
              <span>
                耗时{' '}
                <span className="font-mono tabular-nums text-ink-2">{formatElapsedTime(run.progress.elapsedMs)}</span>
              </span>
              <span>
                进度{' '}
                <span className="tabular-nums text-ink-2">
                  {run.progress.currentStep} / {run.progress.totalSteps} 步（{run.progress.percent}%）
                </span>
              </span>
            </div>
            <div className="flex min-w-0 items-center gap-1.5 text-[10px] text-ink-3">
              <span className="shrink-0">任务 ID</span>
              <span className="truncate font-mono" title={taskId}>
                {taskId}
              </span>
              <CopyButton className="shrink-0" text={taskId} title="复制任务 ID" />
            </div>
          </DialogHeader>

          <Tabs
            className="flex min-h-0 flex-1 flex-col"
            value={tab}
            onValueChange={(value) => {
              setTab(value as DetailTab);
              if (bodyRef.current) bodyRef.current.scrollTop = 0;
            }}
          >
            <TabsList aria-label="运行详情内容" className="shrink-0 gap-5 border-y border-rule px-4 sm:px-5">
              <TabsTrigger className="h-10" value="result">
                结果
              </TabsTrigger>
              <TabsTrigger className="h-10 gap-1.5" value="logs">
                日志<span className="tabular-nums text-ink-3">{detail === null ? '—' : logs.length}</span>
              </TabsTrigger>
              <TabsTrigger className="h-10 gap-1.5" value="variables">
                变量<span className="tabular-nums text-ink-3">{variables.length}</span>
              </TabsTrigger>
            </TabsList>
            {tab === 'logs' && (
              <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-rule px-4 py-2.5 sm:px-5">
                <div className="flex items-center gap-1" aria-label="日志级别">
                  {LOG_FILTERS.map((item) => (
                    <Button
                      key={item.value}
                      aria-pressed={filter === item.value}
                      className={cn('h-7 px-2', filter === item.value && 'bg-accent-soft text-accent-strong')}
                      onClick={() => setFilter(item.value)}
                    >
                      {item.label}
                      <span className="tabular-nums">
                        {item.value === 'all' ? logs.length : item.value === 'error' ? errorCount : warningCount}
                      </span>
                    </Button>
                  ))}
                </div>
                <SearchField
                  className="order-3 min-w-0 max-w-none basis-full sm:order-none sm:min-w-36 sm:basis-auto"
                  label="搜索执行日志"
                  placeholder="搜索消息、详情或节点 ID"
                  value={query}
                  onChange={setQuery}
                />
                <IconButton
                  className="ml-auto sm:ml-0"
                  disabled={errorCount === 0}
                  label="定位最后一条错误"
                  onClick={locateError}
                >
                  <LocateFixed className="h-3.5 w-3.5" strokeWidth={1.5} />
                </IconButton>
              </div>
            )}
            <DialogBody className="relative overscroll-contain px-4 sm:px-5" ref={bodyRef} aria-busy={loading}>
              {loading && (
                <p className="mb-3 flex items-center gap-2 text-[11px] text-ink-3" role="status">
                  <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" strokeWidth={1.5} />
                  {detail === null ? '正在读取完整执行记录…' : '正在刷新执行记录…'}
                </p>
              )}
              {loadError !== null && (
                <div
                  className="mb-3 flex items-start gap-2 rounded-md bg-red-50 px-3 py-2 text-[11px] text-red-700"
                  role="alert"
                >
                  <span className="min-w-0 flex-1 break-words">
                    读取最新记录失败：{loadError}
                    {detail !== null && '。当前显示上次加载的记录。'}
                  </span>
                  <Button className="shrink-0 text-red-700" disabled={loading} onClick={refresh}>
                    重试
                  </Button>
                </div>
              )}
              <TabsContent className="space-y-5" value="result">
                {run.status === 'error' && run.error && <RunNotice title="执行失败" message={run.error} />}
                {run.status === 'awaiting_confirmation' && (
                  <RunNotice title="等待操作" message={run.confirmationMessage || '此任务正在等待操作确认。'} warning />
                )}
                {previewError !== null && (
                  <p className="text-[11px] text-red-700" role="alert">
                    {previewError}
                  </p>
                )}
                {run.result != null && (
                  <section>
                    <div className="mb-2 flex items-center justify-between">
                      <h3 className="text-[12px] font-semibold text-ink-2">提取结果 · {run.result.count} 条</h3>
                      <CopyButton text={JSON.stringify(run.result.values, null, 2)} title="复制提取结果" />
                    </div>
                    <details className="rounded-md border border-rule bg-paper">
                      <summary className="cursor-pointer px-3 py-2 text-[11px] text-ink-2">查看提取数据</summary>
                      <pre className="border-t border-rule p-3 whitespace-pre-wrap font-mono text-[11px] leading-5 text-ink-2 [overflow-wrap:anywhere]">
                        {JSON.stringify(run.result.values, null, 2)}
                      </pre>
                    </details>
                  </section>
                )}
                {artifacts.length > 0 && (
                  <section>
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <h3 className="text-[12px] font-semibold text-ink-2">产物 · {artifacts.length} 个</h3>
                      {onOpenArtifact && (
                        <Button onClick={() => onOpenArtifact(artifacts[0])}>
                          <FolderOpen className="h-3.5 w-3.5" strokeWidth={1.5} />
                          打开目录
                        </Button>
                      )}
                    </div>
                    <div className="divide-y divide-rule rounded-md border border-rule">
                      {artifacts.map((artifact) => (
                        <div className="flex items-center gap-2 px-3 py-2.5" key={artifact.artifactId}>
                          <FileJson className="h-3.5 w-3.5 shrink-0 text-ink-3" strokeWidth={1.5} />
                          <span className="min-w-0 flex-1 truncate text-[11px] text-ink-2" title={artifact.filename}>
                            {artifact.filename}
                          </span>
                          <span className="shrink-0 text-[10px] tabular-nums text-ink-3">
                            {formatBytes(artifact.sizeBytes)}
                          </span>
                          {onReadArtifact && (
                            <IconButton label={`预览 ${artifact.filename}`} onClick={() => handlePreview(artifact)}>
                              <Eye className="h-3.5 w-3.5" strokeWidth={1.5} />
                            </IconButton>
                          )}
                        </div>
                      ))}
                    </div>
                  </section>
                )}
                {!loading && run.result == null && artifacts.length === 0 && (
                  <SurfaceEmpty
                    title="暂无输出数据"
                    hint={
                      run.status === 'queued' || run.status === 'running' || run.status === 'awaiting_confirmation'
                        ? '任务尚未产生提取结果或产物，可在日志中查看执行情况。'
                        : '本次运行没有提取数据或生成产物，可在日志中查看执行过程。'
                    }
                  />
                )}
              </TabsContent>
              <TabsContent value="logs">
                {run.status === 'error' && run.error && (
                  <div className="mb-3">
                    <RunNotice title="执行失败" message={run.error} />
                  </div>
                )}
                {detail !== null && (
                  <p className="mb-2 text-[10px] tabular-nums text-ink-3" role="status">
                    显示 {visibleLogs.length} / {logs.length} 条日志
                  </p>
                )}
                {detail !== null && visibleLogs.length === 0 ? (
                  <SurfaceEmpty
                    title={logs.length === 0 ? '没有日志记录' : '没有匹配的日志'}
                    hint={logs.length === 0 ? '此运行尚未记录执行日志。' : '试试其他关键词，或切换到全部级别。'}
                  />
                ) : (
                  <div className="divide-y divide-rule">
                    {visibleLogs.map((log) => (
                      <div ref={log.id === lastErrorId ? errorRef : undefined} key={log.id}>
                        <RunLogRow log={log} />
                      </div>
                    ))}
                  </div>
                )}
              </TabsContent>
              <TabsContent value="variables">
                {variables.length === 0 ? (
                  !loading && <SurfaceEmpty title="没有变量快照" hint="此运行没有记录变量值。" />
                ) : (
                  <div className="divide-y divide-rule">
                    {variables.map((variable) => (
                      <VariableRow
                        key={`${variable.category ?? ''}:${variable.scope}:${variable.name}`}
                        variable={variable}
                      />
                    ))}
                  </div>
                )}
              </TabsContent>
            </DialogBody>
          </Tabs>
          <DialogFooter className="flex-wrap px-4 sm:px-5">
            <time
              className="mr-auto text-[10px] tabular-nums text-ink-3"
              dateTime={run.updatedAt}
              title={run.updatedAt}
            >
              更新于 {formatDateTime(run.updatedAt)}
            </time>
            <Button disabled={loading} onClick={refresh} variant="outline">
              <RefreshCcw
                className={cn('h-3.5 w-3.5', loading && 'animate-spin motion-reduce:animate-none')}
                strokeWidth={1.5}
              />
              {loading ? '读取中…' : '刷新记录'}
            </Button>
            <Button onClick={() => handleOpenChange(false)} variant="primary">
              关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ArtifactPreviewDialog
        artifact={preview?.artifact ?? null}
        content={preview?.content ?? null}
        loading={preview !== null && preview.content === null}
        onOpenChange={(next) => {
          if (!next) closePreview();
        }}
        open={preview !== null}
      />
    </>
  );
}

function RunNotice({
  title,
  message,
  warning = false,
}: {
  title: string;
  message: string;
  warning?: boolean;
}): ReactElement {
  return (
    <div
      className={cn(
        'flex items-start gap-2 rounded-md px-3 py-2.5',
        warning ? 'bg-amber-50 text-amber-800' : 'bg-red-50 text-red-700',
      )}
    >
      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.5} />
      <div className="min-w-0 flex-1">
        <div className="text-[11px] font-semibold">{title}</div>
        <p className="mt-1 whitespace-pre-wrap text-[11px] leading-5 [overflow-wrap:anywhere]">{message}</p>
      </div>
      <CopyButton
        className={warning ? 'text-amber-800' : 'text-red-700'}
        text={message}
        title={warning ? '复制操作提示' : '复制错误'}
      />
    </div>
  );
}

function RunLogRow({ log }: { log: BackendTaskLogEntry }): ReactElement {
  const [expanded, setExpanded] = useState(false);
  const tone = getLogTone((Object.hasOwn(LOG_LEVEL_LABELS, log.level) ? log.level : 'info') as RunLogLevel);
  const hasDetail = Boolean(log.detail);
  return (
    <div
      className={cn(
        'grid grid-cols-[minmax(0,1fr)_24px] gap-1 rounded-md px-2 py-2.5 text-[11px] sm:grid-cols-[64px_56px_minmax(0,1fr)_24px] sm:gap-2',
        tone.row,
      )}
    >
      <time className="hidden font-mono tabular-nums text-ink-3 sm:block" title={log.time}>
        {formatLogTime(log.time)}
      </time>
      <span className={cn('hidden font-medium sm:block', tone.text)}>
        {LOG_LEVEL_LABELS[log.level as RunLogLevel] ?? log.level}
      </span>
      <div className="min-w-0">
        <div className="mb-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] text-ink-3">
          <span className="sm:hidden">
            {formatLogTime(log.time)} · {LOG_LEVEL_LABELS[log.level as RunLogLevel] ?? log.level}
          </span>
          {log.nodeId && <span className="break-all font-mono">节点 {log.nodeId}</span>}
        </div>
        {hasDetail ? (
          <>
            <button
              aria-expanded={expanded}
              aria-label={expanded ? '收起日志详情' : '展开日志详情'}
              className="flex w-full min-w-0 items-start gap-1 rounded-sm text-left text-ink-2"
              onClick={() => setExpanded((value) => !value)}
              type="button"
            >
              <ChevronRight
                className={cn(
                  'mt-0.5 h-3 w-3 shrink-0 text-ink-3 transition-transform motion-reduce:transition-none',
                  expanded && 'rotate-90',
                )}
                strokeWidth={1.5}
              />
              <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{log.message}</span>
            </button>
            {expanded && (
              <pre className="mt-2 whitespace-pre-wrap font-mono text-[11px] leading-5 text-ink-2 [overflow-wrap:anywhere]">
                {log.detail}
              </pre>
            )}
          </>
        ) : (
          <p className="text-ink-2 [overflow-wrap:anywhere]">{log.message}</p>
        )}
      </div>
      <CopyButton text={hasDetail ? `${log.message}\n${log.detail}` : log.message} title="复制日志" />
    </div>
  );
}

function VariableRow({ variable }: { variable: RuntimeVariable }): ReactElement {
  const protectedValue = variable.sensitive || variable.category === 'credential';
  return (
    <div className="py-3">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="break-all font-mono text-[11px] font-medium text-ink-2">{variable.name}</span>
        <span className="text-[10px] text-ink-3">
          {variable.type} · {variable.scope}
        </span>
      </div>
      {protectedValue ? (
        <span className="inline-flex items-center gap-1.5 text-[11px] text-ink-3">
          <LockKeyhole className="h-3 w-3" strokeWidth={1.5} />
          •••••••• · 敏感值已隐藏
        </span>
      ) : (
        <details className="rounded-md bg-paper px-3 py-2">
          <summary className="cursor-pointer truncate font-mono text-[11px] text-ink-3" title="展开查看完整变量值">
            {variable.value.length > 120 ? `${variable.value.slice(0, 120)}…` : variable.value || '（空值）'}
          </summary>
          <div className="mt-2 flex items-start gap-2 border-t border-rule pt-2">
            <pre className="min-w-0 flex-1 whitespace-pre-wrap font-mono text-[11px] leading-5 text-ink-2 [overflow-wrap:anywhere]">
              {variable.value}
            </pre>
            <CopyButton text={variable.value} title={`复制变量 ${variable.name}`} />
          </div>
        </details>
      )}
    </div>
  );
}

function formatLogTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString('zh-CN', { hour12: false });
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
