import { ChevronDown, ChevronRight } from 'lucide-react';
import type { ReactNode, ReactElement } from 'react';
import { useState, useRef, useEffect } from 'react';
import { cn } from '../../lib/utils';

export function Collapsible({
  title,
  children,
  defaultOpen = false,
  badge,
  className,
  chevronVariant = 'down',
}: {
  title: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  badge?: ReactNode;
  className?: string;
  chevronVariant?: 'down' | 'right';
}): ReactElement {
  const [open, setOpen] = useState(defaultOpen);
  const contentRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | 'auto'>(defaultOpen ? 'auto' : 0);

  // CSS 无法过渡 height:auto，故先设像素高度触发动画，200ms 后（须与 duration-200 一致）再切回 auto
  useEffect(() => {
    const el = contentRef.current;
    if (!el) return;
    if (open) {
      const scrollHeight = el.scrollHeight;
      setHeight(scrollHeight);
      const id = setTimeout(() => setHeight('auto'), 200);
      return () => clearTimeout(id);
    } else {
      setHeight(el.scrollHeight);
      const id = setTimeout(() => setHeight(0), 10);
      return () => clearTimeout(id);
    }
  }, [open]);

  return (
    <div className={cn('rounded-lg border border-slate-200', className)}>
      <button
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
        onClick={() => setOpen((o) => !o)}
        type="button"
      >
        <span className="min-w-0 flex-1 text-[11px] font-medium text-slate-700">{title}</span>
        {badge}
        {chevronVariant === 'right' ? (
          <ChevronRight
            className={cn('h-3.5 w-3.5 shrink-0 text-slate-500 transition-transform duration-200', open && 'rotate-90')}
            strokeWidth={1.5}
          />
        ) : (
          <ChevronDown
            className={cn('h-3.5 w-3.5 shrink-0 text-slate-500 transition-transform duration-200', open && 'rotate-180')}
            strokeWidth={1.5}
          />
        )}
      </button>
      <div
        ref={contentRef}
        style={{ height: height === 'auto' ? 'auto' : `${height}px` }}
        className="overflow-hidden transition-[height] duration-200"
      >
        <div className="border-t border-slate-100 px-3 pb-3 pt-2">
          {children}
        </div>
      </div>
    </div>
  );
}
