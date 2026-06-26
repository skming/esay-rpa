import * as LabelPrimitive from '@radix-ui/react-label';
import type { ComponentPropsWithoutRef, ElementRef, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';

export const Label = forwardRef<ElementRef<typeof LabelPrimitive.Root>, ComponentPropsWithoutRef<typeof LabelPrimitive.Root>>(
  ({ className, ...props }, ref): ReactElement => (
    <LabelPrimitive.Root
      className={cn('text-[11px] font-medium leading-none text-slate-600 peer-disabled:cursor-not-allowed peer-disabled:opacity-70', className)}
      ref={ref}
      {...props}
    />
  )
);

Label.displayName = LabelPrimitive.Root.displayName;
