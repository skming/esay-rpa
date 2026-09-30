import { ContextMenu as ContextMenuPrimitive } from '@base-ui/react/context-menu';
import { Check, ChevronRight, Circle } from 'lucide-react';
import type { ReactElement } from 'react';

import { cn } from '../../lib/utils';

function ContextMenu(props: ContextMenuPrimitive.Root.Props): ReactElement {
  return <ContextMenuPrimitive.Root data-slot="context-menu" {...props} />;
}

function ContextMenuTrigger({ className, ...props }: ContextMenuPrimitive.Trigger.Props): ReactElement {
  return <ContextMenuPrimitive.Trigger className={cn('select-none', className)} data-slot="context-menu-trigger" {...props} />;
}

function ContextMenuContent({
  align = 'start',
  alignOffset = 4,
  className,
  side = 'right',
  sideOffset = 0,
  ...props
}: ContextMenuPrimitive.Popup.Props & Pick<ContextMenuPrimitive.Positioner.Props, 'align' | 'alignOffset' | 'side' | 'sideOffset'>): ReactElement {
  return (
    <ContextMenuPrimitive.Portal>
      <ContextMenuPrimitive.Positioner align={align} alignOffset={alignOffset} className="isolate z-(--z-dropdown) outline-none" side={side} sideOffset={sideOffset}>
        <ContextMenuPrimitive.Popup
          className={cn('max-h-(--available-height) min-w-32 origin-(--transform-origin) overflow-x-hidden overflow-y-auto rounded-lg border border-slate-200 bg-white p-1.5 text-slate-700 shadow-lg outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95', className)}
          data-slot="context-menu-content"
          {...props}
        />
      </ContextMenuPrimitive.Positioner>
    </ContextMenuPrimitive.Portal>
  );
}

function ContextMenuGroup(props: ContextMenuPrimitive.Group.Props): ReactElement {
  return <ContextMenuPrimitive.Group data-slot="context-menu-group" {...props} />;
}

function ContextMenuItem({ className, inset, ...props }: ContextMenuPrimitive.Item.Props & { inset?: boolean }): ReactElement {
  return <ContextMenuPrimitive.Item className={cn('relative flex h-8 cursor-default select-none items-center rounded-md px-2 text-[12px] outline-none transition-colors focus:bg-slate-100 data-disabled:pointer-events-none data-disabled:opacity-50', inset && 'pl-8', className)} data-inset={inset || undefined} data-slot="context-menu-item" {...props} />;
}

function ContextMenuSub(props: ContextMenuPrimitive.SubmenuRoot.Props): ReactElement {
  return <ContextMenuPrimitive.SubmenuRoot data-slot="context-menu-sub" {...props} />;
}

function ContextMenuSubTrigger({ children, className, inset, ...props }: ContextMenuPrimitive.SubmenuTrigger.Props & { inset?: boolean }): ReactElement {
  return (
    <ContextMenuPrimitive.SubmenuTrigger className={cn('flex h-8 cursor-default select-none items-center rounded-sm px-2 text-[12px] outline-none focus:bg-slate-100 data-open:bg-slate-100', inset && 'pl-8', className)} data-inset={inset || undefined} data-slot="context-menu-sub-trigger" {...props}>
      {children}<ChevronRight className="ml-auto h-3.5 w-3.5" strokeWidth={1.5} />
    </ContextMenuPrimitive.SubmenuTrigger>
  );
}

function ContextMenuSubContent(props: React.ComponentProps<typeof ContextMenuContent>): ReactElement {
  return <ContextMenuContent data-slot="context-menu-sub-content" side="right" {...props} />;
}

function ContextMenuCheckboxItem({ checked, children, className, ...props }: ContextMenuPrimitive.CheckboxItem.Props): ReactElement {
  return (
    <ContextMenuPrimitive.CheckboxItem checked={checked} className={cn('relative flex h-8 cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-[12px] outline-none focus:bg-slate-100 data-disabled:pointer-events-none data-disabled:opacity-50', className)} data-slot="context-menu-checkbox-item" {...props}>
      <span className="pointer-events-none absolute left-2 flex h-3.5 w-3.5 items-center justify-center"><ContextMenuPrimitive.CheckboxItemIndicator><Check className="h-3.5 w-3.5" strokeWidth={1.5} /></ContextMenuPrimitive.CheckboxItemIndicator></span>
      {children}
    </ContextMenuPrimitive.CheckboxItem>
  );
}

function ContextMenuRadioGroup(props: ContextMenuPrimitive.RadioGroup.Props): ReactElement {
  return <ContextMenuPrimitive.RadioGroup data-slot="context-menu-radio-group" {...props} />;
}

function ContextMenuRadioItem({ children, className, ...props }: ContextMenuPrimitive.RadioItem.Props): ReactElement {
  return (
    <ContextMenuPrimitive.RadioItem className={cn('relative flex h-8 cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-[12px] outline-none focus:bg-slate-100 data-disabled:pointer-events-none data-disabled:opacity-50', className)} data-slot="context-menu-radio-item" {...props}>
      <span className="pointer-events-none absolute left-2 flex h-3.5 w-3.5 items-center justify-center"><ContextMenuPrimitive.RadioItemIndicator><Circle className="h-2 w-2 fill-current" strokeWidth={1.5} /></ContextMenuPrimitive.RadioItemIndicator></span>
      {children}
    </ContextMenuPrimitive.RadioItem>
  );
}

function ContextMenuSeparator({ className, ...props }: ContextMenuPrimitive.Separator.Props): ReactElement {
  return <ContextMenuPrimitive.Separator className={cn('-mx-1 my-1 h-px bg-slate-100', className)} data-slot="context-menu-separator" {...props} />;
}

const ContextMenuPortal = ContextMenuPrimitive.Portal;

export { ContextMenu, ContextMenuCheckboxItem, ContextMenuContent, ContextMenuGroup, ContextMenuItem, ContextMenuPortal, ContextMenuRadioGroup, ContextMenuRadioItem, ContextMenuSeparator, ContextMenuSub, ContextMenuSubContent, ContextMenuSubTrigger, ContextMenuTrigger };
