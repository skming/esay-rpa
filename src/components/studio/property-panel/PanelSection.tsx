import type { ReactElement, ReactNode } from 'react';

export function PanelSection({ children, title }: { children: ReactNode; title: string }): ReactElement {
  return (
    <section className="mb-4">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="font-mono text-[9.5px] uppercase leading-none tracking-widest text-slate-500">{title}</h3>
        <span className="h-px flex-1 bg-rule" />
      </div>
      <div className="space-y-2">{children}</div>
    </section>
  );
}
