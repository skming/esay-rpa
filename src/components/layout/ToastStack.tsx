import * as icons from 'lucide-react';
import { AlertCircle, CheckCircle2, Info } from 'lucide-react';
import type { ReactElement } from 'react';
import { useCallback, useEffect, useState } from 'react';

import type { BridgeToast } from '../../hooks/useElectronBridge';
import { Toast, ToastClose, ToastDescription, ToastProvider, ToastTitle, ToastViewport } from '../ui/toast';

const AUTO_DISMISS_MS: Record<BridgeToast['type'], number> = {
  success: 3000,
  info:    4000,
  error:   7000,
};

const ICON_CLASS: Record<BridgeToast['type'], string> = {
  error:   'text-red-500',
  info:    'text-blue-500',
  success: 'text-emerald-500',
};

const DEFAULT_ICON: Record<BridgeToast['type'], icons.LucideIcon> = {
  error:   AlertCircle,
  info:    Info,
  success: CheckCircle2,
};

function resolveIcon(name: string | undefined, fallback: icons.LucideIcon): icons.LucideIcon {
  if (!name) return fallback;
  const Icon = (icons as Record<string, unknown>)[name];
  return typeof Icon === 'function' ? (Icon as icons.LucideIcon) : fallback;
}

// Exit animation duration must match data-[state=closed]:duration-200 in toast.tsx
const EXIT_DURATION_MS = 200;

function ToastItem({ toast, onDismiss }: { toast: BridgeToast; onDismiss: (id: number) => void }): ReactElement {
  const [open, setOpen] = useState(true);
  const Icon = resolveIcon(toast.icon, DEFAULT_ICON[toast.type]);

  // Let exit animation play before removing from the array
  const close = useCallback(() => {
    setOpen(false);
    setTimeout(() => onDismiss(toast.id), EXIT_DURATION_MS);
  }, [toast.id, onDismiss]);

  useEffect(() => {
    const timer = setTimeout(close, AUTO_DISMISS_MS[toast.type]);
    return () => clearTimeout(timer);
  }, [toast.type, close]);

  return (
    <Toast onOpenChange={(o) => { if (!o) close(); }} open={open}>
      <ToastTitle>{toast.type}</ToastTitle>
      <Icon className={`h-3.5 w-3.5 shrink-0 ${ICON_CLASS[toast.type]}`} strokeWidth={1.75} />
      <ToastDescription>{toast.message}</ToastDescription>
      <ToastClose aria-label="关闭通知" />
    </Toast>
  );
}

export function ToastStack({
  onDismiss,
  toasts,
}: {
  onDismiss: (toastId: number) => void;
  toasts: BridgeToast[];
}): ReactElement {
  return (
    <ToastProvider swipeDirection="up">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} onDismiss={onDismiss} toast={toast} />
      ))}
      <ToastViewport />
    </ToastProvider>
  );
}
