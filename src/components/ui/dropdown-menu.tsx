import { Menu as MenuPrimitive } from '@base-ui/react/menu';
import { Check, ChevronRight, Circle } from 'lucide-react';
import type { ReactElement } from 'react';

import { cn } from '../../lib/utils';

function DropdownMenu(props: MenuPrimitive.Root.Props): ReactElement {
  return <MenuPrimitive.Root data-slot="dropdown-menu" {...props} />;
}

function DropdownMenuTrigger(props: MenuPrimitive.Trigger.Props): ReactElement {
  return <MenuPrimitive.Trigger data-slot="dropdown-menu-trigger" {...props} />;
}

function DropdownMenuContent({
  align = 'start',
  alignOffset = 0,
  className,
  side = 'bottom',
  sideOffset = 6,
  ...props
}: MenuPrimitive.Popup.Props & Pick<MenuPrimitive.Positioner.Props, 'align' | 'alignOffset' | 'side' | 'sideOffset'>): ReactElement {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Positioner align={align} alignOffset={alignOffset} className="isolate z-(--z-dropdown) outline-none" side={side} sideOffset={sideOffset}>
        <MenuPrimitive.Popup
          className={cn('max-h-(--available-height) min-w-32 origin-(--transform-origin) overflow-x-hidden overflow-y-auto rounded-lg border border-slate-200 bg-white p-1.5 text-slate-700 shadow-lg outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95', className)}
          data-slot="dropdown-menu-content"
          {...props}
        />
      </MenuPrimitive.Positioner>
    </MenuPrimitive.Portal>
  );
}

function DropdownMenuGroup(props: MenuPrimitive.Group.Props): ReactElement {
  return <MenuPrimitive.Group data-slot="dropdown-menu-group" {...props} />;
}

function DropdownMenuItem({ className, inset, ...props }: MenuPrimitive.Item.Props & { inset?: boolean }): ReactElement {
  return (
    <MenuPrimitive.Item
      className={cn('relative flex h-8 cursor-default select-none items-center rounded-md px-2 text-[12px] outline-none transition-colors focus:bg-slate-100 data-disabled:pointer-events-none data-disabled:opacity-50', inset && 'pl-8', className)}
      data-inset={inset || undefined}
      data-slot="dropdown-menu-item"
      {...props}
    />
  );
}

function DropdownMenuSub(props: MenuPrimitive.SubmenuRoot.Props): ReactElement {
  return <MenuPrimitive.SubmenuRoot data-slot="dropdown-menu-sub" {...props} />;
}

function DropdownMenuSubTrigger({ children, className, inset, ...props }: MenuPrimitive.SubmenuTrigger.Props & { inset?: boolean }): ReactElement {
  return (
    <MenuPrimitive.SubmenuTrigger
      className={cn('flex h-8 cursor-default select-none items-center rounded-sm px-2 text-[12px] outline-none focus:bg-slate-100 data-popup-open:bg-slate-100', inset && 'pl-8', className)}
      data-inset={inset || undefined}
      data-slot="dropdown-menu-sub-trigger"
      {...props}
    >
      {children}
      <ChevronRight className="ml-auto h-3.5 w-3.5" strokeWidth={1.5} />
    </MenuPrimitive.SubmenuTrigger>
  );
}

function DropdownMenuSubContent(props: React.ComponentProps<typeof DropdownMenuContent>): ReactElement {
  return <DropdownMenuContent alignOffset={-3} data-slot="dropdown-menu-sub-content" side="right" sideOffset={0} {...props} />;
}

function DropdownMenuCheckboxItem({ checked, children, className, ...props }: MenuPrimitive.CheckboxItem.Props): ReactElement {
  return (
    <MenuPrimitive.CheckboxItem
      checked={checked}
      className={cn('relative flex h-8 cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-[12px] outline-none focus:bg-slate-100 data-disabled:pointer-events-none data-disabled:opacity-50', className)}
      data-slot="dropdown-menu-checkbox-item"
      {...props}
    >
      <span className="pointer-events-none absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <MenuPrimitive.CheckboxItemIndicator><Check className="h-3.5 w-3.5" strokeWidth={1.5} /></MenuPrimitive.CheckboxItemIndicator>
      </span>
      {children}
    </MenuPrimitive.CheckboxItem>
  );
}

function DropdownMenuRadioGroup(props: MenuPrimitive.RadioGroup.Props): ReactElement {
  return <MenuPrimitive.RadioGroup data-slot="dropdown-menu-radio-group" {...props} />;
}

function DropdownMenuRadioItem({ children, className, ...props }: MenuPrimitive.RadioItem.Props): ReactElement {
  return (
    <MenuPrimitive.RadioItem
      className={cn('relative flex h-8 cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-[12px] outline-none focus:bg-slate-100 data-disabled:pointer-events-none data-disabled:opacity-50', className)}
      data-slot="dropdown-menu-radio-item"
      {...props}
    >
      <span className="pointer-events-none absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <MenuPrimitive.RadioItemIndicator><Circle className="h-2 w-2 fill-current" strokeWidth={1.5} /></MenuPrimitive.RadioItemIndicator>
      </span>
      {children}
    </MenuPrimitive.RadioItem>
  );
}

function DropdownMenuSeparator({ className, ...props }: MenuPrimitive.Separator.Props): ReactElement {
  return <MenuPrimitive.Separator className={cn('-mx-1 my-1 h-px bg-slate-100', className)} data-slot="dropdown-menu-separator" {...props} />;
}

const DropdownMenuPortal = MenuPrimitive.Portal;

export { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuPortal, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger };
