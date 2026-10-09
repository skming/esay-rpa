import type { ComponentProps } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ScheduleSnapshot, TaskSnapshot } from '../../types/electron';
import * as buttons from '../ui/button';
import * as tooltips from '../ui/tooltip';
import { ScheduleRunResultsDialog, type ScheduleRunBatch } from './ScheduleRunResultsDialog';

vi.mock('../ui/dialog', () => {
  const Container = ({ children }: ComponentProps<'div'>) => <div>{children}</div>;
  return {
    Dialog: Container, DialogContent: Container, DialogHeader: Container, DialogTitle: Container,
    DialogDescription: Container, DialogBody: Container, DialogFooter: Container,
  };
});

afterEach(() => vi.restoreAllMocks());

const schedule: ScheduleSnapshot = {
  scheduleId: 'schedule-1', name: '每日订单采集', cronExpression: '0 9 * * *', timezone: 'Asia/Shanghai',
  status: 'enabled', task: { mode: 'run', flowId: 'flow-1', flowName: '订单采集' },
  createdAt: '2026-10-08T00:00:00Z', updatedAt: '2026-10-08T01:00:00Z',
  lastRunAt: '2026-10-08T01:00:00Z',
};
const run: TaskSnapshot = {
  taskId: 'task-1', flowName: '订单采集', status: 'success', mode: 'run',
  createdAt: '2026-10-08T01:00:00Z', updatedAt: '2026-10-08T01:00:20Z',
  runConfig: { concurrency: 1, failureStrategy: 'stop', scope: 'full', screenshot: false },
  progress: { currentStep: 1, totalSteps: 1, percent: 100, elapsedMs: 20000 },
  result: { count: 18, url: '', selector: '', values: [] },
};

function render(batch: ScheduleRunBatch, loading = false, onRefresh = vi.fn(), onClose = vi.fn(), onInspectRun = vi.fn()) {
  return renderToStaticMarkup(<ScheduleRunResultsDialog batch={batch} loading={loading}
    onClose={onClose} onInspectRun={onInspectRun} onRefresh={onRefresh} />);
}

describe('调度运行结果弹框', () => {
  it('加载时展示调度、触发时间、时区和真实批次数量', () => {
    const html = render({ schedule, taskIds: ['task-1', 'task-2'], entries: [], runAt: '2026-10-08T02:00:00Z' }, true);
    expect(html).toContain('运行结果 · 每日订单采集');
    expect(html).toContain('2026-10-08 10:00');
    expect(html).toContain('Asia/Shanghai');
    expect(html).toContain('共 2 个流程');
    expect(html).toContain('正在读取运行结果');
    expect(html).not.toContain('成功 0');
  });

  it('复用紧凑运行列表，展示统计、耗时和输出，失败原因按需查看', () => {
    const content = vi.spyOn(tooltips, 'TooltipContent');
    const trigger = vi.spyOn(tooltips, 'TooltipTrigger');
    const failed = { ...run, taskId: 'task-2', flowName: '库存采集', status: 'error' as const, error: '等待页面元素超时' };
    const onInspectRun = vi.fn();
    const html = render({ schedule, taskIds: ['task-1', 'task-2'], entries: [
      { taskId: 'task-1', state: 'ready', run }, { taskId: 'task-2', state: 'ready', run: failed },
    ] }, false, vi.fn(), vi.fn(), onInspectRun);
    expect(html).toContain('成功 1 · 失败 1');
    expect(html).toContain('00:20');
    expect(html).toContain('18 条');
    expect(html).toContain('2026-10-08 09:00');
    expect(html).toContain('本批运行流程');
    expect(html).not.toContain('<section');
    expect(html).not.toContain('等待页面元素超时');
    expect(content.mock.calls.some(([props]) => props.children === failed.error)).toBe(true);
    const status = trigger.mock.calls.find(([props]) => props['aria-label'] === '失败，查看 库存采集 的运行详情');
    status?.[0].onClick?.({} as never);
    expect(onInspectRun).toHaveBeenCalledWith(failed);
  });

  it('缺失和读取失败的记录保留各自原因及 ID，提供重试与关闭', () => {
    const button = vi.spyOn(buttons, 'Button');
    const onRefresh = vi.fn();
    const onClose = vi.fn();
    const html = render({ schedule, taskIds: ['task-1', 'task-2'], entries: [
      { taskId: 'task-1', state: 'missing', message: '运行记录不存在，可能已被清理' },
      { taskId: 'task-2', state: 'error', message: '<script>后端读取失败</script>' },
    ] }, false, onRefresh, onClose);
    expect(html).toContain('记录不可用 2');
    expect(html).toContain('运行记录不存在');
    expect(html).toContain('&lt;script&gt;后端读取失败&lt;/script&gt;');
    expect(html).not.toContain('<script>');
    expect(html).toContain('运行 ID task-1');
    expect(html).toContain('运行 ID task-2');
    button.mock.calls.find(([props]) => props.children === '重试读取')?.[0].onClick?.({} as never);
    button.mock.calls.find(([props]) => props.children === '关闭')?.[0].onClick?.({} as never);
    expect(onRefresh).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
    expect(html).not.toContain('失败 2');
  });
});
