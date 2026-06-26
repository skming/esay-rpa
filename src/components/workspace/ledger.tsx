/* ── Operational surface primitives ───────────────────────────────────────────
   Precise-friendly RPA register: soft-shadowed white cards, Inter data figures,
   a rationed indigo brand with semantic state colors. The earlier editorial
   "Ledger" voice (serif figures, hairline-only bands, mono-uppercase labels) has
   been retired so operations match the Studio canvas. Export names are kept for
   compatibility with existing consumers. */
import type { ReactElement, ReactNode } from 'react';

import { cn } from '../../lib/utils';

/* ── Semantic state — color always paired with a label (colorblind-safe) ─────── */

export type LedgerState = 'live' | 'success' | 'warning' | 'error' | 'idle';

const STATE: Record<LedgerState, { dot: string; text: string; live?: boolean }> = {
  live: { dot: 'bg-live', text: 'text-live', live: true },
  success: { dot: 'bg-emerald-500', text: 'text-emerald-600' },
  warning: { dot: 'bg-amber-500', text: 'text-amber-600' },
  error: { dot: 'bg-red-500', text: 'text-red-600' },
  idle: { dot: 'bg-slate-300', text: 'text-slate-500' },
};

/** Inline state marker: a small dot + label. The dot pulses only when live. */
export function StateTag({
  state, label, className,
}: { state: LedgerState; label: string; className?: string }): ReactElement {
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

/* ── Stat band — a white card holding a row of KPI figures, hairline-divided ─── */

export function StatBand({ children }: { children: ReactNode }): ReactElement {
  return (
    <section className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] overflow-hidden rounded-xl border border-rule bg-surface shadow-sm">
      {children}
    </section>
  );
}

/** One figure in the band. `tone` lets the running figure carry the live blue. */
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
    <div className={cn('px-5 py-4', !first && 'rule-v')}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-medium text-ink-3">{label}</span>
        {note !== undefined && (
          <span className="font-mono text-[10px] tabular-nums text-ink-4">{note}</span>
        )}
      </div>
      <div
        className={cn(
          'figure mt-2.5 text-[30px] leading-none',
          tone === 'live' ? 'text-live' : 'text-ink',
        )}
      >
        {value}
      </div>
      {state !== undefined && <div className="mt-2.5">{state}</div>}
    </div>
  );
}

/* ── Panel — white card, soft shadow, icon + section label header ────────────── */

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
    <section className={cn('rounded-xl border border-rule bg-surface shadow-sm', className)}>
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
export function LedgerEmpty({
  icon, title, hint,
}: { icon?: ReactElement; title: string; hint?: string }): ReactElement {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon !== undefined && <span className="text-ink-4">{icon}</span>}
      <p className="text-[12.5px] font-medium text-ink-2">{title}</p>
      {hint !== undefined && <p className="max-w-[42ch] text-[11.5px] leading-relaxed text-ink-4">{hint}</p>}
    </div>
  );
}
