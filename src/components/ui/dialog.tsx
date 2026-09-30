import { Dialog as DialogPrimitive } from '@base-ui/react/dialog';
import { X } from 'lucide-react';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';
import { IconButton } from './button';

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogPortal = DialogPrimitive.Portal;
const DialogClose = DialogPrimitive.Close;

function DialogOverlay({ className, ...props }: ComponentProps<typeof DialogPrimitive.Backdrop>): ReactElement {
  return (
    <DialogPrimitive.Backdrop className={cn('fixed inset-0 z-(--z-modal-backdrop) bg-ink/45 backdrop-blur-[2px] data-open:animate-in data-open:fade-in-0 data-open:duration-200 data-closed:animate-out data-closed:fade-out-0 data-closed:duration-150', className)} data-slot="dialog-overlay" {...props} />
  );
}

type DialogContentProps = ComponentProps<typeof DialogPrimitive.Popup> & {
  showClose?: boolean;
};

function DialogContent({ children, className, showClose = true, ...props }: DialogContentProps): ReactElement {
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Popup
        className={cn(
          'fixed left-1/2 top-1/2 z-(--z-modal) flex w-110 max-h-[calc(100vh-48px)] max-w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-rule bg-surface text-ink shadow-lg outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-open:duration-200 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95 data-closed:duration-150',
          className
        )}
        data-slot="dialog-content"
        {...props}
      >
        {children}
        {showClose && (
          <DialogPrimitive.Close render={<IconButton className="absolute right-3 top-3 h-7 w-7" label="关闭弹窗" />}>
            <X className="h-3.5 w-3.5" strokeWidth={1.5} />
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Popup>
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
      className={cn('flex shrink-0 items-center justify-end gap-2 border-t border-rule px-5 pb-4 pt-3', className)}
      data-slot="dialog-footer"
      {...props}
    />
  );
}

function DialogTitle({ className, ...props }: ComponentProps<typeof DialogPrimitive.Title>): ReactElement {
  return <DialogPrimitive.Title className={cn('text-sm font-bold leading-none text-ink', className)} data-slot="dialog-title" {...props} />;
}

function DialogDescription({ className, ...props }: ComponentProps<typeof DialogPrimitive.Description>): ReactElement {
  return (
    <DialogPrimitive.Description className={cn('text-[12px] leading-5 text-ink-3', className)} data-slot="dialog-description" {...props} />
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
