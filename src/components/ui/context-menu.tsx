import * as ContextMenuPrimitive from '@radix-ui/react-context-menu';
import { Check, ChevronRight, Circle } from 'lucide-react';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';

const ContextMenu = ContextMenuPrimitive.Root;
const ContextMenuTrigger = ContextMenuPrimitive.Trigger;
const ContextMenuGroup = ContextMenuPrimitive.Group;
const ContextMenuPortal = ContextMenuPrimitive.Portal;
const ContextMenuSub = ContextMenuPrimitive.Sub;
const ContextMenuRadioGroup = ContextMenuPrimitive.RadioGroup;

function ContextMenuSubTrigger({
  children,
  className,
  inset,
  ...props
}: ComponentProps<typeof ContextMenuPrimitive.SubTrigger> & { inset?: boolean }): ReactElement {
  return (
    <ContextMenuPrimitive.SubTrigger
      className={cn(
        'flex h-8 cursor-default select-none items-center rounded-sm px-2 text-[12px] outline-none focus:bg-slate-100 data-[state=open]:bg-slate-100',
        inset && 'pl-8',
        className
      )}
      data-slot="context-menu-sub-trigger"
      {...props}
    >
      {children}
      <ChevronRight className="ml-auto h-3.5 w-3.5" strokeWidth={1.5} />
    </ContextMenuPrimitive.SubTrigger>
  );
}

function ContextMenuSubContent({ className, ...props }: ComponentProps<typeof ContextMenuPrimitive.SubContent>): ReactElement {
  return (
    <ContextMenuPrimitive.SubContent
      className={cn('z-(--z-dropdown) min-w-[8rem] overflow-hidden rounded-md border border-slate-200 bg-white p-1 text-slate-700 shadow-lg', className)}
      data-slot="context-menu-sub-content"
      {...props}
    />
  );
}

function ContextMenuContent({ className, ...props }: ComponentProps<typeof ContextMenuPrimitive.Content>): ReactElement {
  return (
    <ContextMenuPrimitive.Portal>
      <ContextMenuPrimitive.Content
        className={cn('z-(--z-dropdown) min-w-[8rem] overflow-hidden rounded-lg border border-slate-200 bg-white p-1.5 text-slate-700 shadow-lg', className)}
        data-slot="context-menu-content"
        {...props}
      />
    </ContextMenuPrimitive.Portal>
  );
}

function ContextMenuItem({
  className,
  inset,
  ...props
}: ComponentProps<typeof ContextMenuPrimitive.Item> & { inset?: boolean }): ReactElement {
  return (
    <ContextMenuPrimitive.Item
      className={cn(
        'relative flex h-8 cursor-default select-none items-center rounded-md px-2 text-[12px] outline-none transition-colors focus:bg-slate-100 data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        inset && 'pl-8',
        className
      )}
      data-slot="context-menu-item"
      {...props}
    />
  );
}

function ContextMenuCheckboxItem({
  checked,
  children,
  className,
  ...props
}: ComponentProps<typeof ContextMenuPrimitive.CheckboxItem>): ReactElement {
  return (
    <ContextMenuPrimitive.CheckboxItem
      checked={checked}
      className={cn(
        'relative flex h-8 cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-[12px] outline-none focus:bg-slate-100 data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        className
      )}
      data-slot="context-menu-checkbox-item"
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <ContextMenuPrimitive.ItemIndicator>
          <Check className="h-3.5 w-3.5" strokeWidth={1.5} />
        </ContextMenuPrimitive.ItemIndicator>
      </span>
      {children}
    </ContextMenuPrimitive.CheckboxItem>
  );
}

function ContextMenuRadioItem({ children, className, ...props }: ComponentProps<typeof ContextMenuPrimitive.RadioItem>): ReactElement {
  return (
    <ContextMenuPrimitive.RadioItem
      className={cn(
        'relative flex h-8 cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-[12px] outline-none focus:bg-slate-100 data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        className
      )}
      data-slot="context-menu-radio-item"
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <ContextMenuPrimitive.ItemIndicator>
          <Circle className="h-2 w-2 fill-current" strokeWidth={1.5} />
        </ContextMenuPrimitive.ItemIndicator>
      </span>
      {children}
    </ContextMenuPrimitive.RadioItem>
  );
}

function ContextMenuSeparator({ className, ...props }: ComponentProps<typeof ContextMenuPrimitive.Separator>): ReactElement {
  return <ContextMenuPrimitive.Separator className={cn('-mx-1 my-1 h-px bg-slate-100', className)} data-slot="context-menu-separator" {...props} />;
}

export {
  ContextMenu,
  ContextMenuCheckboxItem,
  ContextMenuContent,
  ContextMenuGroup,
  ContextMenuItem,
  ContextMenuPortal,
  ContextMenuRadioGroup,
  ContextMenuRadioItem,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger
};
