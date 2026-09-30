import { useEffect, useRef, useState, type PointerEvent, type ReactElement } from 'react';

import { cn } from '../../lib/utils';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { AiAssistantMark } from './AiAssistantMark';
import {
  clampAiAssistantPosition,
  parseAiAssistantPosition,
  snapAiAssistantPosition,
  type AiAssistantPosition,
} from './aiAssistantPosition';

const POSITION_STORAGE_KEY = 'rpa-studio.ai-assistant-position';
const DRAG_THRESHOLD = 4;

type DragState = {
  pointerId: number;
  startClientX: number;
  startClientY: number;
  startLeft: number;
  startTop: number;
  moved: boolean;
  position: AiAssistantPosition;
};

function loadPosition(): AiAssistantPosition | null {
  if (typeof window === 'undefined') return null;
  try {
    const saved = parseAiAssistantPosition(window.localStorage.getItem(POSITION_STORAGE_KEY));
    return saved === null
      ? null
      : clampAiAssistantPosition(saved, { width: window.innerWidth, height: window.innerHeight });
  } catch {
    return null;
  }
}

function savePosition(position: AiAssistantPosition): void {
  try {
    window.localStorage.setItem(POSITION_STORAGE_KEY, JSON.stringify(position));
  } catch {
    // 持久化失败不影响本次拖动。
  }
}

export function AiAssistantFab({
  busy,
  hidden,
  onClick,
}: {
  busy: boolean;
  hidden: boolean;
  onClick: () => void;
}): ReactElement {
  const label = busy ? 'RPA 助手处理中' : 'RPA 助手';
  const [position, setPosition] = useState<AiAssistantPosition | null>(loadPosition);
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<DragState | null>(null);
  const suppressClickRef = useRef(false);

  useEffect(() => {
    const keepVisible = (): void => {
      setPosition((current) => current === null
        ? null
        : clampAiAssistantPosition(current, { width: window.innerWidth, height: window.innerHeight }));
    };
    window.addEventListener('resize', keepVisible);
    return () => window.removeEventListener('resize', keepVisible);
  }, []);

  const handlePointerDown = (event: PointerEvent<HTMLButtonElement>): void => {
    if (event.button !== 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    dragRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startLeft: rect.left,
      startTop: rect.top,
      moved: false,
      position: { x: rect.left, y: rect.top },
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event: PointerEvent<HTMLButtonElement>): void => {
    const drag = dragRef.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - drag.startClientX;
    const deltaY = event.clientY - drag.startClientY;
    if (!drag.moved && Math.hypot(deltaX, deltaY) < DRAG_THRESHOLD) return;
    drag.moved = true;
    suppressClickRef.current = true;
    setDragging(true);
    drag.position = clampAiAssistantPosition(
      { x: drag.startLeft + deltaX, y: drag.startTop + deltaY },
      { width: window.innerWidth, height: window.innerHeight },
    );
    setPosition(drag.position);
  };

  const finishDrag = (event: PointerEvent<HTMLButtonElement>): void => {
    const drag = dragRef.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (drag.moved) {
      const snapped = snapAiAssistantPosition(
        drag.position,
        { width: window.innerWidth, height: window.innerHeight },
      );
      setPosition(snapped);
      savePosition(snapped);
      window.setTimeout(() => { suppressClickRef.current = false; }, 0);
    }
    dragRef.current = null;
    setDragging(false);
  };

  return (
    <Tooltip>
      <TooltipTrigger render={<button
          // 面板开着时按钮虽然看不见，但仍在 Tab 焦点序列里，会让键盘用户按到一个"隐形按钮"
          aria-hidden={hidden}
          aria-label={label}
          className={cn(
            'group fixed z-(--z-dropdown) flex h-11 w-11 touch-none select-none items-center justify-center rounded-full',
            position === null ? 'bottom-16 right-5' : 'left-0 top-0',
            'bg-transparent text-(--color-accent) transition-[color,opacity,transform,left,top] duration-200',
            'cursor-grab hover:text-(--color-accent-strong) active:cursor-grabbing',
            // 焦点态不在这里写：styles.css 的无 layer 全局规则给出唯一的 2px accent 轮廓
            busy && 'text-live',
            dragging && 'ai-assistant-fab-dragging',
            // 面板打开时用 pointer-events-none 而非卸载，让淡出动画能跑完
            hidden && 'pointer-events-none scale-90 opacity-0',
          )}
          onClick={() => {
            if (suppressClickRef.current) {
              suppressClickRef.current = false;
              return;
            }
            onClick();
          }}
          onPointerCancel={finishDrag}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={finishDrag}
          style={position === null ? undefined : { left: position.x, top: position.y }}
          tabIndex={hidden ? -1 : 0}
          type="button"
        />}>
          <AiAssistantMark busy={busy} />
      </TooltipTrigger>
      <TooltipContent side="left">
        {label}
        <span className="ml-1.5 text-slate-500">⌘J</span>
      </TooltipContent>
    </Tooltip>
  );
}
