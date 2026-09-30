import { Select as SelectPrimitive } from '@base-ui/react/select';
import { Check, ChevronDown, ChevronUp } from 'lucide-react';
import type { ReactElement } from 'react';

import { cn } from '../../lib/utils';

const Select = SelectPrimitive.Root;

function SelectGroup({ className, ...props }: SelectPrimitive.Group.Props): ReactElement {
  return <SelectPrimitive.Group className={cn('scroll-my-1 p-1', className)} data-slot="select-group" {...props} />;
}

function SelectValue({ className, ...props }: SelectPrimitive.Value.Props): ReactElement {
  return <SelectPrimitive.Value className={cn('flex flex-1 text-left', className)} data-slot="select-value" {...props} />;
}

function SelectTrigger({ children, className, ...props }: SelectPrimitive.Trigger.Props): ReactElement {
  return (
    <SelectPrimitive.Trigger
      className={cn(
        'flex h-8 w-full items-center justify-between rounded-md border border-slate-200 bg-white px-2 text-[11px] text-slate-700 outline-none transition focus-visible:border-accent-line focus-visible:ring-2 focus-visible:ring-accent-soft disabled:cursor-not-allowed disabled:opacity-50 data-placeholder:text-slate-400',
        className
      )}
      data-slot="select-trigger"
      {...props}
    >
      {children}
      <SelectPrimitive.Icon render={<ChevronDown className="h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />} />
    </SelectPrimitive.Trigger>
  );
}

function SelectContent({
  align = 'center',
  alignItemWithTrigger = true,
  alignOffset = 0,
  children,
  className,
  side = 'bottom',
  sideOffset = 4,
  ...props
}: SelectPrimitive.Popup.Props & Pick<SelectPrimitive.Positioner.Props, 'align' | 'alignItemWithTrigger' | 'alignOffset' | 'side' | 'sideOffset'>): ReactElement {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Positioner
        align={align}
        alignItemWithTrigger={alignItemWithTrigger}
        alignOffset={alignOffset}
        className="isolate z-(--z-select)"
        side={side}
        sideOffset={sideOffset}
      >
        <SelectPrimitive.Popup
          className={cn(
            'relative max-h-(--available-height) w-(--anchor-width) min-w-32 origin-(--transform-origin) overflow-x-hidden overflow-y-auto rounded-md border border-slate-200 bg-white text-slate-700 shadow-lg outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95',
            className
          )}
          data-slot="select-content"
          {...props}
        >
          <SelectScrollUpButton />
          <SelectPrimitive.List className="p-1">{children}</SelectPrimitive.List>
          <SelectScrollDownButton />
        </SelectPrimitive.Popup>
      </SelectPrimitive.Positioner>
    </SelectPrimitive.Portal>
  );
}

function SelectItem({ children, className, ...props }: SelectPrimitive.Item.Props): ReactElement {
  return (
    <SelectPrimitive.Item
      className={cn(
        'relative flex h-7 w-full cursor-default select-none items-center rounded-sm py-1.5 pl-7 pr-2 text-[11px] outline-none focus:bg-accent-soft focus:text-accent-strong data-selected:font-medium data-selected:text-accent-strong data-disabled:pointer-events-none data-disabled:opacity-50',
        className
      )}
      data-slot="select-item"
      {...props}
    >
      <SelectPrimitive.ItemIndicator
        render={<span className="pointer-events-none absolute left-2 flex h-3.5 w-3.5 items-center justify-center" />}
      >
        <Check className="h-3.5 w-3.5 text-accent" strokeWidth={2} />
      </SelectPrimitive.ItemIndicator>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );
}

function SelectScrollUpButton({ className, ...props }: SelectPrimitive.ScrollUpArrow.Props): ReactElement {
  return (
    <SelectPrimitive.ScrollUpArrow
      className={cn('sticky top-0 z-10 flex w-full cursor-default items-center justify-center bg-white py-1', className)}
      data-slot="select-scroll-up-button"
      {...props}
    >
      <ChevronUp className="h-3.5 w-3.5" strokeWidth={1.5} />
    </SelectPrimitive.ScrollUpArrow>
  );
}

function SelectScrollDownButton({ className, ...props }: SelectPrimitive.ScrollDownArrow.Props): ReactElement {
  return (
    <SelectPrimitive.ScrollDownArrow
      className={cn('sticky bottom-0 z-10 flex w-full cursor-default items-center justify-center bg-white py-1', className)}
      data-slot="select-scroll-down-button"
      {...props}
    >
      <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.5} />
    </SelectPrimitive.ScrollDownArrow>
  );
}

export { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue };
