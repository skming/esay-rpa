import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ElectronBridgeState } from '../../hooks/useElectronBridge';
import type { RunDetail, ScheduleSnapshot, TaskSnapshot } from '../../types/electron';
import * as tooltips from '../ui/tooltip';
import * as results from './ScheduleRunResultsDialog';
import { ScheduleListTable } from './ScheduleListTable';

const commits = vi.hoisted(() => [] as unknown[]);
vi.mock('react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react')>();
  return {
    ...actual,
    useState: (initialState?: unknown) => {
      const [state, setState] = actual.useState(initialState);
      return [state, (next: unknown) => { commits.push(next); setState(next); }];
    },
  };
});

afterEach(() => vi.restoreAllMocks());

const run: TaskSnapshot = {
  taskId: 'task-1', flowName: '采集流程', status: 'error', error: '执行失败', mode: 'run',
  createdAt: '2026-10-08T01:00:00Z', updatedAt: '2026-10-08T01:00:20Z',
  runConfig: { concurrency: 1, failureStrategy: 'stop', scope: 'full', screenshot: false },
  progress: { currentStep: 1, totalSteps: 1, percent: 100, elapsedMs: 20000 },
};
const schedule: ScheduleSnapshot = {
  scheduleId: 'schedule-1', name: '每日采集', cronExpression: '0 9 * * *', timezone: 'Asia/Shanghai',
  status: 'enabled', task: { mode: 'run', flowId: 'flow-1', flowName: run.flowName },
  createdAt: run.createdAt, updatedAt: run.updatedAt, lastTaskId: run.taskId, lastError: run.error,
};

function deferred() {
  let resolve!: (detail: RunDetail) => void;
  const promise = new Promise<RunDetail>((done) => { resolve = done; });
  return { promise, resolve };
}

function renderRequests(schedules: ScheduleSnapshot[], getRunDetail: (id: string) => Promise<RunDetail>) {
  // SSR 保留真实事件处理器；记录状态提交，检查在途请求是否被关闭或新的选择作废。
  commits.length = 0;
  const trigger = vi.spyOn(tooltips, 'TooltipTrigger');
  const dialog = vi.spyOn(results, 'ScheduleRunResultsDialog');
  renderToStaticMarkup(<ScheduleListTable schedules={schedules} runSummaries={{}}
    electron={{ flows: [], getRunDetail, pushToast: vi.fn() } as unknown as ElectronBridgeState} />);
  return {
    commits,
    open: (name: string) => trigger.mock.calls.find(([props]) => props['aria-label'] === `需处理，查看 ${name} 的运行结果`)?.[0].onClick?.({} as never),
    close: () => dialog.mock.calls[0]?.[0].onClose(),
  };
}

const settle = () => new Promise<void>((resolve) => setImmediate(resolve));

describe('调度运行详情请求归属', () => {
  it('单条记录读取成功后直接进入详情', async () => {
    const view = renderRequests([schedule], vi.fn(async () => ({ run, logs: [] })));
    view.open(schedule.name);
    expect(view.commits).toContainEqual(expect.objectContaining({ taskIds: [run.taskId], entries: [] }));
    await settle();
    expect(view.commits).toContainEqual(run);
  });

  it('关闭加载弹框后，迟到的单条记录不能重新打开详情', async () => {
    const pending = deferred();
    const load = vi.fn(() => pending.promise);
    const view = renderRequests([schedule], load);
    view.open(schedule.name);
    expect(load).toHaveBeenCalledWith(run.taskId);
    view.close();
    view.commits.length = 0;
    pending.resolve({ run, logs: [] });
    await settle();
    expect(view.commits).toEqual([]);
  });

  it('快速切换另一调度时，旧请求不能覆盖新的运行详情', async () => {
    const pending = deferred();
    const newer = { ...run, taskId: 'task-new', flowName: '新流程' };
    const next = { ...schedule, scheduleId: 'schedule-new', name: '新调度', lastTaskId: newer.taskId };
    const view = renderRequests([schedule, next], vi.fn(async (id: string) => id === run.taskId
      ? pending.promise : { run: newer, logs: [] }));
    view.open(schedule.name);
    view.open(next.name);
    await settle();
    expect(view.commits).toContainEqual(newer);
    view.commits.length = 0;
    pending.resolve({ run, logs: [] });
    await settle();
    expect(view.commits).toEqual([]);
  });

  it('单条记录不存在时保留错误弹框，不静默关闭或生成详情', async () => {
    const view = renderRequests([schedule], vi.fn(async () => { throw new Error('Task not found'); }));
    view.open(schedule.name);
    await settle();
    expect(view.commits).toContainEqual(expect.objectContaining({ entries: [
      { taskId: run.taskId, state: 'missing', message: '运行记录不存在，可能已被清理' },
    ] }));
    expect(view.commits).not.toContainEqual(run);
  });
});
