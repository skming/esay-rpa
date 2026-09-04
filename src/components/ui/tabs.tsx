import * as TabsPrimitive from '@radix-ui/react-tabs';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';

const Tabs = TabsPrimitive.Root;

function TabsList({ className, ...props }: ComponentProps<typeof TabsPrimitive.List>): ReactElement {
  return (
    <TabsPrimitive.List
      className={cn('inline-flex items-center text-[11px] font-medium text-slate-500', className)}
      data-slot="tabs-list"
      {...props}
    />
  );
}

function TabsTrigger({ className, ...props }: ComponentProps<typeof TabsPrimitive.Trigger>): ReactElement {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        'relative inline-flex items-center justify-center whitespace-nowrap transition hover:text-ink focus-visible:ring-2 focus-visible:ring-rule disabled:pointer-events-none disabled:opacity-50 data-[state=active]:text-ink data-[state=active]:after:absolute data-[state=active]:after:inset-x-0 data-[state=active]:after:bottom-0 data-[state=active]:after:h-0.5 data-[state=active]:after:bg-accent',
        className
      )}
      data-slot="tabs-trigger"
      {...props}
    />
  );
}

function TabsContent({ className, ...props }: ComponentProps<typeof TabsPrimitive.Content>): ReactElement {
  return <TabsPrimitive.Content className={cn('focus-visible:outline-none', className)} data-slot="tabs-content" {...props} />;
}

export { Tabs, TabsContent, TabsList, TabsTrigger };
