import { describe, expect, it } from 'vitest';

import type { ScheduleRunSummary, ScheduleSnapshot, TaskSnapshot } from '../types/electron';
import { buildOperationalHealthSnapshot } from './operationalHealth';

function buildRun(overrides: Partial<TaskSnapshot>): TaskSnapshot {
  return {
    createdAt: '2026-09-01T01:00:00.000Z',
    flowName: '订单同步',
    mode: 'run',
    progress: { currentStep: 1, elapsedMs: 2000, percent: 50, totalSteps: 2 },
    runConfig: { concurrency: 1, failureStrategy: 'stop', scope: 'full', screenshot: true },
    status: 'success',
    taskId: 'run-1',
    updatedAt: '2026-09-01T01:01:00.000Z',
    ...overrides,
  };
}

function buildSchedule(overrides: Partial<ScheduleSnapshot>): ScheduleSnapshot {
  return {
    createdAt: '2026-09-01T00:00:00.000Z',
    cronExpression: '0 9 * * *',
    name: '每日同步',
    scheduleId: 'schedule-1',
    status: 'enabled',
    task: { flowName: '订单同步', mode: 'run' },
    timezone: 'Asia/Shanghai',
    updatedAt: '2026-09-01T00:30:00.000Z',
    ...overrides,
  } as ScheduleSnapshot;
}

describe('buildOperationalHealthSnapshot', () => {
  it.each(['failed', 'partial', 'stopped'] as const)('最近批次 %s 且没有 lastError 时也展示调度待处理', (status) => {
    const summary: ScheduleRunSummary = {
      scheduleId: 'schedule-1', total: 2, running: 0, success: 1, failed: 1, stopped: 0, status, taskIds: ['run-1', 'run-2'],
    };
    const snapshot = buildOperationalHealthSnapshot([], [buildSchedule({ lastError: null })], { 'schedule-1': summary });
    expect(snapshot.attention).toHaveLength(1);
    expect(snapshot.attention[0].kind).toBe('schedule-error');
    expect(snapshot.attention[0].schedule?.scheduleId).toBe('schedule-1');
    expect(snapshot.attention[0].detail).toContain('最近一批运行');
  });

  it.each(['success', 'running', 'empty'] as const)('批次 %s 不被误列为待处理', (status) => {
    const summary: ScheduleRunSummary = {
      scheduleId: 'schedule-1', total: 1, running: 0, success: 0, failed: 0, stopped: 0, status, taskIds: ['run-1'],
    };
    expect(buildOperationalHealthSnapshot([], [buildSchedule({ lastError: null })], { 'schedule-1': summary }).attention).toEqual([]);
  });

  it('停用调度不展示历史批次错误', () => {
    const summary: ScheduleRunSummary = {
      scheduleId: 'schedule-1', total: 1, running: 0, success: 0, failed: 1, stopped: 0, status: 'failed', taskIds: ['run-1'],
    };
    expect(buildOperationalHealthSnapshot([], [buildSchedule({ status: 'disabled' })], { 'schedule-1': summary }).attention).toEqual([]);
  });

  it('优先返回等待确认、运行失败与仍启用的调度错误', () => {
    const runs = [
      buildRun({ flowName: '失败流程', status: 'error', taskId: 'run-error' }),
      buildRun({ flowName: '确认流程', status: 'awaiting_confirmation', taskId: 'run-confirmation' }),
      buildRun({ flowName: '成功流程', status: 'success', taskId: 'run-success' }),
    ];
    const schedules = [
      buildSchedule({ lastError: 'Cron 无效', scheduleId: 'schedule-error' }),
      buildSchedule({ lastError: '历史错误', scheduleId: 'schedule-disabled', status: 'disabled' }),
    ];

    const snapshot = buildOperationalHealthSnapshot(runs, schedules);

    expect(snapshot.attention.map((item) => item.kind)).toEqual(['confirmation', 'run-error', 'schedule-error']);
    expect(snapshot.recentResult).toEqual({ failed: 1, sampleSize: 2, succeeded: 1 });
  });

  it('同一流程已有更新的成功记录时不再把历史失败列为待处理', () => {
    const snapshot = buildOperationalHealthSnapshot([
      buildRun({ flowId: 'flow-1', status: 'error', taskId: 'older-error', updatedAt: '2026-09-01T01:00:00.000Z' }),
      buildRun({ flowId: 'flow-1', status: 'success', taskId: 'newer-success', updatedAt: '2026-09-01T02:00:00.000Z' }),
    ], []);

    expect(snapshot.attention).toEqual([]);
  });
});
