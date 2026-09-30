import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';

export function Label({ className, ...props }: ComponentProps<'label'>): ReactElement {
  return (
    <label
      className={cn('text-[11px] font-medium leading-none text-slate-600 peer-disabled:cursor-not-allowed peer-disabled:opacity-70', className)}
      data-slot="label"
      {...props}
    />
  );
}
