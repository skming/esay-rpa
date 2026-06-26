import type { ReactElement } from 'react';

export function LabelLike({ children, text }: { children: ReactElement; text: string }): ReactElement {
  return (
    <div>
      <div className="mb-1 text-[11px] font-medium text-slate-600">{text}</div>
      {children}
    </div>
  );
}
