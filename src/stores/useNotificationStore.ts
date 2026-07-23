import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type NotificationKind = 'success' | 'error' | 'info' | 'warning';

export type AppNotification = {
  id: string;
  kind: NotificationKind;
  title: string;
  body?: string;
  at: string; // ISO timestamp
  read: boolean;
};

type NotificationStore = {
  notifications: AppNotification[];
  push: (n: Omit<AppNotification, 'id' | 'read'>) => void;
  markAllRead: () => void;
  markRead: (id: string) => void;
  clear: () => void;
  unreadCount: () => number;
};

const MAX_NOTIFICATIONS = 50;

export const useNotificationStore = create<NotificationStore>()(
  persist(
    (set, get) => ({
      notifications: [],

      push: (n) => {
        const entry: AppNotification = {
          ...n,
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          read: false,
        };
        set((s) => ({
          notifications: [entry, ...s.notifications].slice(0, MAX_NOTIFICATIONS),
        }));
      },

      markRead: (id) =>
        set((s) => ({
          notifications: s.notifications.map((n) => (n.id === id ? { ...n, read: true } : n)),
        })),

      markAllRead: () =>
        set((s) => ({
          notifications: s.notifications.map((n) => ({ ...n, read: true })),
        })),

      clear: () => set({ notifications: [] }),

      unreadCount: () => get().notifications.filter((n) => !n.read).length,
    }),
    { name: 'rpa-studio.notifications' }
  )
);
