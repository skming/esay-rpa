import { create } from 'zustand';
import { backend } from '../lib/backendClient';
import type { AiModelMeta } from '../types/electron';

type AiModelCatalogStore = {
  models: AiModelMeta[];
  status: 'idle' | 'loading' | 'ready' | 'error';
  load: (options?: { force?: boolean; signal?: AbortSignal }) => Promise<void>;
};

// 目录是后端状态（含 configured 这类随配置变化的字段），持久化只会拿到过期副本，
// 因此这里不挂 persist；每次进程启动重新拉。
export const useAiModelCatalogStore = create<AiModelCatalogStore>()((set, get) => ({
  models: [],
  status: 'idle',

  load: async ({ force = false, signal } = {}) => {
    if (!force && (get().status === 'ready' || get().status === 'loading')) return;
    set({ status: 'loading' });
    try {
      const data = await backend.listAiModels(signal);
      const list = data.models ?? [];
      if (Array.isArray(list) && list.length > 0) {
        set({ models: list, status: 'ready' });
      } else {
        set({ status: 'error' });
      }
    } catch (err: unknown) {
      // 请求在返回前被取消，不是后端故障，保留上一次的目录
      if ((err as Error).name === 'AbortError') {
        set({ status: get().models.length > 0 ? 'ready' : 'idle' });
      } else {
        set({ status: 'error' });
      }
    }
  },
}));
