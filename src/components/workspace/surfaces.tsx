import { Search, X } from 'lucide-react';
import type { ReactElement, ReactNode } from 'react';

import { cn } from '../../lib/utils';
import { Input } from '../ui/input';

export type StatusTone = 'live' | 'success' | 'warning' | 'error' | 'idle';

const STATE: Record<StatusTone, { dot: string; text: string; live?: boolean }> = {
  live: { dot: 'bg-live', text: 'text-accent-strong', live: true },
  success: { dot: 'bg-emerald-500', text: 'text-emerald-700' },
  warning: { dot: 'bg-amber-500', text: 'text-amber-700' },
  error: { dot: 'bg-red-500', text: 'text-red-600' },
  idle: { dot: 'bg-slate-300', text: 'text-slate-500' },
};

/** Inline state marker: a small dot + label. The dot pulses only when live. */
export function StateTag({
  state, label, className,
}: { state: StatusTone; label: string; className?: string }): ReactElement {
  const cfg = STATE[state];
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <span className={cn('grid h-2 w-2 place-items-center', cfg.live && 'live-dot')}>
        <span className={cn('h-1.5 w-1.5 rounded-full', cfg.dot)} />
      </span>
      <span className={cn('text-[11px] font-medium', cfg.text)}>{label}</span>
    </span>
  );
}

/** 页面级容器的统一外观，调用方不要各写各的半径与投影。 */
export const SURFACE = 'rounded-xl border border-rule bg-surface shadow-xs';

export function HealthRail({ children }: { children: ReactNode }): ReactElement {
  return (
    <section className={cn('grid overflow-hidden divide-x divide-rule', SURFACE)} style={{ gridTemplateColumns: `repeat(${Array.isArray(children) ? children.length : 1}, minmax(0, 1fr))` }}>
      {children}
    </section>
  );
}

export function HealthSignal({
  detail,
  icon,
  label,
  state,
  value,
}: {
  detail: ReactNode;
  icon: ReactElement;
  label: string;
  state?: StatusTone;
  value: ReactNode;
}): ReactElement {
  const cfg = state === undefined ? null : STATE[state];
  return (
    <div className="min-w-0 px-4 py-3.5">
      <div className="flex items-center gap-2 text-[11px] font-medium text-ink-3">
        <span className={cn('text-ink-4', cfg?.text)}>{icon}</span>
        <span className="truncate">{label}</span>
        {cfg !== null && (
          <span className={cn('ml-auto h-1.5 w-1.5 shrink-0 rounded-full', cfg.dot, cfg.live && 'live-dot')} />
        )}
      </div>
      <div className={cn('mt-2 truncate text-[16px] font-semibold leading-none tracking-[-0.02em] text-ink', cfg?.text)}>
        {value}
      </div>
      <div className="mt-1.5 truncate text-[10.5px] text-ink-3">{detail}</div>
    </div>
  );
}

// 单行状态条而非 KPI 卡片网格：桌面工具的指标是随时瞥一眼的参考值，不该占据首屏主视觉
export function StatBand({ children }: { children: ReactNode }): ReactElement {
  return (
    <section className={cn('flex min-h-11 flex-wrap items-stretch overflow-hidden', SURFACE)}>
      {children}
    </section>
  );
}

/** One inline figure in the strip. `tone` lets the running figure carry the live blue. */
export function Figure({
  label, value, note, tone = 'ink', state, first,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: 'ink' | 'live';
  state?: ReactElement;
  first?: boolean;
}): ReactElement {
  return (
    <div className={cn('flex items-center gap-2 px-4 py-2.5', !first && 'rule-v')}>
      <span className="text-[11px] font-medium leading-none text-ink-3">{label}</span>
      <span
        className={cn(
          'figure text-[15px] leading-none',
          tone === 'live' ? 'text-live' : 'text-ink',
        )}
      >
        {value}
      </span>
      {note !== undefined && (
        <span className="font-mono text-[10px] leading-none tabular-nums text-ink-3">{note}</span>
      )}
      {state !== undefined && state}
    </div>
  );
}

export function Panel({
  label, icon, action, children, className, bodyClassName,
}: {
  label?: string;
  icon?: ReactElement;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}): ReactElement {
  return (
    <section className={cn(SURFACE, className)}>
      {label !== undefined && (
        <header className="flex h-11 items-center justify-between border-b border-rule px-5">
          <div className="flex items-center gap-2 text-ink-3">
            {icon}
            <span className="text-[12px] font-semibold text-ink-2">{label}</span>
          </div>
          {action}
        </header>
      )}
      <div className={cn('p-5', bodyClassName)}>{children}</div>
    </section>
  );
}

/** Key/value row — the line item inside a panel. */
export function KeyRow({
  label, value, mono, last,
}: { label: string; value: ReactNode; mono?: boolean; last?: boolean }): ReactElement {
  return (
    <div className={cn('flex items-center justify-between py-2.5', !last && 'border-b border-rule')}>
      <span className="text-[12px] text-ink-3">{label}</span>
      <span className={cn('text-[12.5px] font-medium text-ink-2', mono && 'font-mono text-[11.5px] tabular-nums')}>
        {value}
      </span>
    </div>
  );
}

/** Empty state — teaches the surface, never just "nothing here". */
export function SurfaceEmpty({
  icon, title, hint,
}: { icon?: ReactElement; title: string; hint?: string }): ReactElement {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon !== undefined && <span className="text-ink-4">{icon}</span>}
      <p className="text-[12.5px] font-medium text-ink-2">{title}</p>
      {hint !== undefined && <p className="max-w-[42ch] text-[11.5px] leading-relaxed text-ink-3">{hint}</p>}
    </div>
  );
}

/** 独占一屏的空态（带外框）。 */
export function EmptyPanel(props: {
  icon?: ReactElement;
  title: string;
  hint?: string;
}): ReactElement {
  return (
    <div className={SURFACE}>
      <SurfaceEmpty {...props} />
    </div>
  );
}

/** 面板内的事实项。不加边框底色，避免卡片里再套卡片。 */
export function Fact({
  label, value, mono, className,
}: { label: string; value: ReactNode; mono?: boolean; className?: string }): ReactElement {
  return (
    <div className={cn('min-w-0', className)}>
      <div className="text-[11px] font-medium leading-none text-ink-3">{label}</div>
      <div
        className={cn(
          'mt-1.5 truncate text-[12px] font-medium text-ink-2',
          mono === true && 'font-mono text-[11.5px] tabular-nums',
        )}
      >
        {value}
      </div>
    </div>
  );
}

/** 刻意不覆写 Input 自带的 focus-visible 焦点环：各页各写一套 focus: 会让鼠标点击也描边，且焦点色互不相同。 */
export function SearchField({
  label, onChange, placeholder, value, className,
}: {
  label: string;
  onChange: (value: string) => void;
  placeholder: string;
  value: string;
  className?: string;
}): ReactElement {
  return (
    <div className={cn('relative min-w-0 max-w-sm flex-1', className)}>
      <Search
        className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-4"
        strokeWidth={1.5}
      />
      <Input
        aria-label={label}
        className="h-9 rounded-md border-rule-2 bg-surface pl-9 pr-8 text-[12px] text-ink-2 placeholder:text-ink-3"
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        value={value}
      />
      {value !== '' && (
        <button
          aria-label="清除搜索"
          className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-ink-4 transition-colors hover:bg-paper-sunk hover:text-ink-2"
          onClick={() => onChange('')}
          type="button"
        >
          <X className="h-3.5 w-3.5" strokeWidth={1.5} />
        </button>
      )}
    </div>
  );
}
