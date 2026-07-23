import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import type { ComponentPropsWithoutRef, ElementRef, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';

const TooltipProvider = TooltipPrimitive.Provider;
const Tooltip = TooltipPrimitive.Root;
const TooltipTrigger = TooltipPrimitive.Trigger;

const TooltipContent = forwardRef<ElementRef<typeof TooltipPrimitive.Content>, ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>>(
  ({ className, sideOffset = 6, ...props }, ref): ReactElement => (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        className={cn('z-(--z-dropdown) rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-700 shadow-lg', className)}
        ref={ref}
        sideOffset={sideOffset}
        {...props}
      />
    </TooltipPrimitive.Portal>
  )
);
TooltipContent.displayName = TooltipPrimitive.Content.displayName;

export { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger };
