import { PictureInPicture2, PictureInPicture, Bot, BotOff, RotateCcw } from 'lucide-react';
import type { ReactElement } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '../../../lib/utils';
import { IconButton } from '../../ui/button';
import { ChatInput } from './ChatInput';
import { ChatMessages } from './ChatMessages';
import { ClearChatButton } from './ClearChatButton';
import { useAiChat } from './useAiChat';
import { useAiPanelStore } from '../../../stores/useAiPanelStore';
import type { NodeLookupItem } from './aiPanelTypes';
import {
  getComposerDraft,
  updateComposerDrafts,
  type AiComposerDrafts,
  type AiComposerDraftSetter,
} from './composerDraft';
import {
  adjustPanelRect,
  clampPanelRect,
  defaultPanelRect,
  MAX_PANEL_WIDTH,
  MIN_PANEL_HEIGHT,
  MIN_PANEL_WIDTH,
  type PanelRect,
  type ResizeDir,
} from './panelGeometry';

type PanelMode = 'sidebar' | 'float';
const GRIP = 6; // resize handle thickness px

const HANDLE_DEFS: { dir: ResizeDir; style: React.CSSProperties }[] = [
  { dir: 'e', style: { right: 0, top: GRIP, bottom: GRIP, width: GRIP, cursor: 'ew-resize' } },
  { dir: 'w', style: { left: 0, top: GRIP, bottom: GRIP, width: GRIP, cursor: 'ew-resize' } },
  { dir: 's', style: { bottom: 0, left: GRIP, right: GRIP, height: GRIP, cursor: 'ns-resize' } },
  { dir: 'n', style: { top: 0, left: GRIP, right: GRIP, height: GRIP, cursor: 'ns-resize' } },
  { dir: 'se', style: { bottom: 0, right: 0, width: 12, height: 12, cursor: 'se-resize' } },
  { dir: 'sw', style: { bottom: 0, left: 0, width: 12, height: 12, cursor: 'sw-resize' } },
  { dir: 'ne', style: { top: 0, right: 0, width: 12, height: 12, cursor: 'ne-resize' } },
  { dir: 'nw', style: { top: 0, left: 0, width: 12, height: 12, cursor: 'nw-resize' } },
];

function defaultFloat(): PanelRect {
  return defaultPanelRect(window.innerWidth, window.innerHeight);
}

function clampFloatRect(rect: PanelRect): PanelRect {
  return clampPanelRect(rect, window.innerWidth, window.innerHeight);
}

function ResizeHandles({ rectRef, onRectChange }: {
  rectRef: React.RefObject<PanelRect>;
  onRectChange: (r: PanelRect) => void;
}): ReactElement {
  const applyResize = useCallback((dir: ResizeDir, dx: number, dy: number, orig: PanelRect): PanelRect => {
    let { x, y, w, h } = orig;
    if (dir.includes('e')) w = Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, orig.w + dx));
    if (dir.includes('s')) h = Math.max(MIN_PANEL_HEIGHT, orig.h + dy);
    if (dir.includes('w')) { const nw = Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, orig.w - dx)); x = orig.x + orig.w - nw; w = nw; }
    if (dir.includes('n')) { const nh = Math.max(MIN_PANEL_HEIGHT, orig.h - dy); y = orig.y + orig.h - nh; h = nh; }
    return { x, y, w, h };
  }, []);

  const startResize = useCallback((e: React.PointerEvent, dir: ResizeDir) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const orig = { ...rectRef.current };
    const sx = e.clientX, sy = e.clientY;

    const onMove = (ev: PointerEvent) => onRectChange(applyResize(dir, ev.clientX - sx, ev.clientY - sy, orig));
    const onUp = () => { window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp); };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, [rectRef, onRectChange, applyResize]);

  return (
    <>
      {HANDLE_DEFS.map(({ dir, style }) => (
        <div
          aria-hidden="true"
          key={dir}
          className="absolute z-(--z-sticky)"
          style={{ ...style, position: 'absolute' }}
          onPointerDown={e => startResize(e, dir)}
        />
      ))}
    </>
  );
}

// window-level pointermove/pointerup, not setPointerCapture — capture can drop move events in Electron's sandbox.
function DragHeader({ children, rectRef, onRectChange }: {
  children: ReactElement;
  rectRef: React.MutableRefObject<PanelRect>;
  onRectChange: (r: PanelRect) => void;
}): ReactElement {
  const startDrag = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0) return;
    if ((e.target as HTMLElement).closest('button, a, input, select, textarea, [role="button"]')) return;
    e.preventDefault();
    const orig = { ...rectRef.current };
    const sx = e.clientX, sy = e.clientY;

    const onMove = (ev: PointerEvent) => {
      // min y=40 keeps panel below TitleBar drag region; Electron ignores z-index for
      // -webkit-app-region, so overlap hands control to the OS window manager instead
      const nx = Math.max(0, Math.min(window.innerWidth - rectRef.current.w, orig.x + ev.clientX - sx));
      const ny = Math.max(40, Math.min(window.innerHeight - 48, orig.y + ev.clientY - sy));
      onRectChange({ ...rectRef.current, x: nx, y: ny });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, [rectRef, onRectChange]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
    e.preventDefault();
    const step = e.altKey ? 1 : 12;
    onRectChange(adjustPanelRect(
      rectRef.current,
      e.key as 'ArrowLeft' | 'ArrowRight' | 'ArrowUp' | 'ArrowDown',
      e.shiftKey,
      step,
    ));
  }, [onRectChange, rectRef]);

  return (
    <div
      aria-label="浮窗标题栏；方向键移动，Shift 加方向键调整大小，Alt 可精细调整"
      className="no-drag shrink-0 cursor-grab select-none rounded-t-xl focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent active:cursor-grabbing"
      onKeyDown={handleKeyDown}
      onPointerDown={startDrag}
      role="group"
      tabIndex={0}
    >
      {children}
    </div>
  );
}

export function AiPanel({
  flowId,
  mode,
  open = true,
  onClose,
  onModeChange,
  onApplySuccess,
  pendingMessage,
  onClearPendingMessage,
  nodeLookup,
  onFocusNode,
  sidebarAvailable = true,
}: {
  flowId: string | null;
  mode: PanelMode;
  open?: boolean;
  onClose: () => void;
  onModeChange: (mode: PanelMode) => void;
  sidebarAvailable?: boolean;
  onApplySuccess?: (flowId: string) => void;
  pendingMessage?: string | null;
  onClearPendingMessage?: () => void;
  nodeLookup?: Record<string, NodeLookupItem>;
  onFocusNode?: (nodeId: string) => void;
}): ReactElement {
  const { messages, pending, sentHistory, model, setModel, send, stop, retry, applyDiff, clearDiff, clearMessages } =
    useAiChat(flowId, onApplySuccess);

  const lastMsg = messages.at(-1);
  // 仅当已有 reasoning 流入但正文还没开始输出时才算「思考中」，用于生成状态播报。
  const thinking = pending && lastMsg?.role === 'assistant' && !!lastMsg.reasoning && !lastMsg.content;

  const handleApplyDiff = useCallback(async (diff: import('./aiPanelTypes').FlowDiff): Promise<{ ok: boolean; error?: string }> => {
    const result = await applyDiff(diff);
    if (result.ok && diff.flow_id) onApplySuccess?.(diff.flow_id);
    return result;
  }, [applyDiff, onApplySuccess]);

  // 保持引用稳定，否则 memo 过的 MessageBubble 每次渲染仍会被打穿
  const handleRetry = useCallback(() => void retry(), [retry]);

  const [floatRect, setFloatRect] = useState<PanelRect>(defaultFloat);
  const [composerDrafts, setComposerDrafts] = useState<AiComposerDrafts>({});
  const composerSessionKey = flowId ? `flow_${flowId}` : 'local';
  const composerDraft = getComposerDraft(composerDrafts, composerSessionKey);
  const setComposerDraft = useCallback<AiComposerDraftSetter>((update) => {
    setComposerDrafts((current) => updateComposerDrafts(current, composerSessionKey, update));
  }, [composerSessionKey]);
  const handleFloatRectChange = useCallback(
    (next: PanelRect) => setFloatRect(clampFloatRect(next)),
    [setFloatRect],
  );
  const [syncedMode, setSyncedMode] = useState(mode);
  if (mode !== syncedMode) {
    setSyncedMode(mode);
    if (mode === 'float') setFloatRect(defaultFloat());
  }
  // 让 pointermove 闭包不必依赖 floatRect；同步只能放 effect 里，render 期写 ref 在并发渲染下不安全
  const floatRectRef = useRef(floatRect);
  useEffect(() => {
    floatRectRef.current = floatRect;
  }, [floatRect]);

  useEffect(() => {
    if (mode !== 'float' || !open) return;
    const handleResize = (): void => setFloatRect((current) => clampFloatRect(current));
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [mode, open]);

  const stopped = lastMsg?.toolCalls?.some((call) => call.status === 'stopped');
  const activityAnnouncement = pending
    ? lastMsg?.statusText ?? (thinking ? 'AI 助手正在思考' : 'AI 助手正在处理')
    : lastMsg?.role === 'assistant' && lastMsg.finishedAt !== undefined
      ? lastMsg.error ? 'AI 助手处理失败' : stopped ? 'AI 助手已停止' : 'AI 助手已完成本轮处理'
      : '';

  const switchMode = (next: PanelMode) => onModeChange(next);

  // 关掉面板不会中断这一轮对话，把生成状态抬到 store，画布上的悬浮球才有得可显示
  const setBusy = useAiPanelStore((s) => s.setBusy);
  useEffect(() => {
    setBusy(pending);
  }, [pending, setBusy]);

  // 外部触发的排查消息若在 pending 时到达，先停止当前生成再排队
  const queuedMsgRef = useRef<string | null>(null);

  useEffect(() => {
    if (!pendingMessage) return;
    onClearPendingMessage?.();
    if (!pending) {
      void send(pendingMessage);
    } else {
      queuedMsgRef.current = pendingMessage;
      stop();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingMessage]);

  // 只依赖 pending：等 stop() 真正让上一轮生成结束（pending 变 false）后再发排队消息
  useEffect(() => {
    if (!pending && queuedMsgRef.current) {
      const msg = queuedMsgRef.current;
      queuedMsgRef.current = null;
      void send(msg);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending]);

  const header = (
    <div className="flex h-10 shrink-0 items-center gap-2 border-b border-slate-200 px-3">
      <Bot className="h-3.5 w-3.5 shrink-0 text-accent" />
      <span className="shrink-0 text-[12px] font-semibold text-slate-700">RPA 助手</span>
      {/* 浮窗模式下输入框可能被拖出视野，标题栏保留一个生成中的指示 */}
      {pending && mode === 'float' && (
        <span className="flex min-w-0 items-center gap-1 text-[11px] text-accent-strong">
          <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-current" />
          <span className="truncate">生成中</span>
        </span>
      )}
      <span className="flex-1" />
      <ClearChatButton messageCount={messages.length} onClear={clearMessages} pending={pending} />
      {/* 左边是对话内容操作，右边是窗口操作，分隔开避免误点「清空」 */}
      <span className="mx-0.5 h-4 w-px shrink-0 bg-slate-200" />
      {mode === 'sidebar' ? (
        <IconButton label="弹出浮窗" onClick={() => switchMode('float')}>
          <PictureInPicture2 className="h-3.5 w-3.5" />
        </IconButton>
      ) : (
        <>
          <IconButton label="重置浮窗位置和大小" onClick={() => setFloatRect(defaultFloat())}>
            <RotateCcw className="h-3.5 w-3.5" />
          </IconButton>
          <IconButton
            disabled={!sidebarAvailable}
            label={sidebarAvailable ? '停靠到侧栏' : '画布空间不足，无法停靠到侧栏'}
            onClick={() => switchMode('sidebar')}
          >
            <PictureInPicture className="h-3.5 w-3.5" />
          </IconButton>
        </>
      )}
      <IconButton label="关闭 AI 面板" onClick={onClose}>
        <BotOff className="h-3.5 w-3.5" />
      </IconButton>
    </div>
  );

  const body = (
    <>
      <span aria-atomic="true" aria-live="polite" className="sr-only" role="status">
        {activityAnnouncement}
      </span>
      <ChatMessages
        hasFlow={Boolean(flowId)}
        messages={messages}
        nodeLookup={nodeLookup}
        onApplyDiff={handleApplyDiff}
        onFocusNode={onFocusNode}
        onRejectDiff={clearDiff}
        onRetry={handleRetry}
        onSuggestion={(prompt) => void send(prompt)}
        pending={pending}
      />
      <ChatInput
        autoFocus
        draft={composerDraft}
        history={sentHistory}
        model={model}
        onModelChange={setModel}
        onSend={(text, atts) => void send(text, atts)}
        onStop={stop}
        pending={pending}
        sessionKey={composerSessionKey}
        setDraft={setComposerDraft}
      />
    </>
  );

  if (mode === 'float') {
    if (!open) return <div className="hidden" />;
    return createPortal(
      <section
        aria-label="RPA 助手"
        className="no-drag fixed z-(--z-raised) flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl animate-in zoom-in-95 fade-in duration-150"
        role="dialog"
        style={{ left: floatRect.x, top: floatRect.y, width: floatRect.w, height: floatRect.h }}
      >
        <ResizeHandles rectRef={floatRectRef} onRectChange={handleFloatRectChange} />
        <DragHeader rectRef={floatRectRef} onRectChange={handleFloatRectChange}>
          {header}
        </DragHeader>
        <div className="flex min-h-0 flex-1 flex-col">{body}</div>
      </section>,
      document.body
    );
  }

  return (
    <aside
      aria-hidden={!open}
      aria-label="RPA 助手"
      className={cn(
        'flex h-full shrink-0 flex-col overflow-hidden bg-white',
        'transition-[width] duration-200 ease-in-out',
        open ? 'w-85 border-l border-slate-200' : 'w-0',
      )}
    >
      {open && (
        <div className="flex h-full w-85 shrink-0 flex-col">
          {header}
          {body}
        </div>
      )}
    </aside>
  );
}
