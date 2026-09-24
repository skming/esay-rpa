import { describe, expect, it, vi } from 'vitest';

import type { BackendClient } from './backendClient';
import { createBrowserBridge } from './browserBridge';

describe('getRunDetail', () => {
  it('同时读取任务快照和持久化日志', async () => {
    const run = { taskId: 't-1', result: { count: 0, values: [] } };
    const logs = [{ id: 'log-1', message: '任务完成' }];
    const getTask = vi.fn().mockResolvedValue(run);
    const getLogs = vi.fn().mockResolvedValue(logs);
    const bridge = createBrowserBridge({ backendClient: { getTask, getLogs } as unknown as BackendClient });

    expect(await bridge.getRunDetail('t-1')).toEqual({ ok: true, data: { run, logs } });
    expect(getTask).toHaveBeenCalledWith('t-1');
    expect(getLogs).toHaveBeenCalledWith('t-1');
  });

  it('日志读取失败时明确返回错误', async () => {
    const bridge = createBrowserBridge({ backendClient: {
      getTask: vi.fn().mockResolvedValue({ taskId: 't-1' }),
      getLogs: vi.fn().mockRejectedValue(new Error('日志不可用')),
    } as unknown as BackendClient });

    expect(await bridge.getRunDetail('t-1')).toEqual({ ok: false, error: '日志不可用' });
  });
});
