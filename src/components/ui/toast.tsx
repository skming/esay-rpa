import * as ToastPrimitive from '@radix-ui/react-toast';
import { X } from 'lucide-react';
import type { ComponentProps, ReactElement } from 'react';

import { cn } from '../../lib/utils';

export type ToastVariant = 'default' | 'error' | 'success' | 'info';

const ToastProvider = ToastPrimitive.Provider;

function ToastViewport({ className, ...props }: ComponentProps<typeof ToastPrimitive.Viewport>): ReactElement {
  return (
    <ToastPrimitive.Viewport
      className={cn(
        'fixed top-4 left-1/2 z-(--z-toast) flex w-max max-w-[calc(100vw-32px)] -translate-x-1/2 flex-col items-center gap-2 outline-none',
        className
      )}
      data-slot="toast-viewport"
      {...props}
    />
  );
}

function Toast({
  className,
  variant: _variant,
  ...props
}: ComponentProps<typeof ToastPrimitive.Root> & { variant?: ToastVariant }): ReactElement {
  return (
    <ToastPrimitive.Root
      className={cn(
        'flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-[12px] font-medium text-slate-700 shadow-sm',
        'data-[state=open]:animate-in data-[state=open]:slide-in-from-top-2 data-[state=open]:fade-in-0 data-[state=open]:duration-200',
        'data-[state=closed]:animate-out data-[state=closed]:slide-out-to-top-2 data-[state=closed]:fade-out-0 data-[state=closed]:duration-200',
        className
      )}
      data-slot="toast"
      {...props}
    />
  );
}

function ToastTitle({ className, ...props }: ComponentProps<typeof ToastPrimitive.Title>): ReactElement {
  return <ToastPrimitive.Title className={cn('sr-only', className)} data-slot="toast-title" {...props} />;
}

function ToastDescription({ className, ...props }: ComponentProps<typeof ToastPrimitive.Description>): ReactElement {
  return <ToastPrimitive.Description className={cn('leading-none', className)} data-slot="toast-description" {...props} />;
}

function ToastClose({ className, ...props }: ComponentProps<typeof ToastPrimitive.Close>): ReactElement {
  return (
    <ToastPrimitive.Close
      className={cn(
        '-mr-1 ml-1 rounded-full p-0.5 text-slate-500 opacity-60 outline-none transition-opacity hover:opacity-100',
        className
      )}
      data-slot="toast-close"
      {...props}
    >
      <X className="h-3 w-3" strokeWidth={2} />
    </ToastPrimitive.Close>
  );
}

export { Toast, ToastClose, ToastDescription, ToastProvider, ToastTitle, ToastViewport };
