import { Maximize2, Minimize2, PanelRightClose, Sparkles, Trash2 } from 'lucide-react';
import type { ReactElement } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '../../../lib/utils';
import { IconButton } from '../../ui/button';
import { ChatInput } from './ChatInput';
import { ChatMessages } from './ChatMessages';
import { useAiChat } from './useAiChat';
import type { NodeLookupItem } from './aiPanelTypes';

// ─── Types ─────────────────────────────────────────────────────────────────────
type PanelMode = 'sidebar' | 'float';
type Rect = { x: number; y: number; w: number; h: number };
type ResizeDir = 'e' | 's' | 'se' | 'sw' | 'w' | 'n' | 'ne' | 'nw';

const MIN_W = 320;
const MAX_W = 1100;
const MIN_H = 360;
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

function defaultFloat(): Rect {
  const w = Math.min(440, window.innerWidth - 32);
  const h = Math.min(window.innerHeight - 80, 720);
  return { x: window.innerWidth - w - 16, y: 44, w, h };
}

// ─── Resize handles overlay ────────────────────────────────────────────────────
function ResizeHandles({ rectRef, onRectChange }: {
  rectRef: React.MutableRefObject<Rect>;
  onRectChange: (r: Rect) => void;
}): ReactElement {
  const applyResize = useCallback((dir: ResizeDir, dx: number, dy: number, orig: Rect): Rect => {
    let { x, y, w, h } = orig;
    if (dir.includes('e')) w = Math.max(MIN_W, Math.min(MAX_W, orig.w + dx));
    if (dir.includes('s')) h = Math.max(MIN_H, orig.h + dy);
    if (dir.includes('w')) { const nw = Math.max(MIN_W, Math.min(MAX_W, orig.w - dx)); x = orig.x + orig.w - nw; w = nw; }
    if (dir.includes('n')) { const nh = Math.max(MIN_H, orig.h - dy); y = orig.y + orig.h - nh; h = nh; }
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
          key={dir}
          className="absolute z-10"
          style={{ ...style, position: 'absolute' }}
          onPointerDown={e => startResize(e, dir)}
        />
      ))}
    </>
  );
}

// ─── Draggable header wrapper ──────────────────────────────────────────────────
// Uses window-level pointermove/pointerup without setPointerCapture.
// setPointerCapture redirects events to the capturing element, which conflicts
// with window-level listeners and can drop move events in Electron's sandbox.
function DragHeader({ children, rectRef, onRectChange }: {
  children: ReactElement;
  rectRef: React.MutableRefObject<Rect>;
  onRectChange: (r: Rect) => void;
}): ReactElement {
  const startDrag = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0) return;
    if ((e.target as HTMLElement).closest('button, a, input, select, textarea, [role="button"]')) return;
    e.preventDefault();
    const orig = { ...rectRef.current };
    const sx = e.clientX, sy = e.clientY;

    const onMove = (ev: PointerEvent) => {
      // Min y = 40 keeps the panel below the native TitleBar drag region (h-9 = 36px).
      // Electron ignores z-index for -webkit-app-region, so any overlap with the
      // drag region would hand control to the OS window manager instead of this handler.
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

  return (
    <div
      className="no-drag shrink-0 cursor-grab select-none active:cursor-grabbing"
      onPointerDown={startDrag}
    >
      {children}
    </div>
  );
}

// ─── Main AiPanel component ────────────────────────────────────────────────────
export function AiPanel({
  flowId,
  open = true,
  onClose,
  onModeChange,
  onApplySuccess,
  pendingMessage,
  onClearPendingMessage,
  nodeLookup,
  onFocusNode,
}: {
  flowId: string | null;
  open?: boolean;
  onClose: () => void;
  onModeChange?: (mode: PanelMode) => void;
  onApplySuccess?: (flowId: string) => void;
  pendingMessage?: string | null;
  onClearPendingMessage?: () => void;
  nodeLookup?: Record<string, NodeLookupItem>;
  onFocusNode?: (nodeId: string) => void;
}): ReactElement {
  const { messages, pending, sentHistory, model, setModel, send, stop, retry, applyDiff, clearDiff, clearMessages } =
    useAiChat(flowId, onApplySuccess);

  // True while the last assistant message has reasoning tokens but no visible content yet
  const lastMsg = messages.at(-1);
  const thinking = pending && lastMsg?.role === 'assistant' && !!lastMsg.reasoning && !lastMsg.content;

  const handleApplyDiff = useCallback(async (diff: import('./aiPanelTypes').FlowDiff): Promise<{ ok: boolean; error?: string }> => {
    const result = await applyDiff(diff);
    if (result.ok && diff.flow_id) onApplySuccess?.(diff.flow_id);
    return result;
  }, [applyDiff, onApplySuccess]);

  const [mode, setMode] = useState<PanelMode>('sidebar');
  const [draft, setDraft] = useState<string | null>(null);
  const [floatRect, setFloatRect] = useState<Rect>(defaultFloat);
  const floatRectRef = useRef(floatRect);
  floatRectRef.current = floatRect;

  const switchMode = (next: PanelMode) => {
    if (next === 'float') setFloatRect(defaultFloat());
    setMode(next);
    onModeChange?.(next);
  };

  // Queue for triggered messages (e.g. "AI 分析错误") that arrive while pending=true.
  // We stop the current generation, then drain the queue once pending clears.
  const queuedMsgRef = useRef<string | null>(null);

  useEffect(() => {
    if (!pendingMessage) return;
    onClearPendingMessage?.();
    if (!pending) {
      void send(pendingMessage);
    } else {
      queuedMsgRef.current = pendingMessage;
      stop(); // interrupt current generation; pending will go false in finally
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingMessage]);

  // Drain the queued message as soon as pending clears
  useEffect(() => {
    if (!pending && queuedMsgRef.current) {
      const msg = queuedMsgRef.current;
      queuedMsgRef.current = null;
      void send(msg);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending]);

  const header = (
    <div className="flex h-9 shrink-0 items-center gap-2 border-b border-slate-200 px-3">
      <Sparkles className="h-3.5 w-3.5 shrink-0 text-accent" />
      <span className="flex-1 text-[12px] font-semibold text-slate-700">RPA 助手</span>
      <IconButton className="text-slate-400 hover:text-slate-600" label="清空对话" onClick={clearMessages}>
        <Trash2 className="h-3.5 w-3.5" />
      </IconButton>
      {mode === 'sidebar' ? (
        <IconButton label="弹出浮窗" onClick={() => switchMode('float')}>
          <Maximize2 className="h-3.5 w-3.5" />
        </IconButton>
      ) : (
        <IconButton label="收起到侧边栏" onClick={() => switchMode('sidebar')}>
          <Minimize2 className="h-3.5 w-3.5" />
        </IconButton>
      )}
      <IconButton label="关闭 AI 面板" onClick={onClose}>
        <PanelRightClose className="h-3.5 w-3.5" />
      </IconButton>
    </div>
  );

  const body = (
    <>
      <ChatMessages
        messages={messages}
        nodeLookup={nodeLookup}
        onApplyDiff={handleApplyDiff}
        onFocusNode={onFocusNode}
        onPickPrompt={setDraft}
        onRejectDiff={clearDiff}
        onRetry={() => void retry()}
        pending={pending}
      />
      <ChatInput
        autoFocus
        draft={draft}
        history={sentHistory}
        model={model}
        onDraftConsumed={() => setDraft(null)}
        onModelChange={setModel}
        onSend={(text, atts) => void send(text, atts)}
        onStop={stop}
        pending={pending}
        thinking={thinking}
      />
    </>
  );

  // Float mode — portal to document.body; hidden when closed
  if (mode === 'float') {
    if (!open) return <div className="hidden" />;
    return createPortal(
      <div
        className="no-drag fixed z-[200] flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl animate-in zoom-in-95 fade-in duration-150"
        style={{ left: floatRect.x, top: floatRect.y, width: floatRect.w, height: floatRect.h }}
      >
        <ResizeHandles rectRef={floatRectRef} onRectChange={setFloatRect} />
        <DragHeader rectRef={floatRectRef} onRectChange={setFloatRect}>
          {header}
        </DragHeader>
        <div className="flex min-h-0 flex-1 flex-col">{body}</div>
      </div>,
      document.body
    );
  }

  // Sidebar mode — width animates between 0 and w-85
  return (
    <div
      className={cn(
        'flex h-full shrink-0 flex-col overflow-hidden bg-white',
        'transition-[width] duration-200 ease-in-out',
        open ? 'w-85 border-l border-slate-200' : 'w-0',
      )}
    >
      <div className="flex h-full w-85 shrink-0 flex-col">
        {header}
        {body}
      </div>
    </div>
  );
}
