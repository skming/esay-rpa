import * as ContextMenuPrimitive from '@radix-ui/react-context-menu';
import { Check, ChevronRight, Circle } from 'lucide-react';
import type { ComponentPropsWithoutRef, ElementRef, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';

const ContextMenu = ContextMenuPrimitive.Root;
const ContextMenuTrigger = ContextMenuPrimitive.Trigger;
const ContextMenuGroup = ContextMenuPrimitive.Group;
const ContextMenuPortal = ContextMenuPrimitive.Portal;
const ContextMenuSub = ContextMenuPrimitive.Sub;
const ContextMenuRadioGroup = ContextMenuPrimitive.RadioGroup;

const ContextMenuSubTrigger = forwardRef<
  ElementRef<typeof ContextMenuPrimitive.SubTrigger>,
  ComponentPropsWithoutRef<typeof ContextMenuPrimitive.SubTrigger> & { inset?: boolean }
>(({ children, className, inset, ...props }, ref): ReactElement => (
  <ContextMenuPrimitive.SubTrigger
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
  </ContextMenuPrimitive.SubTrigger>
));
ContextMenuSubTrigger.displayName = ContextMenuPrimitive.SubTrigger.displayName;

const ContextMenuSubContent = forwardRef<ElementRef<typeof ContextMenuPrimitive.SubContent>, ComponentPropsWithoutRef<typeof ContextMenuPrimitive.SubContent>>(
  ({ className, ...props }, ref): ReactElement => (
    <ContextMenuPrimitive.SubContent
      className={cn('z-(--z-dropdown) min-w-[8rem] overflow-hidden rounded-md border border-slate-200 bg-white p-1 text-slate-700 shadow-lg', className)}
      ref={ref}
      {...props}
    />
  )
);
ContextMenuSubContent.displayName = ContextMenuPrimitive.SubContent.displayName;

const ContextMenuContent = forwardRef<ElementRef<typeof ContextMenuPrimitive.Content>, ComponentPropsWithoutRef<typeof ContextMenuPrimitive.Content>>(
  ({ className, ...props }, ref): ReactElement => (
    <ContextMenuPrimitive.Portal>
      <ContextMenuPrimitive.Content
        className={cn('z-(--z-dropdown) min-w-[8rem] overflow-hidden rounded-lg border border-slate-200 bg-white p-1.5 text-slate-700 shadow-lg', className)}
        ref={ref}
        {...props}
      />
    </ContextMenuPrimitive.Portal>
  )
);
ContextMenuContent.displayName = ContextMenuPrimitive.Content.displayName;

const ContextMenuItem = forwardRef<ElementRef<typeof ContextMenuPrimitive.Item>, ComponentPropsWithoutRef<typeof ContextMenuPrimitive.Item> & { inset?: boolean }>(
  ({ className, inset, ...props }, ref): ReactElement => (
    <ContextMenuPrimitive.Item
      className={cn(
        'relative flex h-8 cursor-default select-none items-center rounded-md px-2 text-[12px] outline-none transition-colors focus:bg-slate-100 data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        inset && 'pl-8',
        className
      )}
      ref={ref}
      {...props}
    />
  )
);
ContextMenuItem.displayName = ContextMenuPrimitive.Item.displayName;

const ContextMenuCheckboxItem = forwardRef<ElementRef<typeof ContextMenuPrimitive.CheckboxItem>, ComponentPropsWithoutRef<typeof ContextMenuPrimitive.CheckboxItem>>(
  ({ children, className, checked, ...props }, ref): ReactElement => (
    <ContextMenuPrimitive.CheckboxItem
      checked={checked}
      className={cn(
        'relative flex h-8 cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-[12px] outline-none focus:bg-slate-100 data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        className
      )}
      ref={ref}
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <ContextMenuPrimitive.ItemIndicator>
          <Check className="h-3.5 w-3.5" strokeWidth={1.5} />
        </ContextMenuPrimitive.ItemIndicator>
      </span>
      {children}
    </ContextMenuPrimitive.CheckboxItem>
  )
);
ContextMenuCheckboxItem.displayName = ContextMenuPrimitive.CheckboxItem.displayName;

const ContextMenuRadioItem = forwardRef<ElementRef<typeof ContextMenuPrimitive.RadioItem>, ComponentPropsWithoutRef<typeof ContextMenuPrimitive.RadioItem>>(
  ({ children, className, ...props }, ref): ReactElement => (
    <ContextMenuPrimitive.RadioItem
      className={cn(
        'relative flex h-8 cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-[12px] outline-none focus:bg-slate-100 data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        className
      )}
      ref={ref}
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <ContextMenuPrimitive.ItemIndicator>
          <Circle className="h-2 w-2 fill-current" strokeWidth={1.5} />
        </ContextMenuPrimitive.ItemIndicator>
      </span>
      {children}
    </ContextMenuPrimitive.RadioItem>
  )
);
ContextMenuRadioItem.displayName = ContextMenuPrimitive.RadioItem.displayName;

const ContextMenuSeparator = forwardRef<ElementRef<typeof ContextMenuPrimitive.Separator>, ComponentPropsWithoutRef<typeof ContextMenuPrimitive.Separator>>(
  ({ className, ...props }, ref): ReactElement => <ContextMenuPrimitive.Separator className={cn('-mx-1 my-1 h-px bg-slate-100', className)} ref={ref} {...props} />
);
ContextMenuSeparator.displayName = ContextMenuPrimitive.Separator.displayName;

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
