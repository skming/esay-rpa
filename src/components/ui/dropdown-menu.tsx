import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu';
import { Check, ChevronRight, Circle } from 'lucide-react';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';

const DropdownMenu = DropdownMenuPrimitive.Root;
const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;
const DropdownMenuGroup = DropdownMenuPrimitive.Group;
const DropdownMenuPortal = DropdownMenuPrimitive.Portal;
const DropdownMenuSub = DropdownMenuPrimitive.Sub;
const DropdownMenuRadioGroup = DropdownMenuPrimitive.RadioGroup;

function DropdownMenuSubTrigger({
  children,
  className,
  inset,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.SubTrigger> & { inset?: boolean }): ReactElement {
  return (
    <DropdownMenuPrimitive.SubTrigger
      className={cn(
        'flex h-8 cursor-default select-none items-center rounded-sm px-2 text-[12px] outline-none focus:bg-slate-100 data-[state=open]:bg-slate-100',
        inset && 'pl-8',
        className
      )}
      data-slot="dropdown-menu-sub-trigger"
      {...props}
    >
      {children}
      <ChevronRight className="ml-auto h-3.5 w-3.5" strokeWidth={1.5} />
    </DropdownMenuPrimitive.SubTrigger>
  );
}

function DropdownMenuSubContent({ className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.SubContent>): ReactElement {
  return (
    <DropdownMenuPrimitive.SubContent
      className={cn('z-(--z-dropdown) min-w-32 overflow-hidden rounded-md border border-slate-200 bg-white p-1 text-slate-700 shadow-lg', className)}
      data-slot="dropdown-menu-sub-content"
      {...props}
    />
  );
}

function DropdownMenuContent({
  className,
  sideOffset = 6,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Content>): ReactElement {
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        className={cn('z-(--z-dropdown) min-w-32 overflow-hidden rounded-lg border border-slate-200 bg-white p-1.5 text-slate-700 shadow-lg data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:duration-150 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:duration-100', className)}
        data-slot="dropdown-menu-content"
        sideOffset={sideOffset}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  );
}

function DropdownMenuItem({
  className,
  inset,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Item> & { inset?: boolean }): ReactElement {
  return (
    <DropdownMenuPrimitive.Item
      className={cn(
        'relative flex h-8 cursor-default select-none items-center rounded-md px-2 text-[12px] outline-none transition-colors focus:bg-slate-100 data-disabled:pointer-events-none data-disabled:opacity-50',
        inset && 'pl-8',
        className
      )}
      data-slot="dropdown-menu-item"
      {...props}
    />
  );
}

function DropdownMenuCheckboxItem({
  checked,
  children,
  className,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.CheckboxItem>): ReactElement {
  return (
    <DropdownMenuPrimitive.CheckboxItem
      checked={checked}
      className={cn('relative flex h-8 cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-[12px] outline-none focus:bg-slate-100 data-disabled:pointer-events-none data-disabled:opacity-50', className)}
      data-slot="dropdown-menu-checkbox-item"
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <DropdownMenuPrimitive.ItemIndicator>
          <Check className="h-3.5 w-3.5" strokeWidth={1.5} />
        </DropdownMenuPrimitive.ItemIndicator>
      </span>
      {children}
    </DropdownMenuPrimitive.CheckboxItem>
  );
}

function DropdownMenuRadioItem({ children, className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.RadioItem>): ReactElement {
  return (
    <DropdownMenuPrimitive.RadioItem
      className={cn('relative flex h-8 cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-[12px] outline-none focus:bg-slate-100 data-disabled:pointer-events-none data-disabled:opacity-50', className)}
      data-slot="dropdown-menu-radio-item"
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <DropdownMenuPrimitive.ItemIndicator>
          <Circle className="h-2 w-2 fill-current" strokeWidth={1.5} />
        </DropdownMenuPrimitive.ItemIndicator>
      </span>
      {children}
    </DropdownMenuPrimitive.RadioItem>
  );
}

function DropdownMenuSeparator({ className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.Separator>): ReactElement {
  return <DropdownMenuPrimitive.Separator className={cn('-mx-1 my-1 h-px bg-slate-100', className)} data-slot="dropdown-menu-separator" {...props} />;
}

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
