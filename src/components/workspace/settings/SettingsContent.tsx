import type { ReactElement, ReactNode } from 'react';
import { RotateCcw } from 'lucide-react';
import { Button } from '../../ui/button';

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

export function SettingsLoadError({ message, onRetry }: { message: string; onRetry: () => void }): ReactElement {
  return (
    <div className="flex items-center justify-between gap-4 rounded-md border border-red-200 bg-red-50/70 px-3 py-2" role="alert">
      <p className="text-[11px] text-red-700">{message}</p>
      <Button className="shrink-0" onClick={onRetry} size="sm" variant="ghost">
        <RotateCcw className="h-3.5 w-3.5" />
        重试
      </Button>
    </div>
  );
}
