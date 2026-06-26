import * as SelectPrimitive from '@radix-ui/react-select';
import { Check, ChevronDown, ChevronUp } from 'lucide-react';
import type { ComponentPropsWithoutRef, ElementRef, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';

const Select = SelectPrimitive.Root;
const SelectGroup = SelectPrimitive.Group;
const SelectValue = SelectPrimitive.Value;

const SelectTrigger = forwardRef<ElementRef<typeof SelectPrimitive.Trigger>, ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger>>(
  ({ children, className, ...props }, ref): ReactElement => (
    <SelectPrimitive.Trigger
      className={cn(
        'flex h-8 w-full items-center justify-between rounded-md border border-slate-200 bg-white px-2 text-[11px] text-slate-700 outline-none transition focus:border-accent focus:ring-2 focus:ring-accent-soft disabled:cursor-not-allowed disabled:opacity-50',
        className
      )}
      ref={ref}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <ChevronDown className="h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  )
);
SelectTrigger.displayName = SelectPrimitive.Trigger.displayName;

const SelectScrollUpButton = forwardRef<ElementRef<typeof SelectPrimitive.ScrollUpButton>, ComponentPropsWithoutRef<typeof SelectPrimitive.ScrollUpButton>>(
  ({ className, ...props }, ref): ReactElement => (
    <SelectPrimitive.ScrollUpButton className={cn('flex cursor-default items-center justify-center py-1', className)} ref={ref} {...props}>
      <ChevronUp className="h-3.5 w-3.5" strokeWidth={1.5} />
    </SelectPrimitive.ScrollUpButton>
  )
);
SelectScrollUpButton.displayName = SelectPrimitive.ScrollUpButton.displayName;

const SelectScrollDownButton = forwardRef<ElementRef<typeof SelectPrimitive.ScrollDownButton>, ComponentPropsWithoutRef<typeof SelectPrimitive.ScrollDownButton>>(
  ({ className, ...props }, ref): ReactElement => (
    <SelectPrimitive.ScrollDownButton className={cn('flex cursor-default items-center justify-center py-1', className)} ref={ref} {...props}>
      <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.5} />
    </SelectPrimitive.ScrollDownButton>
  )
);
SelectScrollDownButton.displayName = SelectPrimitive.ScrollDownButton.displayName;

const SelectContent = forwardRef<ElementRef<typeof SelectPrimitive.Content>, ComponentPropsWithoutRef<typeof SelectPrimitive.Content>>(
  ({ children, className, position = 'popper', ...props }, ref): ReactElement => (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        className={cn(
          'relative z-110 max-h-72 min-w-32 overflow-hidden rounded-md border border-slate-200 bg-white text-slate-700 shadow-lg data-[side=bottom]:translate-y-1 data-[side=top]:-translate-y-1 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:duration-150 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:duration-100',
          position === 'popper' && 'data-[side=left]:-translate-x-1 data-[side=right]:translate-x-1',
          className
        )}
        position={position}
        ref={ref}
        {...props}
      >
        <SelectScrollUpButton />
        <SelectPrimitive.Viewport className={cn('p-1', position === 'popper' && 'h-(--radix-select-trigger-height) min-w-(--radix-select-trigger-width)')}>
          {children}
        </SelectPrimitive.Viewport>
        <SelectScrollDownButton />
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  )
);
SelectContent.displayName = SelectPrimitive.Content.displayName;

const SelectItem = forwardRef<ElementRef<typeof SelectPrimitive.Item>, ComponentPropsWithoutRef<typeof SelectPrimitive.Item>>(
  ({ children, className, ...props }, ref): ReactElement => (
    <SelectPrimitive.Item
      className={cn(
        'relative flex h-7 w-full cursor-default select-none items-center rounded-sm py-1.5 pl-7 pr-2 text-[11px] outline-none focus:bg-accent-soft focus:text-accent-strong data-[state=checked]:font-medium data-[state=checked]:text-accent-strong data-disabled:pointer-events-none data-disabled:opacity-50',
        className
      )}
      ref={ref}
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <Check className="h-3.5 w-3.5 text-accent" strokeWidth={2} />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  )
);
SelectItem.displayName = SelectPrimitive.Item.displayName;

export { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue };
