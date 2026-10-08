import type { ReactElement, ReactNode } from 'react';

/* Desktop tool page frame. A compact header row — title, inline description,
   actions on the trailing edge — over a full-width content field. The register
   is Figma-panel / Linear, not a web page: no centered magazine column, no
   oversized masthead. Wayfinding lives in the NavRail. */
export function WorkspaceShell({
  actions,
  children,
  description,
  fill = false,
  title,
}: {
  actions?: ReactNode;
  children: ReactNode;
  description?: string;
  /** 内容自己管滚动，避免出现两条滚动条。 */
  fill?: boolean;
  title: string;
}): ReactElement {
  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-paper">
      <header className="flex h-12 shrink-0 items-center justify-between gap-x-6 border-b border-rule bg-surface px-4 sm:px-6">
        <div className="flex min-w-0 shrink-0 items-baseline gap-3">
          <h1 className="shrink-0 text-[14px] font-semibold leading-none tracking-[-0.01em] text-ink">
            {title}
          </h1>
          {description !== undefined && description !== '' && (
            <p className="hidden min-w-0 max-w-[52ch] truncate text-[12px] leading-none text-ink-3 sm:block">
              {description}
            </p>
          )}
        </div>
        {actions !== undefined && (
          <div className="no-scrollbar flex min-w-0 items-center gap-2 overflow-x-auto">{actions}</div>
        )}
      </header>

      {fill ? (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col px-4 pb-6 pt-5 sm:px-6">{children}</div>
      ) : (
        <div className="no-scrollbar min-h-0 flex-1 overflow-auto">
          <div className="grid min-w-0 w-full gap-5 px-4 pb-10 pt-5 sm:px-6">
            {children}
          </div>
        </div>
      )}
    </main>
  );
}
