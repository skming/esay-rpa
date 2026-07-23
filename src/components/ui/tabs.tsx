import * as TabsPrimitive from '@radix-ui/react-tabs';
import type { ComponentPropsWithoutRef, ElementRef, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';

const Tabs = TabsPrimitive.Root;

const TabsList = forwardRef<ElementRef<typeof TabsPrimitive.List>, ComponentPropsWithoutRef<typeof TabsPrimitive.List>>(
  ({ className, ...props }, ref): ReactElement => (
    <TabsPrimitive.List className={cn('inline-flex items-center text-[11px] font-medium text-slate-500', className)} ref={ref} {...props} />
  )
);
TabsList.displayName = TabsPrimitive.List.displayName;

const TabsTrigger = forwardRef<ElementRef<typeof TabsPrimitive.Trigger>, ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>>(
  ({ className, ...props }, ref): ReactElement => (
    <TabsPrimitive.Trigger
      className={cn(
        'relative inline-flex items-center justify-center whitespace-nowrap transition hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rule disabled:pointer-events-none disabled:opacity-50 data-[state=active]:text-ink data-[state=active]:after:absolute data-[state=active]:after:inset-x-0 data-[state=active]:after:bottom-0 data-[state=active]:after:h-0.5 data-[state=active]:after:bg-accent',
        className
      )}
      ref={ref}
      {...props}
    />
  )
);
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;

const TabsContent = forwardRef<ElementRef<typeof TabsPrimitive.Content>, ComponentPropsWithoutRef<typeof TabsPrimitive.Content>>(
  ({ className, ...props }, ref): ReactElement => (
    <TabsPrimitive.Content className={cn('focus-visible:outline-none', className)} ref={ref} {...props} />
  )
);
TabsContent.displayName = TabsPrimitive.Content.displayName;

export { Tabs, TabsContent, TabsList, TabsTrigger };
