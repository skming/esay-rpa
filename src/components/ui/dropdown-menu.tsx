import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu';
import { Check, ChevronRight, Circle } from 'lucide-react';
import type { ComponentPropsWithoutRef, ComponentRef, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';

const DropdownMenu = DropdownMenuPrimitive.Root;
const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;
const DropdownMenuGroup = DropdownMenuPrimitive.Group;
const DropdownMenuPortal = DropdownMenuPrimitive.Portal;
const DropdownMenuSub = DropdownMenuPrimitive.Sub;
const DropdownMenuRadioGroup = DropdownMenuPrimitive.RadioGroup;

const DropdownMenuSubTrigger = forwardRef<ComponentRef<typeof DropdownMenuPrimitive.SubTrigger>, ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.SubTrigger> & { inset?: boolean }>(
  ({ children, className, inset, ...props }, ref): ReactElement => (
    <DropdownMenuPrimitive.SubTrigger
      className={cn(
        'flex h-8 cursor-default select-none items-center rounded-sm px-2 text-[12px] outline-none focus:bg-slate-100 data-[state=open]:bg-slate-100',
        inset && 'pl-8',
        className
      )}
      ref={ref}
      {...props}
    >
      {children}
      <ChevronRight className="ml-auto h-3.5 w-3.5" strokeWidth={1.5} />
    </DropdownMenuPrimitive.SubTrigger>
  )
);
DropdownMenuSubTrigger.displayName = DropdownMenuPrimitive.SubTrigger.displayName;

const DropdownMenuSubContent = forwardRef<ComponentRef<typeof DropdownMenuPrimitive.SubContent>, ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.SubContent>>(
  ({ className, ...props }, ref): ReactElement => (
    <DropdownMenuPrimitive.SubContent
      className={cn('z-50 min-w-32 overflow-hidden rounded-md border border-slate-200 bg-white p-1 text-slate-700 shadow-lg', className)}
      ref={ref}
      {...props}
    />
  )
);
DropdownMenuSubContent.displayName = DropdownMenuPrimitive.SubContent.displayName;

const DropdownMenuContent = forwardRef<ComponentRef<typeof DropdownMenuPrimitive.Content>, ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Content>>(
  ({ className, sideOffset = 6, ...props }, ref): ReactElement => (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        className={cn('z-50 min-w-32 overflow-hidden rounded-lg border border-slate-200 bg-white p-1.5 text-slate-700 shadow-lg data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:duration-150 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:duration-100', className)}
        ref={ref}
        sideOffset={sideOffset}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  )
);
DropdownMenuContent.displayName = DropdownMenuPrimitive.Content.displayName;

const DropdownMenuItem = forwardRef<ComponentRef<typeof DropdownMenuPrimitive.Item>, ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Item> & { inset?: boolean }>(
  ({ className, inset, ...props }, ref): ReactElement => (
    <DropdownMenuPrimitive.Item
      className={cn(
        'relative flex h-8 cursor-default select-none items-center rounded-md px-2 text-[12px] outline-none transition-colors focus:bg-slate-100 data-disabled:pointer-events-none data-disabled:opacity-50',
        inset && 'pl-8',
        className
      )}
      ref={ref}
      {...props}
    />
  )
);
DropdownMenuItem.displayName = DropdownMenuPrimitive.Item.displayName;

const DropdownMenuCheckboxItem = forwardRef<ComponentRef<typeof DropdownMenuPrimitive.CheckboxItem>, ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.CheckboxItem>>(
  ({ children, className, checked, ...props }, ref): ReactElement => (
    <DropdownMenuPrimitive.CheckboxItem
      checked={checked}
      className={cn('relative flex h-8 cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-[12px] outline-none focus:bg-slate-100 data-disabled:pointer-events-none data-disabled:opacity-50', className)}
      ref={ref}
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <DropdownMenuPrimitive.ItemIndicator>
          <Check className="h-3.5 w-3.5" strokeWidth={1.5} />
        </DropdownMenuPrimitive.ItemIndicator>
      </span>
      {children}
    </DropdownMenuPrimitive.CheckboxItem>
  )
);
DropdownMenuCheckboxItem.displayName = DropdownMenuPrimitive.CheckboxItem.displayName;

const DropdownMenuRadioItem = forwardRef<ComponentRef<typeof DropdownMenuPrimitive.RadioItem>, ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.RadioItem>>(
  ({ children, className, ...props }, ref): ReactElement => (
    <DropdownMenuPrimitive.RadioItem
      className={cn('relative flex h-8 cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-[12px] outline-none focus:bg-slate-100 data-disabled:pointer-events-none data-disabled:opacity-50', className)}
      ref={ref}
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <DropdownMenuPrimitive.ItemIndicator>
          <Circle className="h-2 w-2 fill-current" strokeWidth={1.5} />
        </DropdownMenuPrimitive.ItemIndicator>
      </span>
      {children}
    </DropdownMenuPrimitive.RadioItem>
  )
);
DropdownMenuRadioItem.displayName = DropdownMenuPrimitive.RadioItem.displayName;

const DropdownMenuSeparator = forwardRef<ComponentRef<typeof DropdownMenuPrimitive.Separator>, ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Separator>>(
  ({ className, ...props }, ref): ReactElement => <DropdownMenuPrimitive.Separator className={cn('-mx-1 my-1 h-px bg-slate-100', className)} ref={ref} {...props} />
);
DropdownMenuSeparator.displayName = DropdownMenuPrimitive.Separator.displayName;

export {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger
};
