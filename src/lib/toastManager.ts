import { toast } from '../components/ui/toast';

export type ToastKind = 'success' | 'error' | 'info';

const TOAST_DEDUPE_WINDOW_MS = 2_000;
const TOAST_TIMEOUT_MS: Record<ToastKind, number> = {
  error: 7_000,
  info: 4_000,
  success: 3_000,
};
const TOAST_TITLE: Record<ToastKind, string> = {
  error: '错误',
  info: '提示',
  success: '成功',
};

type RecentToast = {
  createdAt: number;
  id: string;
};

const recentToasts = new Map<string, RecentToast>();
let toastSequence = 0;

export function pushAppToast(type: ToastKind, message: string): string {
  const now = Date.now();
  const dedupeKey = `${type}\u0000${message}`;
  const recentToast = recentToasts.get(dedupeKey);
  const id = recentToast !== undefined && now - recentToast.createdAt < TOAST_DEDUPE_WINDOW_MS
    ? recentToast.id
    : `toast-${now}-${++toastSequence}`;

  recentToasts.set(dedupeKey, { createdAt: now, id });
  return toast.add({
    description: message,
    id,
    onRemove: () => {
      if (recentToasts.get(dedupeKey)?.id === id) recentToasts.delete(dedupeKey);
    },
    priority: type === 'error' ? 'high' : 'low',
    timeout: TOAST_TIMEOUT_MS[type],
    title: TOAST_TITLE[type],
    type,
  });
}

export function dismissAppToast(toastId: string): void {
  toast.close(toastId);
}

export function clearAppToasts(): void {
  recentToasts.clear();
  toast.close();
}
