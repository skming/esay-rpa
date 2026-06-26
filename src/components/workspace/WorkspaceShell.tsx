import type { ReactElement, ReactNode } from 'react';

/* Editorial page frame. The masthead leads with a confident serif title and an
   optional one-line standfirst set on the same baseline, divided by a hairline —
   a magazine title/standfirst, not a stacked eyebrow. Wayfinding lives in the
   NavRail, so the old "运营 · X" kicker is gone. The `icon`/`kicker` props are
   accepted for caller compatibility but no longer rendered. */
export function WorkspaceShell({
  actions,
  children,
  description,
  title,
}: {
  actions?: ReactNode;
  children: ReactNode;
  description?: string;
  icon?: ReactNode;
  kicker?: string;
  title: string;
}): ReactElement {
  return (
    <main className="flex min-h-0 flex-1 flex-col overflow-hidden bg-paper">
      {/* Masthead — title + standfirst on one baseline, structural rule beneath */}
      <header className="shrink-0 border-b border-rule-2 bg-paper">
        <div className="flex min-h-17 items-end justify-between gap-6 px-9 pb-4 pt-6">
          <div className="flex min-w-0 items-baseline gap-3.5">
            <h1 className="shrink-0 font-serif text-[28px] font-medium leading-none tracking-[-0.015em] text-ink">
              {title}
            </h1>
            {description !== undefined && description !== '' && (
              <p className="rule-v hidden min-w-0 max-w-[46ch] truncate pl-3.5 text-[12px] leading-none text-ink-3 sm:block">
                {description}
              </p>
            )}
          </div>
          {actions !== undefined && (
            <div className="flex shrink-0 items-center gap-2 pb-0.5">{actions}</div>
          )}
        </div>
      </header>

      {/* Scrollable content field */}
      <div className="no-scrollbar min-h-0 flex-1 overflow-auto">
        <div className="mx-auto grid w-full max-w-310 gap-7 px-9 pb-12 pt-8">
          {children}
        </div>
      </div>
    </main>
  );
}
