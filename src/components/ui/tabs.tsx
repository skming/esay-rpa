import { Tabs as TabsPrimitive } from '@base-ui/react/tabs';
import type { ReactElement } from 'react';

import { cn } from '../../lib/utils';

function Tabs(props: TabsPrimitive.Root.Props): ReactElement {
  return <TabsPrimitive.Root data-slot="tabs" {...props} />;
}

function TabsList({ className, ...props }: TabsPrimitive.List.Props): ReactElement {
  return (
    <TabsPrimitive.List
      className={cn('inline-flex items-center text-[11px] font-medium text-slate-500', className)}
      data-slot="tabs-list"
      {...props}
    />
  );
}

function TabsTrigger({ className, ...props }: TabsPrimitive.Tab.Props): ReactElement {
  return (
    <TabsPrimitive.Tab
      className={cn(
        'relative inline-flex items-center justify-center whitespace-nowrap transition hover:text-ink focus-visible:ring-2 focus-visible:ring-rule disabled:pointer-events-none disabled:opacity-50 data-active:text-ink data-active:after:absolute data-active:after:inset-x-0 data-active:after:bottom-0 data-active:after:h-0.5 data-active:after:bg-accent',
        className
      )}
      data-slot="tabs-trigger"
      {...props}
    />
  );
}

function TabsContent({ className, ...props }: TabsPrimitive.Panel.Props): ReactElement {
  return <TabsPrimitive.Panel className={cn('focus-visible:outline-none', className)} data-slot="tabs-content" {...props} />;
}

export { Tabs, TabsContent, TabsList, TabsTrigger };
