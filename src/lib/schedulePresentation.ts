import type { ScheduleSnapshot } from '../types/electron';

export type ScheduleFilter = 'all' | 'enabled' | 'disabled';
/** Decomposed cron expression fields (standard 5-part, no seconds). */
export type CronFields = {
  minute: string;
  hour: string;
  dayOfMonth: string;
  month: string;
  dayOfWeek: string;
};

/** Filters and sorts schedule list by status and free-text search against name/cron/timezone/flowName. */
export function filterSchedules(schedules: ScheduleSnapshot[], filter: ScheduleFilter, query: string): ScheduleSnapshot[] {
  const normalizedQuery = query.trim().toLowerCase();
  return [...schedules]
    .filter((schedule) => filter === 'all' || schedule.status === filter)
    .filter((schedule) => {
      if (normalizedQuery.length === 0) {
        return true;
      }
      return `${schedule.name} ${schedule.cronExpression} ${schedule.timezone} ${schedule.task.flowName}`.toLowerCase().includes(normalizedQuery);
    })
    .sort((left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime());
}

/** Converts a cron expression to a short human-readable Chinese description for common patterns. */
export function describeCronExpression(expression: string): string {
  const normalized = expression.trim().replace(/\s+/g, ' ');
  const parts = normalized.split(' ');
  const fields = parts.length === 6 ? parts.slice(1) : parts;
  if (fields.length !== 5) {
    return normalized;
  }
  const [minute, hour, dayOfMonth, month, dayOfWeek] = fields;
  if (minute === '0' && hour === '*' && dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    return '每小时整点';
  }
  if (minute.startsWith('*/') && hour === '*' && dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    return `每 ${minute.slice(2)} 分钟`;
  }
  if (isNumeric(minute) && isNumeric(hour) && dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    return `每天 ${padTime(hour)}:${padTime(minute)}`;
  }
  if (isNumeric(minute) && isNumeric(hour) && dayOfMonth === '*' && month === '*' && dayOfWeek === '1-5') {
    return `工作日 ${padTime(hour)}:${padTime(minute)}`;
  }
  return normalized;
}

export function parseCronFields(expression: string): CronFields {
  const fields = normalizeCronParts(expression);
  return {
    minute: fields[0] ?? '0',
    hour: fields[1] ?? '9',
    dayOfMonth: fields[2] ?? '*',
    month: fields[3] ?? '*',
    dayOfWeek: fields[4] ?? '*'
  };
}

export function buildCronExpression(fields: CronFields): string {
  return [fields.minute, fields.hour, fields.dayOfMonth, fields.month, fields.dayOfWeek].map((field) => field.trim() || '*').join(' ');
}

/** Brute-force scans up to ~1 year minute-by-minute to compute the next `limit` fire times. */
export function previewNextCronRuns(expression: string, baseDate = new Date(), limit = 5): string[] {
  const fields = normalizeCronParts(expression);
  if (fields.length !== 5) {
    return [];
  }
  const matches: Date[] = [];
  const cursor = new Date(baseDate.getTime());
  cursor.setSeconds(0, 0);
  cursor.setMinutes(cursor.getMinutes() + 1);

  for (let i = 0; i < 60 * 24 * 370 && matches.length < limit; i += 1) {
    if (matchesCron(cursor, fields)) {
      matches.push(new Date(cursor.getTime()));
    }
    cursor.setMinutes(cursor.getMinutes() + 1);
  }

  return matches.map((date) => formatScheduleDateTime(date.toISOString()));
}

export function formatScheduleDateTime(value: string | null | undefined): string {
  if (value === null || value === undefined) {
    return '未计算';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return `${date.getFullYear()}-${padTime(String(date.getMonth() + 1))}-${padTime(String(date.getDate()))} ${padTime(String(date.getHours()))}:${padTime(String(date.getMinutes()))}`;
}

export function countEnabledSchedules(schedules: ScheduleSnapshot[]): number {
  return schedules.filter((schedule) => schedule.status === 'enabled').length;
}

function isNumeric(value: string): boolean {
  return /^\d+$/.test(value);
}

function padTime(value: string): string {
  return value.padStart(2, '0');
}

function normalizeCronParts(expression: string): string[] {
  const parts = expression.trim().replace(/\s+/g, ' ').split(' ').filter(Boolean);
  return parts.length === 6 ? parts.slice(1) : parts;
}

function matchesCron(date: Date, fields: string[]): boolean {
  const [minute, hour, dayOfMonth, month, dayOfWeek] = fields;
  return (
    matchesField(date.getMinutes(), minute, 0, 59) &&
    matchesField(date.getHours(), hour, 0, 23) &&
    matchesField(date.getDate(), dayOfMonth, 1, 31) &&
    matchesField(date.getMonth() + 1, month, 1, 12) &&
    matchesField(date.getDay() === 0 ? 7 : date.getDay(), dayOfWeek, 1, 7)
  );
}

function matchesField(value: number, expression: string | undefined, min: number, max: number): boolean {
  if (expression === undefined || expression === '*') {
    return true;
  }
  if (expression.startsWith('*/')) {
    const step = Number.parseInt(expression.slice(2), 10);
    return Number.isInteger(step) && step > 0 && value % step === 0;
  }
  if (expression.includes('-')) {
    const [start, end] = expression.split('-').map((part) => Number.parseInt(part, 10));
    return Number.isInteger(start) && Number.isInteger(end) && value >= Math.max(start, min) && value <= Math.min(end, max);
  }
  if (expression.includes(',')) {
    return expression.split(',').some((part) => matchesField(value, part, min, max));
  }
  const exact = Number.parseInt(expression, 10);
  return Number.isInteger(exact) && exact >= min && exact <= max && value === exact;
}
