import * as ToastPrimitive from '@radix-ui/react-toast';
import { X } from 'lucide-react';
import type { ComponentPropsWithoutRef, ElementRef, ReactElement } from 'react';
import { forwardRef } from 'react';

import { cn } from '../../lib/utils';

export type ToastVariant = 'default' | 'error' | 'success' | 'info';

const ToastProvider = ToastPrimitive.Provider;

const ToastViewport = forwardRef<
  ElementRef<typeof ToastPrimitive.Viewport>,
  ComponentPropsWithoutRef<typeof ToastPrimitive.Viewport>
>(({ className, ...props }, ref): ReactElement => (
  <ToastPrimitive.Viewport
    className={cn(
      'fixed top-4 left-1/2 z-(--z-toast) flex w-max max-w-[calc(100vw-32px)] -translate-x-1/2 flex-col items-center gap-2 outline-none',
      className
    )}
    ref={ref}
    {...props}
  />
));
ToastViewport.displayName = ToastPrimitive.Viewport.displayName;

const Toast = forwardRef<
  ElementRef<typeof ToastPrimitive.Root>,
  ComponentPropsWithoutRef<typeof ToastPrimitive.Root> & { variant?: ToastVariant }
>(({ className, variant: _variant, ...props }, ref): ReactElement => (
  <ToastPrimitive.Root
    className={cn(
      'flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-[12px] font-medium text-slate-700 shadow-sm',
      'data-[state=open]:animate-in data-[state=open]:slide-in-from-top-2 data-[state=open]:fade-in-0 data-[state=open]:duration-200',
      'data-[state=closed]:animate-out data-[state=closed]:slide-out-to-top-2 data-[state=closed]:fade-out-0 data-[state=closed]:duration-200',
      className
    )}
    ref={ref}
    {...props}
  />
));
Toast.displayName = ToastPrimitive.Root.displayName;

const ToastTitle = forwardRef<
  ElementRef<typeof ToastPrimitive.Title>,
  ComponentPropsWithoutRef<typeof ToastPrimitive.Title>
>(({ className, ...props }, ref): ReactElement => (
  <ToastPrimitive.Title className={cn('sr-only', className)} ref={ref} {...props} />
));
ToastTitle.displayName = ToastPrimitive.Title.displayName;

const ToastDescription = forwardRef<
  ElementRef<typeof ToastPrimitive.Description>,
  ComponentPropsWithoutRef<typeof ToastPrimitive.Description>
>(({ className, ...props }, ref): ReactElement => (
  <ToastPrimitive.Description className={cn('leading-none', className)} ref={ref} {...props} />
));
ToastDescription.displayName = ToastPrimitive.Description.displayName;

const ToastClose = forwardRef<
  ElementRef<typeof ToastPrimitive.Close>,
  ComponentPropsWithoutRef<typeof ToastPrimitive.Close>
>(({ className, ...props }, ref): ReactElement => (
  <ToastPrimitive.Close
    className={cn(
      '-mr-1 ml-1 rounded-full p-0.5 text-slate-500 opacity-60 outline-none transition-opacity hover:opacity-100',
      className
    )}
    ref={ref}
    {...props}
  >
    <X className="h-3 w-3" strokeWidth={2} />
  </ToastPrimitive.Close>
));
ToastClose.displayName = ToastPrimitive.Close.displayName;

export { Toast, ToastClose, ToastDescription, ToastProvider, ToastTitle, ToastViewport };
