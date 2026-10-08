import { describe, expect, it } from 'vitest';

import { applyScheduleFrequency, getScheduleFrequency, normalizeScheduleCron } from './scheduleForm';

describe('scheduleForm', () => {
  it.each([
    ['* * * * *', 'minute'],
    ['15 * * * *', 'hour'],
    ['30 9 * * *', 'day'],
    ['30 9 * * 1-5', 'weekday'],
    ['0 9 * * 7', 'week'],
    ['0 9 31 * *', 'month'],
    ['*/15 * * * *', 'custom'],
    ['0 9 * 2 *', 'custom'],
    ['0 9 1 * 1', 'custom'],
    ['60 9 * * *', 'custom'],
    ['0 24 * * *', 'custom'],
    ['0 9 32 * *', 'custom'],
    ['0 9 * * 8', 'custom'],
    ['0 0 9 * * *', 'custom'],
  ] as const)('正确识别 %s，复杂或越界规则保留自定义模式', (expression, frequency) => {
    expect(getScheduleFrequency(expression)).toBe(frequency);
  });

  it('切换频率保留有效时间，并清除不再适用的日期限制', () => {
    expect(applyScheduleFrequency('weekday', '30 16 31 * *')).toBe('30 16 * * 1-5');
    expect(applyScheduleFrequency('week', '30 16 * * 1-5')).toBe('30 16 * * 1');
    expect(applyScheduleFrequency('day', '30 16 * * 6')).toBe('30 16 * * *');
    expect(applyScheduleFrequency('month', '30 16 * * 6')).toBe('30 16 1 * *');
    expect(applyScheduleFrequency('month', '30 16 31 * *')).toBe('30 16 31 * *');
    expect(applyScheduleFrequency('hour', '30 16 * * *')).toBe('30 * * * *');
    expect(applyScheduleFrequency('minute', '30 16 * * *')).toBe('* * * * *');
  });

  it('从复杂或无效表达式切换时使用有效默认时间，自定义模式不改写原表达式', () => {
    expect(applyScheduleFrequency('day', '*/15 * * * *')).toBe('0 9 * * *');
    expect(applyScheduleFrequency('week', '99 99 * * 99')).toBe('0 9 * * 1');
    expect(applyScheduleFrequency('custom', '  */15 * * * *  ')).toBe('  */15 * * * *  ');
    expect(normalizeScheduleCron('  0\t9  *\n*  * ')).toBe('0 9 * * *');
    expect(getScheduleFrequency('  *  *\t* * *  ')).toBe('minute');
  });
});
