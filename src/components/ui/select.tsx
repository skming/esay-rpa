import * as SelectPrimitive from '@radix-ui/react-select';
import { Check, ChevronDown, ChevronUp } from 'lucide-react';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';

const Select = SelectPrimitive.Root;
const SelectGroup = SelectPrimitive.Group;
const SelectValue = SelectPrimitive.Value;

function SelectTrigger({ children, className, ...props }: ComponentProps<typeof SelectPrimitive.Trigger>): ReactElement {
  return (
    <SelectPrimitive.Trigger
      className={cn(
        'flex h-8 w-full items-center justify-between rounded-md border border-slate-200 bg-white px-2 text-[11px] text-slate-700 outline-none transition focus-visible:border-accent-line focus-visible:ring-2 focus-visible:ring-accent-soft disabled:cursor-not-allowed disabled:opacity-50',
        className
      )}
      data-slot="select-trigger"
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <ChevronDown className="h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

function SelectScrollUpButton({ className, ...props }: ComponentProps<typeof SelectPrimitive.ScrollUpButton>): ReactElement {
  return (
    <SelectPrimitive.ScrollUpButton
      className={cn('flex cursor-default items-center justify-center py-1', className)}
      data-slot="select-scroll-up-button"
      {...props}
    >
      <ChevronUp className="h-3.5 w-3.5" strokeWidth={1.5} />
    </SelectPrimitive.ScrollUpButton>
  );
}

function SelectScrollDownButton({ className, ...props }: ComponentProps<typeof SelectPrimitive.ScrollDownButton>): ReactElement {
  return (
    <SelectPrimitive.ScrollDownButton
      className={cn('flex cursor-default items-center justify-center py-1', className)}
      data-slot="select-scroll-down-button"
      {...props}
    >
      <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.5} />
    </SelectPrimitive.ScrollDownButton>
  );
}

function SelectContent({
  children,
  className,
  position = 'popper',
  ...props
}: ComponentProps<typeof SelectPrimitive.Content>): ReactElement {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        className={cn(
          'relative z-(--z-select) max-h-72 min-w-32 overflow-hidden rounded-md border border-slate-200 bg-white text-slate-700 shadow-lg data-[side=bottom]:translate-y-1 data-[side=top]:-translate-y-1 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:duration-150 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:duration-100',
          position === 'popper' && 'data-[side=left]:-translate-x-1 data-[side=right]:translate-x-1',
          className
        )}
        data-slot="select-content"
        position={position}
        {...props}
      >
        <SelectScrollUpButton />
        <SelectPrimitive.Viewport
          className={cn('p-1', position === 'popper' && 'h-(--radix-select-trigger-height) min-w-(--radix-select-trigger-width)')}
          data-slot="select-viewport"
        >
          {children}
        </SelectPrimitive.Viewport>
        <SelectScrollDownButton />
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

function SelectItem({ children, className, ...props }: ComponentProps<typeof SelectPrimitive.Item>): ReactElement {
  return (
    <SelectPrimitive.Item
      className={cn(
        'relative flex h-7 w-full cursor-default select-none items-center rounded-sm py-1.5 pl-7 pr-2 text-[11px] outline-none focus:bg-accent-soft focus:text-accent-strong data-[state=checked]:font-medium data-[state=checked]:text-accent-strong data-disabled:pointer-events-none data-disabled:opacity-50',
        className
      )}
      data-slot="select-item"
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <Check className="h-3.5 w-3.5 text-accent" strokeWidth={2} />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );
}

export { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue };
