import { describe, expect, it } from 'vitest';

import type { ScheduleSnapshot } from '../types/electron';
import { buildCronExpression, countEnabledSchedules, describeCronExpression, filterSchedules, formatScheduleDateTime, parseCronFields, previewNextCronRuns } from './schedulePresentation';

function buildSchedule(overrides: Partial<ScheduleSnapshot> = {}): ScheduleSnapshot {
  return {
    createdAt: '2026-06-10T00:00:00.000Z',
    cronExpression: '0 9 * * *',
    enabled: true,
    name: '每日订单采集',
    scheduleId: 'schedule-1',
    status: 'enabled',
    task: {
      flowName: '订单自动处理',
      mode: 'run',
      selector: '.quote .text::text',
      targetUrl: 'https://quotes.toscrape.com/'
    },
    timezone: 'Asia/Shanghai',
    updatedAt: '2026-06-10T00:00:00.000Z',
    ...overrides
  } as ScheduleSnapshot;
}

describe('schedulePresentation', () => {
  it('把常见 Cron 表达式转换为中文说明', () => {
    expect(describeCronExpression('0 9 * * *')).toBe('每天 09:00');
    expect(describeCronExpression('0 * * * *')).toBe('每小时整点');
    expect(describeCronExpression('*/15 * * * *')).toBe('每 15 分钟');
    expect(describeCronExpression('30 9 * * 1-5')).toBe('工作日 09:30');
  });

  it('按状态和关键字筛选调度并按更新时间倒序', () => {
    const schedules = [
      buildSchedule({ name: '停用任务', scheduleId: 'schedule-1', status: 'disabled', updatedAt: '2026-06-10T00:00:00.000Z' }),
      buildSchedule({ name: '订单任务', scheduleId: 'schedule-2', status: 'enabled', updatedAt: '2026-06-10T01:00:00.000Z' }),
      buildSchedule({ name: '库存任务', scheduleId: 'schedule-3', status: 'enabled', updatedAt: '2026-06-10T02:00:00.000Z' })
    ];

    expect(filterSchedules(schedules, 'enabled', '任务').map((schedule) => schedule.scheduleId)).toEqual(['schedule-3', 'schedule-2']);
    expect(filterSchedules(schedules, 'all', '库存').map((schedule) => schedule.scheduleId)).toEqual(['schedule-3']);
    expect(countEnabledSchedules(schedules)).toBe(2);
  });

  it('格式化调度时间空值和 ISO 时间', () => {
    expect(formatScheduleDateTime(null)).toBe('未计算');
    expect(formatScheduleDateTime('2026-06-10T01:02:03.000Z')).toMatch(/2026-06-10/);
  });

  it('解析、组装并预览 Cron 字段', () => {
    expect(parseCronFields('0 9 * * 1-5')).toEqual({
      dayOfMonth: '*',
      dayOfWeek: '1-5',
      hour: '9',
      minute: '0',
      month: '*'
    });
    expect(buildCronExpression({ dayOfMonth: '*', dayOfWeek: '1-5', hour: '9', minute: '30', month: '*' })).toBe('30 9 * * 1-5');
    expect(previewNextCronRuns('0 9 * * *', new Date('2026-06-10T08:58:00+08:00'), 2)).toHaveLength(2);
  });
});
