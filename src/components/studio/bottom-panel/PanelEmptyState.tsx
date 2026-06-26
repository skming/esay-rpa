import type { LucideIcon } from 'lucide-react';
import type { ReactElement } from 'react';

export function PanelEmptyState({ icon: Icon, text, tone = 'text-slate-300' }: { icon: LucideIcon; text: string; tone?: string }): ReactElement {
  return (
    <div className="grid h-full place-items-center text-center text-[11px] text-slate-400">
      <div>
        <Icon className={`mx-auto mb-2 h-8 w-8 ${tone}`} strokeWidth={1.5} />
        {text}
      </div>
    </div>
  );
}
