import { GripHorizontal } from 'lucide-react';
import type { PointerEvent, ReactElement } from 'react';

export function ResizeHandle({ onPointerDown }: { onPointerDown: (event: PointerEvent<HTMLDivElement>) => void }): ReactElement {
  return (
    <div
      aria-label="调整底部面板高度"
      className="group absolute inset-x-0 -top-1 z-10 flex h-2 cursor-row-resize items-center justify-center"
      onPointerDown={onPointerDown}
      role="separator"
    >
      <div className="flex h-1 w-16 items-center justify-center rounded-full bg-transparent transition group-hover:bg-blue-100">
        <GripHorizontal className="h-3.5 w-3.5 text-slate-300 group-hover:text-blue-500" strokeWidth={1.5} />
      </div>
    </div>
  );
}
