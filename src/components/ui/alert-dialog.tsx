import * as AlertDialogPrimitive from '@radix-ui/react-alert-dialog';
import type { ComponentPropsWithoutRef, ComponentRef, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';
import { buttonVariants } from './button';

const AlertDialog = AlertDialogPrimitive.Root;
const AlertDialogTrigger = AlertDialogPrimitive.Trigger;
const AlertDialogPortal = AlertDialogPrimitive.Portal;

const AlertDialogOverlay = forwardRef<ComponentRef<typeof AlertDialogPrimitive.Overlay>, ComponentPropsWithoutRef<typeof AlertDialogPrimitive.Overlay>>(
  ({ className, ...props }, ref): ReactElement => (
    <AlertDialogPrimitive.Overlay
      className={cn('fixed inset-0 z-(--z-modal-backdrop) bg-slate-950/35 backdrop-blur-[1px] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:duration-200 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:duration-150', className)}
      ref={ref}
      {...props}
    />
  )
);
AlertDialogOverlay.displayName = AlertDialogPrimitive.Overlay.displayName;

const AlertDialogContent = forwardRef<ComponentRef<typeof AlertDialogPrimitive.Content>, ComponentPropsWithoutRef<typeof AlertDialogPrimitive.Content>>(
  ({ className, ...props }, ref): ReactElement => (
    <AlertDialogPortal>
      <AlertDialogOverlay />
      <AlertDialogPrimitive.Content
        className={cn(
          'fixed left-1/2 top-1/2 z-(--z-modal) grid w-90 -translate-x-1/2 -translate-y-1/2 gap-4 rounded-xl border border-slate-200 bg-white p-4 text-slate-900 shadow-lg outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:duration-200 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:duration-150',
          className
        )}
        ref={ref}
        {...props}
      />
    </AlertDialogPortal>
  )
);
AlertDialogContent.displayName = AlertDialogPrimitive.Content.displayName;

const AlertDialogHeader = ({ className, ...props }: ComponentPropsWithoutRef<'div'>): ReactElement => (
  <div className={cn('flex flex-col gap-1.5', className)} {...props} />
);
AlertDialogHeader.displayName = 'AlertDialogHeader';

const AlertDialogFooter = ({ className, ...props }: ComponentPropsWithoutRef<'div'>): ReactElement => (
  <div className={cn('flex items-center justify-end gap-2', className)} {...props} />
);
AlertDialogFooter.displayName = 'AlertDialogFooter';

const AlertDialogTitle = forwardRef<ComponentRef<typeof AlertDialogPrimitive.Title>, ComponentPropsWithoutRef<typeof AlertDialogPrimitive.Title>>(
  ({ className, ...props }, ref): ReactElement => (
    <AlertDialogPrimitive.Title className={cn('text-sm font-bold leading-none text-slate-900', className)} ref={ref} {...props} />
  )
);
AlertDialogTitle.displayName = AlertDialogPrimitive.Title.displayName;

const AlertDialogDescription = forwardRef<ComponentRef<typeof AlertDialogPrimitive.Description>, ComponentPropsWithoutRef<typeof AlertDialogPrimitive.Description>>(
  ({ className, ...props }, ref): ReactElement => (
    <AlertDialogPrimitive.Description className={cn('text-[12px] leading-5 text-slate-500', className)} ref={ref} {...props} />
  )
);
AlertDialogDescription.displayName = AlertDialogPrimitive.Description.displayName;

const AlertDialogAction = forwardRef<ComponentRef<typeof AlertDialogPrimitive.Action>, ComponentPropsWithoutRef<typeof AlertDialogPrimitive.Action>>(
  ({ className, ...props }, ref): ReactElement => (
    <AlertDialogPrimitive.Action className={cn(buttonVariants({ variant: 'danger' }), 'h-8 px-3', className)} ref={ref} {...props} />
  )
);
AlertDialogAction.displayName = AlertDialogPrimitive.Action.displayName;

const AlertDialogCancel = forwardRef<ComponentRef<typeof AlertDialogPrimitive.Cancel>, ComponentPropsWithoutRef<typeof AlertDialogPrimitive.Cancel>>(
  ({ className, ...props }, ref): ReactElement => (
    <AlertDialogPrimitive.Cancel className={cn(buttonVariants({ variant: 'outline' }), 'h-8 px-3', className)} ref={ref} {...props} />
  )
);
AlertDialogCancel.displayName = AlertDialogPrimitive.Cancel.displayName;

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
