import type { ScheduleRunSummary, ScheduleSnapshot } from '../types/electron';

export type ScheduleFilter = 'all' | 'attention' | 'enabled' | 'disabled';
export type CronFields = {
  minute: string;
  hour: string;
  dayOfMonth: string;
  month: string;
  dayOfWeek: string;
};

export function filterSchedules(
  schedules: ScheduleSnapshot[],
  filter: ScheduleFilter,
  query: string,
  runSummaries: Record<string, ScheduleRunSummary> = {},
): ScheduleSnapshot[] {
  const normalizedQuery = query.trim().toLowerCase();
  return [...schedules]
    .filter((schedule) => {
      if (filter === 'all') return true;
      if (filter === 'attention') return hasScheduleError(schedule, runSummaries[schedule.scheduleId]);
      return schedule.status === filter;
    })
    .filter((schedule) => {
      if (normalizedQuery.length === 0) {
        return true;
      }
      return `${schedule.name} ${schedule.cronExpression} ${schedule.timezone} ${schedule.task.flowName}`.toLowerCase().includes(normalizedQuery);
    })
    .sort((left, right) => compareSchedulesForOperations(left, right, runSummaries));
}

export function hasScheduleError(schedule: ScheduleSnapshot, runSummary?: ScheduleRunSummary): boolean {
  return schedule.status === 'enabled' && (
    (typeof schedule.lastError === 'string' && schedule.lastError.trim() !== '') ||
    (runSummary !== undefined && ['failed', 'partial', 'stopped'].includes(runSummary.status))
  );
}

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

export function selectUpcomingSchedules(schedules: ScheduleSnapshot[], limit?: number): ScheduleSnapshot[] {
  const upcoming = schedules
    .filter((schedule) => schedule.status === 'enabled' && typeof schedule.nextRunAt === 'string' && schedule.nextRunAt.trim() !== '')
    .sort((left, right) => new Date(left.nextRunAt ?? '').getTime() - new Date(right.nextRunAt ?? '').getTime());
  return limit === undefined ? upcoming : upcoming.slice(0, limit);
}

export function describeNextRun(schedule: ScheduleSnapshot): string {
  if (typeof schedule.nextRunAt === 'string' && schedule.nextRunAt.trim() !== '') {
    return formatScheduleDateTime(schedule.nextRunAt);
  }
  // 停用与"排期被系统清掉"都表现为 nextRunAt 为空，混成一句会把用户自己按的暂停说成故障。
  if (schedule.status !== 'enabled') {
    return '已停用';
  }
  return schedule.lastError === null || schedule.lastError === undefined || schedule.lastError === '' ? '等待计算' : '已停止排期';
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

function compareSchedulesForOperations(left: ScheduleSnapshot, right: ScheduleSnapshot, runSummaries: Record<string, ScheduleRunSummary>): number {
  const attentionOrder = Number(hasScheduleError(right, runSummaries[right.scheduleId])) - Number(hasScheduleError(left, runSummaries[left.scheduleId]));
  if (attentionOrder !== 0) return attentionOrder;

  const enabledOrder = Number(right.status === 'enabled') - Number(left.status === 'enabled');
  if (enabledOrder !== 0) return enabledOrder;

  const leftNextRun = dateOrInfinity(left.nextRunAt);
  const rightNextRun = dateOrInfinity(right.nextRunAt);
  if (leftNextRun !== rightNextRun) return leftNextRun - rightNextRun;
  return dateOrZero(right.updatedAt) - dateOrZero(left.updatedAt);
}

function dateOrInfinity(value: string | null | undefined): number {
  if (value === null || value === undefined || value.trim() === '') return Number.POSITIVE_INFINITY;
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? Number.POSITIVE_INFINITY : time;
}

function dateOrZero(value: string): number {
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? 0 : time;
}

export function formatZonedDateTime(date: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).formatToParts(date);
  const lookup: Record<string, string> = {};
  for (const part of parts) {
    lookup[part.type] = part.value;
  }
  return `${lookup.year}-${lookup.month}-${lookup.day} ${lookup.hour}:${lookup.minute}`;
}
