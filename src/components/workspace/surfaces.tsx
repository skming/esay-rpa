import { ArrowUpRight, Loader2, Search, X } from 'lucide-react';
import type { ReactElement, ReactNode } from 'react';

import { cn } from '../../lib/utils';
import { Input } from '../ui/input';

export type StatusTone = 'live' | 'success' | 'warning' | 'error' | 'idle';

const STATE: Record<StatusTone, { dot: string; text: string; live?: boolean }> = {
  live: { dot: 'bg-live', text: 'text-accent-strong', live: true },
  success: { dot: 'bg-emerald-500', text: 'text-emerald-700' },
  warning: { dot: 'bg-amber-500', text: 'text-amber-700' },
  error: { dot: 'bg-red-500', text: 'text-red-600' },
  idle: { dot: 'bg-ink-4', text: 'text-ink-3' },
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
    <section className={cn('@container p-1', SURFACE)}>
      <div className="grid grid-cols-1 gap-px bg-rule @min-sm:grid-cols-2 @min-3xl:grid-cols-4">{children}</div>
    </section>
  );
}

export function HealthSignal({
  detail,
  icon,
  label,
  loading = false,
  onClick,
  selected,
  state,
  value,
}: {
  detail: ReactNode;
  icon: ReactElement;
  label: string;
  loading?: boolean;
  onClick?: () => void;
  selected?: boolean;
  state?: StatusTone;
  value: ReactNode;
}): ReactElement {
  const cfg = loading || state === undefined ? null : STATE[state];
  const Container = onClick === undefined ? 'div' : 'button';
  return (
    <Container
      aria-busy={loading || undefined}
      aria-pressed={onClick === undefined ? undefined : selected}
      className={cn('group flex min-w-0 flex-col items-stretch justify-start bg-surface px-3 py-2.5 text-left @min-sm:px-4', onClick !== undefined && 'disabled:cursor-wait')}
      disabled={onClick === undefined ? undefined : loading}
      onClick={onClick}
      type={onClick === undefined ? undefined : 'button'}
    >
      <div className="flex min-w-0 items-center gap-2 text-[11px] font-medium leading-4 text-ink-3">
        <span aria-hidden="true" className={cn('shrink-0 text-ink-4', cfg?.text)}>{icon}</span>
        <span className={cn('min-w-0 flex-1 truncate', onClick !== undefined && 'transition-colors group-hover:text-ink group-focus-visible:text-ink')}>{label}</span>
        {cfg !== null && (
          <span aria-hidden="true" className={cn('h-1.5 w-1.5 shrink-0 rounded-full', cfg.dot, cfg.live && 'live-dot')} />
        )}
        {onClick !== undefined && (
          <ArrowUpRight
            aria-hidden="true"
            className={cn('h-3 w-3 shrink-0 text-ink-4 transition-opacity motion-reduce:transition-none', loading ? 'opacity-0' : 'opacity-60 group-hover:opacity-100 group-focus-visible:opacity-100')}
            strokeWidth={1.5}
          />
        )}
      </div>
      <div className={cn('figure mt-1.5 min-h-5 wrap-break-word text-[17px] leading-5 text-ink', state !== 'idle' && cfg?.text)}>
        {loading ? '…' : value}
      </div>
      <div className="mt-1 min-h-3.5 line-clamp-2 wrap-break-word text-[10.5px] leading-3.5 text-ink-3">{loading ? '正在读取状态' : detail}</div>
    </Container>
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

/** Empty state — teaches the surface, never just "nothing here". */
export function SurfaceEmpty({
  action, icon, title, hint,
}: { action?: ReactNode; icon?: ReactElement; title: string; hint?: string }): ReactElement {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon !== undefined && <span className="text-ink-4">{icon}</span>}
      <p className="text-[12.5px] font-medium text-ink-2">{title}</p>
      {hint !== undefined && <p className="max-w-[42ch] text-[11.5px] leading-relaxed text-ink-3">{hint}</p>}
      {action !== undefined && <div className="mt-2 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  );
}

/** 首屏加载态。与空态同尺寸，避免加载完成时布局跳动。 */
export function SurfaceLoading({ label = '加载中…' }: { label?: string }): ReactElement {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center" role="status" aria-live="polite">
      <Loader2 className="h-5 w-5 animate-spin text-ink-4" strokeWidth={1.5} />
      <p className="text-[12px] text-ink-3">{label}</p>
    </div>
  );
}

/** 独占一屏的空态（带外框）。 */
export function EmptyPanel(props: {
  action?: ReactNode;
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

/** 首屏加载态（带外框），与 EmptyPanel 同壳，数据到位前后不跳版。 */
export function LoadingPanel({ label }: { label?: string }): ReactElement {
  return (
    <div className={SURFACE}>
      <SurfaceLoading label={label} />
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

/** 分段筛选 + 搜索的横条。任务中心与调度中心共用同一套，勿再各写各的高度、间距与选中态。 */
export function FilterBar<T extends string>({
  counts, filters, onQueryChange, onValueChange, query, searchLabel, searchPlaceholder, value,
}: {
  counts: Record<T, number>;
  filters: ReadonlyArray<{ label: string; value: T }>;
  onQueryChange: (query: string) => void;
  onValueChange: (value: T) => void;
  query: string;
  searchLabel: string;
  searchPlaceholder: string;
  value: T;
}): ReactElement {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-rule bg-surface p-2 shadow-xs">
      <div className="flex min-w-0 flex-[1_1_520px] flex-wrap items-center gap-1" aria-label="状态筛选" role="group">
        {filters.map((item) => (
          <button
            aria-pressed={value === item.value}
            className={cn(
              'flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-[11px] font-medium transition-colors',
              value === item.value
                ? 'bg-accent-soft text-accent-strong'
                : 'text-ink-3 hover:bg-paper-sunk hover:text-ink-2',
            )}
            key={item.value}
            onClick={() => onValueChange(item.value)}
            type="button"
          >
            {item.label}
            <span className={cn(
              'font-mono text-[10px] tabular-nums',
              value === item.value ? 'text-accent-strong' : 'text-ink-3',
            )}>
              {counts[item.value]}
            </span>
          </button>
        ))}
      </div>
      <SearchField
        className="max-w-none flex-[1_1_220px]"
        label={searchLabel}
        onChange={onQueryChange}
        placeholder={searchPlaceholder}
        value={query}
      />
    </div>
  );
}
