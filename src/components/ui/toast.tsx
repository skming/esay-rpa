import { Toast as ToastPrimitive } from '@base-ui/react/toast';
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ReactElement } from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

const toast = ToastPrimitive.createToastManager();

function ToastProvider(props: ToastPrimitive.Provider.Props): ReactElement {
  return <ToastPrimitive.Provider {...props} />;
}

function ToastPortal(props: ToastPrimitive.Portal.Props): ReactElement {
  return <ToastPrimitive.Portal data-slot="toast-portal" {...props} />;
}

function ToastViewport({ className, ...props }: ToastPrimitive.Viewport.Props): ReactElement {
  return (
    <ToastPrimitive.Viewport
      className={cn(
        'pointer-events-none fixed inset-x-4 top-4 z-(--z-toast) mx-auto w-auto max-w-sm outline-none',
        className
      )}
      data-slot="toast-viewport"
      {...props}
    />
  );
}

function Toast({ className, ...props }: ToastPrimitive.Root.Props): ReactElement {
  return (
    <ToastPrimitive.Root
      className={cn(
        'group/toast pointer-events-auto absolute left-0 top-0 z-[calc(1000-var(--toast-index))] w-full origin-top rounded-xl border bg-white text-ink shadow-lg outline-none select-none will-change-transform focus-visible:ring-2 focus-visible:ring-blue-500/30',
        '[--gap:0.5rem] [--height:var(--toast-frontmost-height,var(--toast-height))] [--offset-y:calc(var(--toast-offset-y)+calc(var(--toast-index)*var(--gap))+var(--toast-swipe-movement-y))] [--peek:0.45rem] [--scale:calc(max(0,1-(var(--toast-index)*0.04)))] [--shrink:calc(1-var(--scale))]',
        'h-(--height) [transform:translateX(var(--toast-swipe-movement-x))_translateY(calc(var(--toast-swipe-movement-y)+(var(--toast-index)*var(--peek))+(var(--shrink)*var(--height))))_scale(var(--scale))] [transition:transform_300ms_cubic-bezier(0.22,1,0.36,1),opacity_300ms,height_150ms]',
        "after:absolute after:left-0 after:top-full after:h-[calc(var(--gap)+1px)] after:w-full after:content-['']",
        'data-expanded:h-(--toast-height) data-expanded:[transform:translateX(var(--toast-swipe-movement-x))_translateY(var(--offset-y))]',
        'data-limited:opacity-0 data-starting-style:[transform:translateY(-150%)]',
        '[&[data-ending-style]:not([data-limited]):not([data-swipe-direction])]:[transform:translateY(-150%)]',
        'data-ending-style:data-[swipe-direction=down]:[transform:translateY(calc(var(--toast-swipe-movement-y)+150%))]',
        'data-ending-style:data-[swipe-direction=left]:[transform:translateX(calc(var(--toast-swipe-movement-x)-150%))_translateY(var(--offset-y))]',
        'data-ending-style:data-[swipe-direction=right]:[transform:translateX(calc(var(--toast-swipe-movement-x)+150%))_translateY(var(--offset-y))]',
        'data-ending-style:data-[swipe-direction=up]:[transform:translateY(calc(var(--toast-swipe-movement-y)-150%))]',
        'data-expanded:data-ending-style:data-[swipe-direction=down]:[transform:translateY(calc(var(--toast-swipe-movement-y)+150%))]',
        'data-expanded:data-ending-style:data-[swipe-direction=left]:[transform:translateX(calc(var(--toast-swipe-movement-x)-150%))_translateY(var(--offset-y))]',
        'data-expanded:data-ending-style:data-[swipe-direction=right]:[transform:translateX(calc(var(--toast-swipe-movement-x)+150%))_translateY(var(--offset-y))]',
        'data-expanded:data-ending-style:data-[swipe-direction=up]:[transform:translateY(calc(var(--toast-swipe-movement-y)-150%))]',
        className
      )}
      data-slot="toast"
      {...props}
    />
  );
}

function ToastContent({ className, ...props }: ToastPrimitive.Content.Props): ReactElement {
  return (
    <ToastPrimitive.Content
      className={cn(
        'flex h-full min-h-11 items-center gap-2.5 overflow-hidden px-3 py-2.5 transition-opacity duration-200 ease-[cubic-bezier(0.22,1,0.36,1)] data-behind:opacity-0 data-expanded:opacity-100',
        className
      )}
      data-slot="toast-content"
      {...props}
    />
  );
}

function ToastTitle({ className, ...props }: ToastPrimitive.Title.Props): ReactElement {
  return <ToastPrimitive.Title className={cn('text-xs font-medium', className)} data-slot="toast-title" {...props} />;
}

function ToastDescription({ className, ...props }: ToastPrimitive.Description.Props): ReactElement {
  return (
    <ToastPrimitive.Description
      className={cn('break-words text-xs font-medium leading-5 text-ink-2', className)}
      data-slot="toast-description"
      {...props}
    />
  );
}

function ToastAction({ className, render = <Button size="sm" variant="outline" />, ...props }: ToastPrimitive.Action.Props): ReactElement {
  return <ToastPrimitive.Action className={cn('shrink-0', className)} data-slot="toast-action" render={render} {...props} />;
}

function ToastClose({ className, children, render = <Button size="icon" variant="ghost" />, ...props }: ToastPrimitive.Close.Props): ReactElement {
  return (
    <ToastPrimitive.Close
      aria-label="关闭通知"
      className={cn('-mr-1 h-6 w-6 shrink-0 text-ink-3 opacity-60 hover:text-ink hover:opacity-100', className)}
      data-slot="toast-close"
      render={render}
      {...props}
    >
      {children ?? <X aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={1.75} />}
    </ToastPrimitive.Close>
  );
}

const TOAST_ICON: Record<string, LucideIcon> = {
  error: AlertCircle,
  info: Info,
  success: CheckCircle2,
};

const ICON_CLASS: Record<string, string> = {
  error: 'border-red-200 bg-red-50 text-red-600',
  info: 'border-blue-200 bg-blue-50 text-blue-600',
  success: 'border-emerald-200 bg-emerald-50 text-emerald-600',
};

const BORDER_CLASS: Record<string, string> = {
  error: 'border-red-200/80',
  info: 'border-blue-200/80',
  success: 'border-emerald-200/80',
};

function ToastIcon({ type }: { type: string | undefined }): ReactElement | null {
  if (type === undefined) return null;
  const Icon = TOAST_ICON[type];
  if (Icon === undefined) return null;

  return (
    <span className={cn('grid h-6 w-6 shrink-0 place-items-center rounded-md border', ICON_CLASS[type])} data-slot="toast-icon">
      <Icon aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={1.75} />
    </span>
  );
}

function ToastList(): ReactElement {
  const { toasts } = ToastPrimitive.useToastManager();

  return (
    <>
      {toasts.map((toastItem) => (
        <Toast
          className={toastItem.type === undefined ? undefined : BORDER_CLASS[toastItem.type]}
          key={toastItem.id}
          swipeDirection={['up', 'right']}
          toast={toastItem}
        >
          <ToastContent>
            <ToastIcon type={toastItem.type} />
            <div className="flex min-w-0 flex-1 flex-col">
              <ToastTitle className="sr-only" />
              <ToastDescription />
            </div>
            <ToastAction />
            <ToastClose />
          </ToastContent>
        </Toast>
      ))}
    </>
  );
}

function Toaster({ children, toastManager = toast, ...props }: ToastPrimitive.Provider.Props): ReactElement {
  return (
    <ToastProvider limit={3} toastManager={toastManager} {...props}>
      {children}
      <ToastPortal>
        <ToastViewport>
          <ToastList />
        </ToastViewport>
      </ToastPortal>
    </ToastProvider>
  );
}

const createToastManager = ToastPrimitive.createToastManager;
const useToastManager = ToastPrimitive.useToastManager;

export {
  Toaster,
  Toast,
  ToastAction,
  ToastClose,
  ToastContent,
  ToastDescription,
  ToastPortal,
  ToastProvider,
  ToastTitle,
  ToastViewport,
  createToastManager,
  toast,
  useToastManager,
};
