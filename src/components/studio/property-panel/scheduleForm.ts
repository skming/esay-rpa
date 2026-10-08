import { buildCronExpression, parseCronFields } from '../../../lib/schedulePresentation';

export const SCHEDULE_FREQUENCIES = [
  { value: 'minute', label: '每分钟' },
  { value: 'hour', label: '每小时' },
  { value: 'day', label: '每天' },
  { value: 'weekday', label: '工作日' },
  { value: 'week', label: '每周' },
  { value: 'month', label: '每月' },
  { value: 'custom', label: '自定义 Cron' },
] as const;
export type ScheduleFrequency = (typeof SCHEDULE_FREQUENCIES)[number]['value'];

export function normalizeScheduleCron(value: string): string {
  return value.trim().replace(/\s+/g, ' ');
}

export function getScheduleFrequency(expression: string): ScheduleFrequency {
  const normalized = normalizeScheduleCron(expression);
  const parts = normalized.split(' ');
  if (parts.length !== 5) return 'custom';
  const [minute, hour, day, month, weekday] = parts;
  if (normalized === '* * * * *') return 'minute';
  if (!inRange(minute, 0, 59) || month !== '*') return 'custom';
  if (day === '*' && weekday === '*') {
    if (hour === '*') return 'hour';
    if (inRange(hour, 0, 23)) return 'day';
  }
  if (!inRange(hour, 0, 23)) return 'custom';
  if (day === '*' && weekday === '1-5') return 'weekday';
  if (day === '*' && inRange(weekday, 0, 7)) return 'week';
  if (inRange(day, 1, 31) && weekday === '*') return 'month';
  return 'custom';
}

export function applyScheduleFrequency(frequency: ScheduleFrequency, expression: string): string {
  if (frequency === 'custom') return expression;
  if (frequency === 'minute') return '* * * * *';
  const previous = parseCronFields(expression);
  const minute = inRange(previous.minute, 0, 59) ? previous.minute : '0';
  const hour = inRange(previous.hour, 0, 23) ? previous.hour : '9';
  return buildCronExpression({
    minute,
    hour: frequency === 'hour' ? '*' : hour,
    dayOfMonth:
      frequency === 'month' && inRange(previous.dayOfMonth, 1, 31)
        ? previous.dayOfMonth
        : frequency === 'month'
          ? '1'
          : '*',
    month: '*',
    dayOfWeek:
      frequency === 'week'
        ? inRange(previous.dayOfWeek, 0, 7)
          ? previous.dayOfWeek
          : '1'
        : frequency === 'weekday'
          ? '1-5'
          : '*',
  });
}

function inRange(value: string, min: number, max: number): boolean {
  return /^\d+$/.test(value) && Number(value) >= min && Number(value) <= max;
}
