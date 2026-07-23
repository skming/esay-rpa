import * as CheckboxPrimitive from '@radix-ui/react-checkbox';
import { Check } from 'lucide-react';
import type { ComponentPropsWithoutRef, ElementRef, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';

export const Checkbox = forwardRef<ElementRef<typeof CheckboxPrimitive.Root>, ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root>>(
  ({ className, ...props }, ref): ReactElement => (
    <CheckboxPrimitive.Root
      className={cn(
        'peer h-3.5 w-3.5 shrink-0 rounded-sm border border-rule-2 bg-surface outline-none transition-colors focus-visible:ring-2 focus-visible:ring-accent-soft disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-accent data-[state=checked]:bg-accent data-[state=checked]:text-white',
        className
      )}
      ref={ref}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="flex items-center justify-center text-current">
        <Check className="h-3 w-3" strokeWidth={3} />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
);

Checkbox.displayName = CheckboxPrimitive.Root.displayName;
