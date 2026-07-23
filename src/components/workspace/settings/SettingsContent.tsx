import type { ReactElement, ReactNode } from 'react';

export function SettingsContent({
  action,
  children,
  icon,
  title,
}: {
  action?: ReactNode;
  children: ReactNode;
  icon: ReactElement;
  title: string;
}): ReactElement {
  return (
    <section className="min-h-full">
      <header className="flex h-11 items-center justify-between border-b border-rule px-5 sticky top-0 bg-surface">
        <div className="flex items-center gap-2 text-ink-3">
          {icon}
          <span className="text-[12px] font-semibold text-ink-2">{title}</span>
        </div>
        {action}
      </header>
      <div className="p-5">{children}</div>
    </section>
  );
}
