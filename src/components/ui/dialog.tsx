import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ComponentPropsWithoutRef, ElementRef, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';
import { IconButton } from './button';

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogPortal = DialogPrimitive.Portal;
const DialogClose = DialogPrimitive.Close;

const DialogOverlay = forwardRef<ElementRef<typeof DialogPrimitive.Overlay>, ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>>(
  ({ className, ...props }, ref): ReactElement => (
    <DialogPrimitive.Overlay className={cn('fixed inset-0 z-(--z-modal-backdrop) bg-slate-950/45 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:duration-200 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:duration-150', className)} ref={ref} {...props} />
  )
);
DialogOverlay.displayName = DialogPrimitive.Overlay.displayName;

type DialogContentProps = ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & {
  showClose?: boolean;
};

const DialogContent = forwardRef<ElementRef<typeof DialogPrimitive.Content>, DialogContentProps>(
  ({ children, className, showClose = true, ...props }, ref): ReactElement => (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Content
        className={cn(
          'fixed left-1/2 top-1/2 z-(--z-modal) flex w-110 max-h-[calc(100vh-48px)] max-w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-lg outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:duration-200 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:duration-150',
          className
        )}
        ref={ref}
        {...props}
      >
        {children}
        {showClose && (
          <DialogPrimitive.Close asChild>
            <IconButton className="absolute right-3 top-3 h-7 w-7" label="关闭弹窗">
              <X className="h-3.5 w-3.5" strokeWidth={1.5} />
            </IconButton>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPortal>
  )
);
DialogContent.displayName = DialogPrimitive.Content.displayName;

const DialogHeader = ({ className, ...props }: ComponentPropsWithoutRef<'div'>): ReactElement => (
  <div className={cn('flex shrink-0 flex-col gap-1.5 px-6 pb-4 pt-6 pr-12', className)} {...props} />
);
DialogHeader.displayName = 'DialogHeader';

const DialogBody = ({ className, ...props }: ComponentPropsWithoutRef<'div'>): ReactElement => (
  <div className={cn('min-h-0 flex-1 overflow-y-auto px-5 py-4', className)} {...props} />
);
DialogBody.displayName = 'DialogBody';

const DialogFooter = ({ className, ...props }: ComponentPropsWithoutRef<'div'>): ReactElement => (
  <div className={cn('flex shrink-0 items-center justify-end gap-2 border-t border-slate-100 px-5 pb-4 pt-3', className)} {...props} />
);
DialogFooter.displayName = 'DialogFooter';

const DialogTitle = forwardRef<ElementRef<typeof DialogPrimitive.Title>, ComponentPropsWithoutRef<typeof DialogPrimitive.Title>>(
  ({ className, ...props }, ref): ReactElement => <DialogPrimitive.Title className={cn('text-sm font-bold leading-none text-slate-900', className)} ref={ref} {...props} />
);
DialogTitle.displayName = DialogPrimitive.Title.displayName;

const DialogDescription = forwardRef<ElementRef<typeof DialogPrimitive.Description>, ComponentPropsWithoutRef<typeof DialogPrimitive.Description>>(
  ({ className, ...props }, ref): ReactElement => (
    <DialogPrimitive.Description className={cn('text-[12px] leading-5 text-slate-500', className)} ref={ref} {...props} />
  )
);
DialogDescription.displayName = DialogPrimitive.Description.displayName;

export {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger
};
