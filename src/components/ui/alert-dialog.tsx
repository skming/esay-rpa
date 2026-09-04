import * as AlertDialogPrimitive from '@radix-ui/react-alert-dialog';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';
import { buttonVariants } from './button';

const AlertDialog = AlertDialogPrimitive.Root;
const AlertDialogTrigger = AlertDialogPrimitive.Trigger;
const AlertDialogPortal = AlertDialogPrimitive.Portal;

function AlertDialogOverlay({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Overlay>): ReactElement {
  return (
    <AlertDialogPrimitive.Overlay
      className={cn('fixed inset-0 z-(--z-modal-backdrop) bg-slate-950/35 backdrop-blur-[1px] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:duration-200 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:duration-150', className)}
      data-slot="alert-dialog-overlay"
      {...props}
    />
  );
}

function AlertDialogContent({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Content>): ReactElement {
  return (
    <AlertDialogPortal>
      <AlertDialogOverlay />
      <AlertDialogPrimitive.Content
        className={cn(
          'fixed left-1/2 top-1/2 z-(--z-modal) grid w-90 -translate-x-1/2 -translate-y-1/2 gap-4 rounded-xl border border-slate-200 bg-white p-4 text-slate-900 shadow-lg outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:duration-200 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:duration-150',
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
      className={cn('text-sm font-bold leading-none text-slate-900', className)}
      data-slot="alert-dialog-title"
      {...props}
    />
  );
}

function AlertDialogDescription({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Description>): ReactElement {
  return (
    <AlertDialogPrimitive.Description
      className={cn('text-[12px] leading-5 text-slate-500', className)}
      data-slot="alert-dialog-description"
      {...props}
    />
  );
}

function AlertDialogAction({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Action>): ReactElement {
  return (
    <AlertDialogPrimitive.Action
      className={cn(buttonVariants({ variant: 'danger' }), 'h-8 px-3', className)}
      data-slot="alert-dialog-action"
      {...props}
    />
  );
}

function AlertDialogCancel({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Cancel>): ReactElement {
  return (
    <AlertDialogPrimitive.Cancel
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
