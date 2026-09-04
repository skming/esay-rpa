import * as SwitchPrimitive from '@radix-ui/react-switch';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';

export function Switch({ className, ...props }: ComponentProps<typeof SwitchPrimitive.Root>): ReactElement {
  return (
    <SwitchPrimitive.Root
      className={cn(
        'peer inline-flex h-4 w-7 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:ring-2 focus-visible:ring-accent-soft disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-accent data-[state=unchecked]:bg-slate-300',
        className
      )}
      data-slot="switch"
      {...props}
    >
      <SwitchPrimitive.Thumb
        className="pointer-events-none block h-3 w-3 rounded-full bg-white shadow-sm transition-transform data-[state=checked]:translate-x-3 data-[state=unchecked]:translate-x-0"
        data-slot="switch-thumb"
      />
    </SwitchPrimitive.Root>
  );
}
