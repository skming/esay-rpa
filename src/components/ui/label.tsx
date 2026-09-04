import * as LabelPrimitive from '@radix-ui/react-label';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';

export function Label({ className, ...props }: ComponentProps<typeof LabelPrimitive.Root>): ReactElement {
  return (
    <LabelPrimitive.Root
      className={cn('text-[11px] font-medium leading-none text-slate-600 peer-disabled:cursor-not-allowed peer-disabled:opacity-70', className)}
      data-slot="label"
      {...props}
    />
  );
}
