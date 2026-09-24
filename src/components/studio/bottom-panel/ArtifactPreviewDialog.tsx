import { Check, ChevronLeft, ChevronRight, Copy, Loader2 } from 'lucide-react';
import { JsonView, allExpanded } from 'react-json-view-lite';
import 'react-json-view-lite/dist/index.css';
import './json-view-theme.css';
import { rpaJsonViewStyles } from './jsonViewStyles';
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent, ReactElement, ReactNode } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { Button } from '../../ui/button';
import { CodeBlock, InlineCode } from '../../ui/CodeBlock';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../ui/table';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../../ui/tooltip';

import type { ArtifactContent, ArtifactSnapshot } from '../../../types/electron';
import { Badge } from '../../ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../../ui/dialog';
import { getArtifactMeta } from './artifactMeta';

const OOXML_EXTS = new Set(['.xlsx', '.xlsm', '.xls', '.docx', '.doc', '.pptx', '.ppt']);

function getOoxmlExt(filename: string): string | null {
  const dot = filename.lastIndexOf('.');
  if (dot === -1) return null;
  const ext = filename.slice(dot).toLowerCase();
  return OOXML_EXTS.has(ext) ? ext : null;
}

const MARKDOWN_EXTS = new Set(['.md', '.markdown', '.mdown', '.mkd']);

function isMarkdownFile(filename: string, contentType: string): boolean {
  const dot = filename.lastIndexOf('.');
  const ext = dot === -1 ? '' : filename.slice(dot).toLowerCase();
  return MARKDOWN_EXTS.has(ext) || contentType.includes('markdown');
}

// 部分 markdown artifact 以 base64 data URL 形式下发（后端未能识别 text/* mime type），需解码回 UTF-8
function decodeMarkdownSource(content: string): string {
  const trimmed = content.trim();
  if (!trimmed.startsWith('data:')) return trimmed;
  const comma = trimmed.indexOf(',');
  if (comma === -1) return trimmed;
  const header = trimmed.slice(5, comma);
  const body = trimmed.slice(comma + 1);
  if (!header.includes('base64')) {
    try { return decodeURIComponent(body); } catch { return body; }
  }
  try {
    const binary = atob(body);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new TextDecoder('utf-8').decode(bytes);
  } catch {
    return trimmed;
  }
}

function base64ToArrayBuffer(dataUrl: string): ArrayBuffer {
  const base64 = dataUrl.includes(',') ? dataUrl.split(',')[1] : dataUrl;
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

export function ArtifactPreviewDialog({
  artifact,
  content,
  loading,
  onOpenChange,
  open,
}: {
  artifact: ArtifactSnapshot | null;
  content: ArtifactContent | null;
  loading?: boolean;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}): ReactElement {
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="flex h-[min(80vh,600px)] w-200 max-w-[calc(100vw-32px)] flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 border-b border-slate-100 px-5 py-4">
          <DialogTitle className="flex items-center gap-2 font-mono text-sm">
            {artifact && (() => {
              const meta = getArtifactMeta(artifact.artifactType);
              return <meta.icon className={`h-4 w-4 ${meta.iconTone}`} strokeWidth={1.5} />;
            })()}
            {artifact?.filename ?? '预览'}
          </DialogTitle>
        </DialogHeader>
        <ArtifactPreviewContent content={content} loading={loading} />
      </DialogContent>
    </Dialog>
  );
}

export function ArtifactPreviewContent({ content, loading }: { content: ArtifactContent | null; loading?: boolean }): ReactElement {
  const [copied, setCopied] = useState(false);

  if (loading) {
    return (
      <div className="grid min-h-60 flex-1 place-items-center bg-slate-950 text-[11px] text-slate-400">
        加载中…
      </div>
    );
  }

  if (content === null) {
    return (
      <div className="grid min-h-60 flex-1 place-items-center bg-slate-950 text-[11px] text-slate-400">
        无内容
      </div>
    );
  }

  const { artifactId, filename, contentType } = content.artifact;
  const isImage = contentType.startsWith('image/');
  const ooxmlExt = getOoxmlExt(filename);
  const isMarkdown = !isImage && !ooxmlExt && isMarkdownFile(filename, contentType);
  const isJson = !isImage && !ooxmlExt && !isMarkdown && (contentType.includes('json') || filename.toLowerCase().endsWith('.json'));
  const raw = content.content.trim();

  let parsedJson: unknown = null;
  if (isJson) {
    try { parsedJson = deepParseJsonStrings(JSON.parse(raw)); } catch { /* fall through */ }
  }

  const copyText = parsedJson !== null
    ? JSON.stringify(parsedJson, null, 2)
    : isMarkdown ? decodeMarkdownSource(raw) : raw;

  const handleCopy = (): void => {
    void navigator.clipboard.writeText(copyText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const badgeLabel = ooxmlExt
    ? ooxmlExt.slice(1).toUpperCase()
    : isMarkdown
      ? 'MARKDOWN'
      : contentType;

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-console">
      <div className="flex h-8 shrink-0 items-center gap-2 border-b border-white/10 px-3">
        <Badge className="border-slate-700 bg-slate-900 text-slate-300">
          {badgeLabel}
        </Badge>
        {!ooxmlExt && (
          <span className="ml-auto font-mono text-[10px] text-slate-500">{formatBytes(content.content.length)} chars</span>
        )}
        {!isImage && !ooxmlExt && (
          <TooltipProvider delayDuration={300}>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  aria-label="复制内容"
                  className="flex h-6 w-6 items-center justify-center rounded transition-colors hover:bg-white/10"
                  onClick={handleCopy}
                  type="button"
                >
                  {copied
                    ? <Check className="h-3.5 w-3.5 text-emerald-400" strokeWidth={2} />
                    : <Copy className="h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />
                  }
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">{copied ? '已复制' : '复制内容'}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>
      {isImage ? (
        // key 让换图时缩放状态随之复位到 100%，无需在 effect 里手工重置
        <ImagePreview alt={filename} key={artifactId} src={content.content} />
      ) : ooxmlExt ? (
        // key 让换文档时预览器整体重挂载，页码/状态随之复位，无需在 effect 里手工重置
        <OoxmlPreview content={content.content} ext={ooxmlExt} key={artifactId} />
      ) : isMarkdown ? (
        <MarkdownPreview source={decodeMarkdownSource(raw)} />
      ) : parsedJson !== null ? (
        <div className="rpa-json-view min-h-0 flex-1 overflow-y-auto overflow-x-auto p-3 font-mono text-[11px] leading-5">
          <JsonView
            data={parsedJson as object}
            shouldExpandNode={allExpanded}
            style={rpaJsonViewStyles}
          />
        </div>
      ) : (
        <pre className="min-h-0 flex-1 overflow-x-auto overflow-y-auto whitespace-pre p-4 font-mono text-[11px] leading-5 text-slate-200">{raw}</pre>
      )}
    </div>
  );
}

// 平移边界：offsetWidth/Height 是 transform 前的布局尺寸，乘 scale 得显示尺寸；只有溢出容器的部分可平移
function clampPan(container: HTMLElement | null, img: HTMLElement | null, x: number, y: number, scale: number): { x: number; y: number } {
  if (container === null || img === null) return { x, y };
  const maxX = Math.max(0, (img.offsetWidth * scale - container.clientWidth) / 2);
  const maxY = Math.max(0, (img.offsetHeight * scale - container.clientHeight) / 2);
  return { x: Math.min(maxX, Math.max(-maxX, x)), y: Math.min(maxY, Math.max(-maxY, y)) };
}

function ImagePreview({ alt, src }: { alt: string; src: string }): ReactElement {
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [ratioVisible, setRatioVisible] = useState(false);
  // wheel/拖拽/双击高频触发：实时值累积到 ref，用 rAF 每帧只提交一次 state；缩放以焦点为锚
  const scaleRef = useRef(1);
  const offsetRef = useRef({ x: 0, y: 0 });
  const committedScaleRef = useRef(1);  // offsetRef 所对应的已提交缩放，是缩放锚点换算的分母；双击直改缩放时必须一起更新
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // 展示比例并计时淡出；wheel 与双击共用同一 ref 计时器；useCallback 保持引用稳定，wheel effect 才能只订阅一次
  const pokeRatio = useCallback((): void => {
    setRatioVisible(true);
    clearTimeout(hideTimerRef.current);
    hideTimerRef.current = setTimeout(() => setRatioVisible(false), 800);
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (el === null) return;
    // 滚轮与触控板捏合统一走 wheel 缩放；React onWheel 是 passive 无法 preventDefault，只有原生非 passive 监听能拦掉整页缩放
    let raf = 0;
    const flush = (): void => {
      raf = 0;
      const s = scaleRef.current;
      // 以容器中心为锚：offset 随缩放比例同步放缩，让中心处的图像点在缩放中保持不动
      const prev = committedScaleRef.current;
      const anchored = s <= 1
        ? { x: 0, y: 0 }
        : { x: offsetRef.current.x * (s / prev), y: offsetRef.current.y * (s / prev) };
      const clamped = clampPan(containerRef.current, imgRef.current, anchored.x, anchored.y, s);
      committedScaleRef.current = s;
      offsetRef.current = clamped;
      setScale(s);
      setOffset(clamped);
    };
    const onWheel = (e: WheelEvent): void => {
      e.preventDefault();
      // 不做 0.01 量化，保留触控板捏合的连续步进；比例数字在渲染处再取整
      scaleRef.current = Math.min(4, Math.max(0.25, scaleRef.current * Math.exp(-e.deltaY * 0.0015)));
      if (raf === 0) raf = requestAnimationFrame(flush);
      pokeRatio();
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      el.removeEventListener('wheel', onWheel);
      clearTimeout(hideTimerRef.current);
      if (raf !== 0) cancelAnimationFrame(raf);
    };
  }, [pokeRatio]);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    if (scaleRef.current <= 1) return;
    dragRef.current = { x: e.clientX, y: e.clientY, ox: offsetRef.current.x, oy: offsetRef.current.y };
    setDragging(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>): void => {
    const d = dragRef.current;
    if (d === null) return;
    const next = clampPan(containerRef.current, imgRef.current, d.ox + e.clientX - d.x, d.oy + e.clientY - d.y, scaleRef.current);
    offsetRef.current = next;
    setOffset(next);
  };
  const endDrag = (): void => {
    dragRef.current = null;
    setDragging(false);
  };
  // 双击切换：已放大则复位到适配；否则放大到双击点并保持该点不动
  const onToggleZoom = (e: ReactMouseEvent<HTMLDivElement>): void => {
    const c = containerRef.current;
    if (c === null) return;
    if (scaleRef.current > 1) {
      scaleRef.current = 1;
      committedScaleRef.current = 1;
      offsetRef.current = { x: 0, y: 0 };
      setScale(1);
      setOffset({ x: 0, y: 0 });
    } else {
      const s1 = committedScaleRef.current;
      const s2 = 2;
      const rect = c.getBoundingClientRect();
      // 双击点相对容器中心的位移；screenRel = o + s·u 恒定 → o2 = screenRel·(1 - s2/s1) + o1·(s2/s1) 使该点缩放后不动
      const px = e.clientX - rect.left - rect.width / 2;
      const py = e.clientY - rect.top - rect.height / 2;
      const o = clampPan(
        c,
        imgRef.current,
        px * (1 - s2 / s1) + offsetRef.current.x * (s2 / s1),
        py * (1 - s2 / s1) + offsetRef.current.y * (s2 / s1),
        s2,
      );
      scaleRef.current = s2;
      committedScaleRef.current = s2;
      offsetRef.current = o;
      setScale(s2);
      setOffset(o);
    }
    pokeRatio();
  };

  return (
    <div className="relative flex min-h-0 flex-1 flex-col bg-slate-900">
      <div
        className="grid min-h-0 flex-1 place-items-center overflow-hidden p-4"
        onDoubleClick={onToggleZoom}
        onPointerDown={onPointerDown}
        onPointerLeave={endDrag}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        ref={containerRef}
        style={{ cursor: scale > 1 ? (dragging ? 'grabbing' : 'grab') : 'default' }}
      >
        <img
          alt={alt}
          className="max-h-full max-w-full select-none rounded border border-white/10 object-contain"
          draggable={false}
          ref={imgRef}
          src={src}
          style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})` }}
        />
      </div>
      <div
        className={`pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full bg-slate-950/80 px-3 py-1 font-mono text-[11px] tabular-nums text-slate-100 shadow-lg backdrop-blur transition-opacity duration-300 ${ratioVisible ? 'opacity-100' : 'opacity-0'}`}
      >
        {Math.round(scale * 100)}%
      </div>
    </div>
  );
}

type PagedViewer = {
  destroy(): void;
  next?(): Promise<void>;
  prev?(): Promise<void>;
  pageCount: number;
};

// adaptive：超预算时降采样而非硬失败；256MiB 为经验上限
// 勿静态 import 该包类型：会把其声明图连带 @types/node/ffi.d.ts 拉进 tsc，本地 tsc 解析不了会整 build 失败
const OOXML_IMAGE_RESOURCES = {
  strategy: 'adaptive',
  resolution: 'display',
  decodedByteBudget: 256 * 1024 * 1024,
} as const;

// 按 code 鸭子判别而非 import 运行时 guard：保住按格式懒加载，不把 ooxml 主包提前打进 bundle
function friendlyOoxmlError(e: unknown): string {
  const err = e as { code?: unknown; message?: unknown } | null;
  const code = typeof err?.code === 'string' ? err.code : '';
  if (code === 'ooxml-decoded-image-limit') return '内嵌图片过大，超出预览解码上限；请用本地软件打开。';
  if (code === 'ooxml-resource-limit') return '文档过大或过于复杂，超出预览上限；请用本地软件打开。';
  return typeof err?.message === 'string' && err.message ? err.message : '文件解析失败';
}

function OoxmlPreview({ content, ext }: { content: string; ext: string }): ReactElement {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewerRef = useRef<PagedViewer | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(1);

  const isXlsx = ext === '.xlsx' || ext === '.xlsm' || ext === '.xls';
  const isDocx = ext === '.docx' || ext === '.doc';

  useEffect(() => {
    let cancelled = false;

    // import 与解析分开计时：15s 防 chunk 加载卡死，30s 给多页 xlsx/docx 留够解析时间
    const withTimeout = <T,>(promise: Promise<T>, ms: number, label: string): Promise<T> => {
      return new Promise((resolve, reject) => {
        const tid = setTimeout(() => reject(new Error(`${label} timed out after ${ms / 1000}s`)), ms);
        promise.then((v) => { clearTimeout(tid); resolve(v); }, (e) => { clearTimeout(tid); reject(e); });
      });
    };

    const init = async (): Promise<void> => {
      try {
        const buffer = base64ToArrayBuffer(content);
        console.debug('[OoxmlPreview] buffer size', buffer.byteLength, 'ext', ext);

        if (isXlsx) {
          const mod = await withTimeout(import('@silurus/ooxml/xlsx'), 15000, 'import xlsx');
          if (cancelled || !containerRef.current) return;
          const v = new mod.XlsxViewer(containerRef.current, { imageResources: OOXML_IMAGE_RESOURCES });
          console.debug('[OoxmlPreview] XlsxViewer created, loading…');
          await withTimeout(v.load(buffer), 30000, 'xlsx load');
          if (cancelled) { v.destroy(); return; }
          viewerRef.current = { destroy: () => v.destroy(), pageCount: 1 };

        } else if (isDocx) {
          const mod = await withTimeout(import('@silurus/ooxml/docx'), 15000, 'import docx');
          if (cancelled || !canvasRef.current) return;
          const v = new mod.DocxViewer(canvasRef.current, { imageResources: OOXML_IMAGE_RESOURCES });
          console.debug('[OoxmlPreview] DocxViewer created, loading…');
          await withTimeout(v.load(buffer), 30000, 'docx load');
          if (cancelled) { v.destroy(); return; }
          const count = v.pageCount;
          setTotal(count);
          viewerRef.current = {
            destroy: () => v.destroy(),
            next: async () => { await v.nextPage(); setPage((p) => Math.min(p + 1, count)); },
            prev: async () => { await v.prevPage(); setPage((p) => Math.max(p - 1, 1)); },
            pageCount: count,
          };

        } else {
          const mod = await withTimeout(import('@silurus/ooxml/pptx'), 15000, 'import pptx');
          if (cancelled || !canvasRef.current) return;
          const v = new mod.PptxViewer(canvasRef.current, { imageResources: OOXML_IMAGE_RESOURCES });
          console.debug('[OoxmlPreview] PptxViewer created, loading…');
          await withTimeout(v.load(buffer), 30000, 'pptx load');
          if (cancelled) { v.destroy(); return; }
          const count = v.slideCount;
          setTotal(count);
          viewerRef.current = {
            destroy: () => v.destroy(),
            next: async () => { await v.nextSlide(); setPage((p) => Math.min(p + 1, count)); },
            prev: async () => { await v.prevSlide(); setPage((p) => Math.max(p - 1, 1)); },
            pageCount: count,
          };
        }

        if (!cancelled) setStatus('ready');
      } catch (e) {
        console.error('[OoxmlPreview] error:', e);
        if (!cancelled) { setError(friendlyOoxmlError(e)); setStatus('error'); }
      }
    };

    void init();

    return () => {
      cancelled = true;
      viewerRef.current?.destroy();
      viewerRef.current = null;
    };
  }, [content, ext, isDocx, isXlsx]);

  if (status === 'error') {
    return (
      <div className="grid min-h-60 flex-1 place-items-center bg-slate-950 px-6 text-center text-[11px] text-red-400">
        {error || '文件解析失败'}
      </div>
    );
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col bg-white">
      {status === 'loading' && (
        <div className="absolute inset-0 z-(--z-sticky) flex flex-col items-center justify-center gap-2.5 bg-white">
          <Loader2 className="h-5 w-5 animate-spin text-slate-300" strokeWidth={1.5} />
          <span className="text-[11px] text-slate-500">正在加载文件…</span>
        </div>
      )}

      {!isXlsx && total > 1 && status === 'ready' && (
        <div className="flex shrink-0 items-center gap-2 border-b border-slate-100 bg-slate-50 px-3 py-1.5">
          <Button
            className="h-6 w-6 px-0"
            disabled={page <= 1}
            onClick={() => void viewerRef.current?.prev?.()}
            variant="outline"
          >
            <ChevronLeft className="h-3.5 w-3.5" strokeWidth={1.5} />
          </Button>
          <span className="font-mono text-[11px] text-slate-500">
            {page} / {total}
          </span>
          <Button
            className="h-6 w-6 px-0"
            disabled={page >= total}
            onClick={() => void viewerRef.current?.next?.()}
            variant="outline"
          >
            <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.5} />
          </Button>
        </div>
      )}

      {/* XlsxViewer 自行管理 containerRef 内的 canvas + tab bar */}
      {isXlsx && (
        <div className="relative min-h-0 flex-1 overflow-hidden" ref={containerRef} />
      )}

      {!isXlsx && (
        <div className="min-h-0 flex-1 overflow-auto">
          <canvas className="w-full" ref={canvasRef} />
        </div>
      )}
    </div>
  );
}

// 报告类内容走浅色阅读排版，而非深色代码原文视图
function MarkdownPreview({ source }: { source: string }): ReactElement {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-slate-50">
      <article className="mx-auto max-w-[72ch] px-8 py-7 text-[13px] leading-relaxed text-slate-700">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({ children }) => (
              <h1 className="mb-3 mt-6 border-b border-slate-200 pb-1.5 text-[20px] font-semibold tracking-tight text-slate-900 first:mt-0">{children}</h1>
            ),
            h2: ({ children }) => (
              <h2 className="mb-2.5 mt-6 text-[16px] font-semibold tracking-tight text-slate-900 first:mt-0">{children}</h2>
            ),
            h3: ({ children }) => (
              <h3 className="mb-2 mt-5 text-[14px] font-semibold text-slate-800 first:mt-0">{children}</h3>
            ),
            p: ({ children }) => <p className="my-2.5 first:mt-0">{children}</p>,
            ul: ({ children }) => <ul className="my-2.5 ml-5 list-disc space-y-1">{children}</ul>,
            ol: ({ children }) => <ol className="my-2.5 ml-5 list-decimal space-y-1">{children}</ol>,
            li: ({ children }) => <li className="leading-relaxed">{children}</li>,
            blockquote: ({ children }) => (
              <blockquote className="my-3 border-l-[3px] border-accent-line bg-accent-soft py-1 pl-4 text-accent italic">{children}</blockquote>
            ),
            hr: () => <hr className="my-5 border-slate-200" />,
            a: ({ href, children }) => (
              <a className="text-accent-strong underline decoration-accent-line underline-offset-2 hover:text-accent-strong" href={href} rel="noopener noreferrer" target="_blank">{children}</a>
            ),
            strong: ({ children }) => <strong className="font-semibold text-slate-900">{children}</strong>,
            code: ({ className, children, ...props }: { className?: string; children?: ReactNode }) => {
              if (/language-(\w+)/.test(className ?? '')) {
                return <code className={className} {...props}>{children}</code>;
              }
              return <InlineCode className="bg-slate-200/70 px-1.5 text-[12px]">{children}</InlineCode>;
            },
            pre: ({ children }) => {
              const codeEl = children as ReactElement | null;
              const codeClass: string = (codeEl?.props as { className?: string } | undefined)?.className ?? '';
              const lang = /language-(\w+)/.exec(codeClass)?.[1] ?? '';
              const rawCode = String((codeEl?.props as { children?: ReactNode })?.children ?? '').replace(/\n$/, '');
              return <CodeBlock className="my-3 text-[12px]" code={rawCode} language={lang} variant="light" />;
            },
            table: ({ children }) => (
              <div className="my-4 overflow-x-auto rounded-lg border border-slate-200">
                <Table className="text-[12px]">{children}</Table>
              </div>
            ),
            thead: ({ children }) => <TableHeader className="bg-slate-100">{children}</TableHeader>,
            tbody: ({ children }) => <TableBody>{children}</TableBody>,
            tr: ({ children }) => <TableRow>{children}</TableRow>,
            th: ({ children }) => <TableHead className="font-semibold text-slate-700">{children}</TableHead>,
            td: ({ children }) => <TableCell className="text-slate-600">{children}</TableCell>,
          }}
        >
          {source}
        </ReactMarkdown>
      </article>
    </div>
  );
}

// 递归把值为 JSON 字符串的字段也解析展开，方便折叠查看；depth 上限 3 层防止异常数据导致无限递归
function deepParseJsonStrings(value: unknown, depth = 0): unknown {
  if (depth > 3) return value;
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try { return deepParseJsonStrings(JSON.parse(trimmed), depth + 1); } catch { /* keep as string */ }
    }
    return value;
  }
  if (Array.isArray(value)) return value.map((item) => deepParseJsonStrings(item, depth + 1));
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, deepParseJsonStrings(v, depth + 1)]));
  }
  return value;
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return '0 B';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
