import { AlertDialog as AlertDialogPrimitive } from '@base-ui/react/alert-dialog';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';
import { buttonVariants } from './button';

const AlertDialog = AlertDialogPrimitive.Root;
const AlertDialogTrigger = AlertDialogPrimitive.Trigger;
const AlertDialogPortal = AlertDialogPrimitive.Portal;

function AlertDialogOverlay({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Backdrop>): ReactElement {
  return (
    <AlertDialogPrimitive.Backdrop
      className={cn('fixed inset-0 z-(--z-modal-backdrop) bg-ink/35 backdrop-blur-[1px] data-open:animate-in data-open:fade-in-0 data-open:duration-200 data-closed:animate-out data-closed:fade-out-0 data-closed:duration-150', className)}
      data-slot="alert-dialog-overlay"
      {...props}
    />
  );
}

function AlertDialogContent({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Popup>): ReactElement {
  return (
    <AlertDialogPortal>
      <AlertDialogOverlay />
      <AlertDialogPrimitive.Popup
        className={cn(
          'fixed left-1/2 top-1/2 z-(--z-modal) grid w-90 -translate-x-1/2 -translate-y-1/2 gap-4 rounded-xl border border-rule bg-surface p-4 text-ink shadow-lg outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-open:duration-200 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95 data-closed:duration-150',
          className
        )}
        data-slot="alert-dialog-content"
        {...props}
      />
    </AlertDialogPortal>
  );
}

function AlertDialogHeader({ className, ...props }: ComponentProps<'div'>): ReactElement {
  return <div className={cn('flex flex-col gap-1.5', className)} data-slot="alert-dialog-header" {...props} />;
}

function AlertDialogFooter({ className, ...props }: ComponentProps<'div'>): ReactElement {
  return <div className={cn('flex items-center justify-end gap-2', className)} data-slot="alert-dialog-footer" {...props} />;
}

function AlertDialogTitle({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Title>): ReactElement {
  return (
    <AlertDialogPrimitive.Title
      className={cn('text-sm font-bold leading-none text-ink', className)}
      data-slot="alert-dialog-title"
      {...props}
    />
  );
}

function AlertDialogDescription({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Description>): ReactElement {
  return (
    <AlertDialogPrimitive.Description
      className={cn('text-[12px] leading-5 text-ink-3', className)}
      data-slot="alert-dialog-description"
      {...props}
    />
  );
}

function AlertDialogAction({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Close>): ReactElement {
  return (
    <AlertDialogPrimitive.Close
      className={cn(buttonVariants({ variant: 'danger' }), 'h-8 px-3', className)}
      data-slot="alert-dialog-action"
      {...props}
    />
  );
}

function AlertDialogCancel({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Close>): ReactElement {
  return (
    <AlertDialogPrimitive.Close
      className={cn(buttonVariants({ variant: 'outline' }), 'h-8 px-3', className)}
      data-slot="alert-dialog-cancel"
      {...props}
    />
  );
}

export {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogOverlay,
  AlertDialogPortal,
  AlertDialogTitle,
  AlertDialogTrigger
};
