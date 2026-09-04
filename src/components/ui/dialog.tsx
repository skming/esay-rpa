import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';
import { IconButton } from './button';

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogPortal = DialogPrimitive.Portal;
const DialogClose = DialogPrimitive.Close;

function DialogOverlay({ className, ...props }: ComponentProps<typeof DialogPrimitive.Overlay>): ReactElement {
  return (
    <DialogPrimitive.Overlay className={cn('fixed inset-0 z-(--z-modal-backdrop) bg-slate-950/45 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:duration-200 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:duration-150', className)} data-slot="dialog-overlay" {...props} />
  );
}

type DialogContentProps = ComponentProps<typeof DialogPrimitive.Content> & {
  showClose?: boolean;
};

function DialogContent({ children, className, showClose = true, ...props }: DialogContentProps): ReactElement {
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Content
        className={cn(
          'fixed left-1/2 top-1/2 z-(--z-modal) flex w-110 max-h-[calc(100vh-48px)] max-w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-lg outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:duration-200 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:duration-150',
          className
        )}
        data-slot="dialog-content"
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
  );
}

function DialogHeader({ className, ...props }: ComponentProps<'div'>): ReactElement {
  return <div className={cn('flex shrink-0 flex-col gap-1.5 px-6 pb-4 pt-6 pr-12', className)} data-slot="dialog-header" {...props} />;
}

function DialogBody({ className, ...props }: ComponentProps<'div'>): ReactElement {
  return <div className={cn('min-h-0 flex-1 overflow-y-auto px-5 py-4', className)} data-slot="dialog-body" {...props} />;
}

function DialogFooter({ className, ...props }: ComponentProps<'div'>): ReactElement {
  return (
    <div
      className={cn('flex shrink-0 items-center justify-end gap-2 border-t border-slate-100 px-5 pb-4 pt-3', className)}
      data-slot="dialog-footer"
      {...props}
    />
  );
}

function DialogTitle({ className, ...props }: ComponentProps<typeof DialogPrimitive.Title>): ReactElement {
  return <DialogPrimitive.Title className={cn('text-sm font-bold leading-none text-slate-900', className)} data-slot="dialog-title" {...props} />;
}

function DialogDescription({ className, ...props }: ComponentProps<typeof DialogPrimitive.Description>): ReactElement {
  return (
    <DialogPrimitive.Description className={cn('text-[12px] leading-5 text-slate-500', className)} data-slot="dialog-description" {...props} />
  );
}

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
