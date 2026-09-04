import * as CheckboxPrimitive from '@radix-ui/react-checkbox';
import { Check } from 'lucide-react';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';

export function Checkbox({ className, ...props }: ComponentProps<typeof CheckboxPrimitive.Root>): ReactElement {
  return (
    <CheckboxPrimitive.Root
      className={cn(
        'peer h-3.5 w-3.5 shrink-0 rounded-sm border border-rule-2 bg-surface outline-none transition-colors focus-visible:ring-2 focus-visible:ring-accent-soft disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-accent data-[state=checked]:bg-accent data-[state=checked]:text-white',
        className
      )}
      data-slot="checkbox"
      {...props}
    >
      <CheckboxPrimitive.Indicator className="flex items-center justify-center text-current" data-slot="checkbox-indicator">
        <Check className="h-3 w-3" strokeWidth={3} />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}
