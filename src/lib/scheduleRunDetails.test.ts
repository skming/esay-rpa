import { describe, expect, it, vi } from 'vitest';

import type { RunDetail, TaskSnapshot } from '../types/electron';
import { loadScheduleRunDetails } from './scheduleRunDetails';

const run: TaskSnapshot = {
  taskId: 'task-1', flowName: '采集流程', status: 'success', mode: 'run',
  createdAt: '2026-10-08T01:00:00Z', updatedAt: '2026-10-08T01:00:20Z',
  runConfig: { concurrency: 1, failureStrategy: 'stop', scope: 'full', screenshot: false },
  progress: { currentStep: 1, totalSteps: 1, percent: 100, elapsedMs: 20000 },
};

describe('调度运行记录加载', () => {
  it('同时读取本批记录，保留请求顺序及每条记录的读取状态', async () => {
    let resolve!: (detail: RunDetail) => void;
    const pending = new Promise<RunDetail>((done) => { resolve = done; });
    const loadDetail = vi.fn(async (id: string): Promise<RunDetail> => {
      if (id === 'task-1') return pending;
      if (id === 'task-2') throw new Error('后端暂时不可用');
      throw new Error('Task not found');
    });
    const loading = loadScheduleRunDetails(['task-1', 'task-2', 'task-3'], loadDetail);
    expect(loadDetail.mock.calls).toEqual([['task-1'], ['task-2'], ['task-3']]);
    resolve({ run, logs: [] });
    expect(await loading).toEqual([
      { taskId: 'task-1', state: 'ready', run },
      { taskId: 'task-2', state: 'error', message: '后端暂时不可用' },
      { taskId: 'task-3', state: 'missing', message: '运行记录不存在，可能已被清理' },
    ]);
  });

  it('读取失败后可再次读取，不把读取失败当作运行失败', async () => {
    const loadDetail = vi.fn<(_: string) => Promise<RunDetail>>()
      .mockRejectedValueOnce(new Error('读取失败'))
      .mockResolvedValueOnce({ run, logs: [] });
    expect(await loadScheduleRunDetails(['task-1'], loadDetail)).toEqual([
      { taskId: 'task-1', state: 'error', message: '读取失败' },
    ]);
    expect(await loadScheduleRunDetails(['task-1'], loadDetail)).toEqual([
      { taskId: 'task-1', state: 'ready', run },
    ]);
  });

  it('同步抛错和非 Error 拒绝都有可操作的错误提示', async () => {
    expect(await loadScheduleRunDetails(['task-1'], () => { throw new Error('桥接服务不可用'); })).toEqual([
      { taskId: 'task-1', state: 'error', message: '桥接服务不可用' },
    ]);
    expect(await loadScheduleRunDetails(['task-1'], async () => { throw null; })).toEqual([
      { taskId: 'task-1', state: 'error', message: '读取运行记录失败，请重试' },
    ]);
  });

  it('没有任务 ID 时不读取记录', async () => {
    const loadDetail = vi.fn();
    expect(await loadScheduleRunDetails([], loadDetail)).toEqual([]);
    expect(loadDetail).not.toHaveBeenCalled();
  });
});
