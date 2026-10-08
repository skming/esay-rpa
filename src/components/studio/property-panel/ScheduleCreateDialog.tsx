import { AlertCircle, CalendarClock, Loader2, Plus, RefreshCcw } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useId, useRef, useState } from 'react';

import type { CreateScheduleOptions } from '../../../hooks/useElectronBridgeActions';
import {
  buildCronExpression,
  describeCronExpression,
  formatScheduleDateTime,
  parseCronFields,
  type CronFields,
} from '../../../lib/schedulePresentation';
import { cn } from '../../../lib/utils';
import type { FlowSnapshot, ScheduleSnapshot } from '../../../types/electron';
import { Button } from '../../ui/button';
import { Checkbox } from '../../ui/checkbox';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../ui/dialog';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../ui/select';
import { Switch } from '../../ui/switch';
import { SearchField, SurfaceEmpty } from '../../workspace/surfaces';
import {
  applyScheduleFrequency,
  getScheduleFrequency,
  normalizeScheduleCron,
  SCHEDULE_FREQUENCIES,
  type ScheduleFrequency,
} from './scheduleForm';

type ScheduleDraft = Omit<Required<CreateScheduleOptions>, 'flowId'>;
type ScheduleCreateDialogProps = {
  flows?: FlowSnapshot[];
  onCreate?: (options: CreateScheduleOptions) => Promise<boolean>;
  onOpenChange: (open: boolean) => void;
  onPreview: (cronExpression: string, timezone: string) => Promise<string[] | null>;
  onUpdate?: (scheduleId: string, options: CreateScheduleOptions) => Promise<boolean>;
  open: boolean;
  schedule?: ScheduleSnapshot;
};
const TIMEZONES = ['Asia/Shanghai', 'UTC', 'America/Los_Angeles', 'Europe/London'];
const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];

function buildInitialDraft(schedule?: ScheduleSnapshot): ScheduleDraft {
  return schedule
    ? {
        cronExpression: schedule.cronExpression,
        enabled: schedule.status === 'enabled',
        flowIds: schedule.task.flowIds?.length
          ? schedule.task.flowIds
          : schedule.task.flowId
            ? [schedule.task.flowId]
            : [],
        name: schedule.name,
        timezone: schedule.timezone,
      }
    : {
        cronExpression: '0 9 * * *',
        enabled: true,
        flowIds: [],
        name: '',
        timezone: 'Asia/Shanghai',
      };
}

export function ScheduleCreateDialog(props: ScheduleCreateDialogProps): ReactElement | null {
  if (!props.open) return null;
  return <ScheduleForm key={props.schedule?.scheduleId ?? 'new'} {...props} />;
}

function ScheduleForm({
  flows = [],
  onCreate,
  onOpenChange,
  onPreview,
  onUpdate,
  schedule,
}: ScheduleCreateDialogProps): ReactElement {
  const isEdit = schedule !== undefined;
  const id = useId();
  const [draft, setDraft] = useState(() => buildInitialDraft(schedule));
  const [scope, setScope] = useState<'all' | 'selected'>(() => (draft.flowIds.length ? 'selected' : 'all'));
  const [frequency, setFrequency] = useState<ScheduleFrequency>(() => getScheduleFrequency(draft.cronExpression));
  const [flowQuery, setFlowQuery] = useState('');
  const [error, setError] = useState<{ field: 'name' | 'flows' | 'cron' | 'save'; message: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const [previewResult, setPreviewResult] = useState<{ key: string; runs: string[] | null } | null>(null);
  const [previewRetry, setPreviewRetry] = useState(0);
  const cronFields = parseCronFields(draft.cronExpression);
  const activeFlows = flows.filter((flow) => flow.status === 'active');
  const selectedFlows = scope === 'all' ? activeFlows : flows.filter((flow) => draft.flowIds.includes(flow.flowId));
  const visibleFlows = flows.filter(
    (flow) =>
      (flow.status === 'active' || draft.flowIds.includes(flow.flowId)) &&
      `${flow.name} ${flow.flowId}`.toLocaleLowerCase().includes(flowQuery.trim().toLocaleLowerCase()),
  );
  const missingIds = draft.flowIds.filter((flowId) => !flows.some((flow) => flow.flowId === flowId));
  const usesExtension = selectedFlows.some((flow) => flow.defaultBrowserExecutor === 'extension');
  const previewExpression = normalizeScheduleCron(draft.cronExpression);
  const previewKey = `${previewExpression}\u0000${draft.timezone}\u0000${previewRetry}`;
  const previewIsValid = previewExpression.split(' ').length === 5;
  const previewRuns = previewResult?.key === previewKey ? previewResult.runs : undefined;
  const previewReady = previewRuns != null && previewRuns.length > 0;
  const flowCount = scope === 'all' ? activeFlows.length : draft.flowIds.length;

  useEffect(() => {
    if (!previewIsValid) return;
    let active = true;
    const timer = window.setTimeout(() => {
      void onPreview(previewExpression, draft.timezone)
        .then((runs) => {
          if (active) setPreviewResult({ key: previewKey, runs });
        })
        .catch(() => {
          if (active) setPreviewResult({ key: previewKey, runs: null });
        });
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [draft.timezone, onPreview, previewExpression, previewIsValid, previewKey]);

  const updateDraft = <K extends keyof ScheduleDraft>(key: K, value: ScheduleDraft[K]): void => {
    setDraft((current) => ({ ...current, [key]: value }));
    setError(null);
  };
  const updateCron = (changes: Partial<CronFields>): void => {
    updateDraft('cronExpression', buildCronExpression({ ...cronFields, ...changes }));
  };
  const toggleFlow = (flowId: string): void => {
    updateDraft(
      'flowIds',
      draft.flowIds.includes(flowId) ? draft.flowIds.filter((value) => value !== flowId) : [...draft.flowIds, flowId],
    );
  };
  const handleOpenChange = (open: boolean): void => {
    if (!savingRef.current) onOpenChange(open);
  };
  const handleSubmit = async (): Promise<void> => {
    if (savingRef.current) return;
    if (!draft.name.trim()) {
      setError({ field: 'name', message: '请输入调度名称，方便在调度中心识别。' });
      nameRef.current?.focus();
      return;
    }
    if (flowCount === 0) {
      setError({
        field: 'flows',
        message: scope === 'selected' ? '请至少选择一个执行流程。' : '没有可用流程，请先在任务中心启用流程。',
      });
      return;
    }
    if (!previewIsValid || !previewReady) {
      setError({
        field: 'cron',
        message: !previewIsValid
          ? 'Cron 表达式需要 5 段：分钟、小时、日期、月份、星期。'
          : previewRuns === undefined
            ? '正在计算触发时间，请稍候再保存。'
            : '请检查触发规则和时区，预览成功后再保存。',
      });
      return;
    }
    const options: CreateScheduleOptions = {
      cronExpression: previewExpression,
      enabled: draft.enabled,
      flowIds: scope === 'all' ? [] : draft.flowIds,
      name: draft.name.trim(),
      timezone: draft.timezone,
    };
    savingRef.current = true;
    setSaving(true);
    setError(null);
    try {
      const saved = schedule ? await onUpdate?.(schedule.scheduleId, options) : await onCreate?.(options);
      if (saved) onOpenChange(false);
      else setError({ field: 'save', message: '保存失败，请检查提示后重试。已填写的内容会保留。' });
    } catch (cause) {
      setError({ field: 'save', message: cause instanceof Error ? cause.message : '保存失败，请重试。' });
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  return (
    <Dialog onOpenChange={handleOpenChange} open>
      <DialogContent className="w-200 max-h-[calc(100dvh-32px)] max-w-[calc(100vw-24px)]" showClose={!saving}>
        <form
          className="flex min-h-0 flex-col"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            void handleSubmit();
          }}
        >
          <DialogHeader className="px-4 pb-3 pt-4 sm:px-5 sm:pr-12">
            <DialogTitle>{isEdit ? '编辑调度' : '新建调度'}</DialogTitle>
            <DialogDescription>选择执行流程与触发时间，保存前确认下一次运行安排。</DialogDescription>
          </DialogHeader>
          <DialogBody className="overscroll-contain border-t border-rule px-4 sm:px-5">
            <fieldset className="grid min-w-0 gap-5 sm:grid-cols-[minmax(0,1fr)_220px]" disabled={saving}>
              <div className="min-w-0 space-y-5">
                <div className="space-y-1.5">
                  <Label htmlFor={`${id}-name`}>调度名称</Label>
                  <Input
                    aria-describedby={error?.field === 'name' ? `${id}-error` : undefined}
                    aria-invalid={error?.field === 'name'}
                    id={`${id}-name`}
                    onChange={(event) => updateDraft('name', event.target.value)}
                    placeholder="例如：每日订单采集"
                    ref={nameRef}
                    value={draft.name}
                  />
                </div>
                <section className="space-y-2.5" aria-labelledby={`${id}-flows`}>
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="text-[12px] font-semibold text-ink-2" id={`${id}-flows`}>
                      执行流程
                    </h3>
                    <span className="text-[11px] tabular-nums text-ink-3">
                      {scope === 'all' ? `当前 ${flowCount} 个可用` : `已选 ${flowCount} 个`}
                    </span>
                  </div>
                  <div className="flex gap-2" aria-label="执行范围">
                    <Button
                      aria-pressed={scope === 'all'}
                      className={cn(
                        'flex-1',
                        scope === 'all' && 'border-accent-line bg-accent-soft text-accent-strong',
                      )}
                      onClick={() => {
                        setScope('all');
                        setError(null);
                      }}
                      variant="outline"
                    >
                      所有启用流程
                    </Button>
                    <Button
                      aria-pressed={scope === 'selected'}
                      className={cn(
                        'flex-1',
                        scope === 'selected' && 'border-accent-line bg-accent-soft text-accent-strong',
                      )}
                      onClick={() => {
                        setScope('selected');
                        setError(null);
                      }}
                      variant="outline"
                    >
                      选择流程
                    </Button>
                  </div>
                  {scope === 'all' ? (
                    <p className="text-[11px] leading-5 text-ink-3">
                      每次触发时读取当时已启用的流程，包含之后新启用的流程。
                    </p>
                  ) : (
                    <>
                      <SearchField
                        className="max-w-none"
                        label="搜索可用流程"
                        onChange={setFlowQuery}
                        placeholder="搜索流程名称或 ID…"
                        value={flowQuery}
                      />
                      <div className="divide-y divide-rule rounded-md border border-rule">
                        {visibleFlows.map((flow) => (
                          <label
                            className="flex min-h-10 cursor-pointer items-center gap-2.5 px-3 py-2 hover:bg-paper"
                            key={flow.flowId}
                          >
                            <Checkbox
                              checked={draft.flowIds.includes(flow.flowId)}
                              onCheckedChange={() => toggleFlow(flow.flowId)}
                            />
                            <span
                              className="min-w-0 flex-1 break-words text-[11px] leading-5 text-ink-2"
                              title={flow.flowId}
                            >
                              {flow.name}
                            </span>
                            {flow.status !== 'active' && (
                              <span className="shrink-0 text-[10px] text-amber-700">未启用</span>
                            )}
                          </label>
                        ))}
                        {missingIds.map((flowId) => (
                          <label className="flex items-center gap-2.5 px-3 py-2" key={flowId}>
                            <Checkbox checked onCheckedChange={() => toggleFlow(flowId)} />
                            <span className="min-w-0 break-all text-[11px] text-red-700">流程已不存在 · {flowId}</span>
                          </label>
                        ))}
                        {visibleFlows.length === 0 && missingIds.length === 0 && (
                          <SurfaceEmpty
                            title={activeFlows.length === 0 ? '暂无可用流程' : '没有匹配的流程'}
                            hint={
                              activeFlows.length === 0
                                ? '请先在任务中心启用流程。'
                                : '试试其他名称或 ID，已选流程不会被清除。'
                            }
                          />
                        )}
                      </div>
                    </>
                  )}
                  {scope === 'all' && activeFlows.length === 0 && (
                    <p className="text-[11px] text-amber-700">暂无可用流程，请先在任务中心启用流程。</p>
                  )}
                  {usesExtension && (
                    <p className="flex items-start gap-1.5 text-[11px] leading-5 text-amber-800">
                      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.5} />
                      所选流程使用浏览器插件，触发时需保持插件连接。
                    </p>
                  )}
                </section>
                <section className="space-y-3 border-t border-rule pt-4" aria-labelledby={`${id}-rule`}>
                  <h3 className="text-[12px] font-semibold text-ink-2" id={`${id}-rule`}>
                    触发规则
                  </h3>
                  <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label htmlFor={`${id}-frequency`}>执行频率</Label>
                      <Select
                        disabled={saving}
                        onValueChange={(value) => {
                          if (value !== null) {
                            setFrequency(value);
                            updateDraft('cronExpression', applyScheduleFrequency(value, draft.cronExpression));
                          }
                        }}
                        value={frequency}
                      >
                        <SelectTrigger id={`${id}-frequency`}>
                          <SelectValue>
                            {SCHEDULE_FREQUENCIES.find((item) => item.value === frequency)?.label}
                          </SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {SCHEDULE_FREQUENCIES.map((item) => (
                            <SelectItem key={item.value} value={item.value}>
                              {item.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    {frequency !== 'custom' && frequency !== 'minute' && (
                      <div className="space-y-1.5">
                        <Label htmlFor={`${id}-time`}>{frequency === 'hour' ? '每小时的第几分钟' : '执行时间'}</Label>
                        {frequency === 'hour' ? (
                          <CronNumberSelect
                            disabled={saving}
                            id={`${id}-time`}
                            min={0}
                            max={59}
                            value={cronFields.minute}
                            onChange={(value) => updateCron({ minute: value })}
                          />
                        ) : (
                          <Input
                            id={`${id}-time`}
                            type="time"
                            value={`${cronFields.hour.padStart(2, '0')}:${cronFields.minute.padStart(2, '0')}`}
                            onInput={(event) => {
                              if (event.currentTarget.value) {
                                const [hour, minute] = event.currentTarget.value.split(':');
                                updateCron({ hour: String(Number(hour)), minute: String(Number(minute)) });
                              }
                            }}
                          />
                        )}
                      </div>
                    )}
                    {frequency === 'week' && (
                      <div className="space-y-1.5">
                        <Label htmlFor={`${id}-weekday`}>每周执行日</Label>
                        <Select
                          disabled={saving}
                          onValueChange={(value) => {
                            if (value !== null) updateCron({ dayOfWeek: value });
                          }}
                          value={String(Number(cronFields.dayOfWeek) % 7)}
                        >
                          <SelectTrigger id={`${id}-weekday`}>
                            <SelectValue>{WEEKDAYS[Number(cronFields.dayOfWeek) % 7]}</SelectValue>
                          </SelectTrigger>
                          <SelectContent>
                            {WEEKDAYS.map((label, index) => (
                              <SelectItem key={label} value={String(index)}>
                                {label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                    )}
                    {frequency === 'month' && (
                      <div className="space-y-1.5">
                        <Label htmlFor={`${id}-monthday`}>每月执行日（1–31）</Label>
                        <CronNumberSelect
                          disabled={saving}
                          id={`${id}-monthday`}
                          min={1}
                          max={31}
                          value={cronFields.dayOfMonth}
                          onChange={(value) => updateCron({ dayOfMonth: value })}
                        />
                        <p className="text-[10px] leading-4 text-ink-3">没有该日期的月份会跳过。</p>
                      </div>
                    )}
                  </div>
                  {frequency === 'custom' ? (
                    <div className="space-y-1.5">
                      <Label htmlFor={`${id}-cron`}>Cron 表达式</Label>
                      <Input
                        aria-describedby={`${id}-cron-hint`}
                        aria-invalid={!previewIsValid || previewRuns === null}
                        className="font-mono"
                        id={`${id}-cron`}
                        onChange={(event) => updateDraft('cronExpression', event.target.value)}
                        placeholder="0 9 * * *"
                        value={draft.cronExpression}
                      />
                      <p className="text-[10px] leading-5 text-ink-3" id={`${id}-cron-hint`}>
                        按「分钟 小时 日期 月份 星期」填写 5 段。例如 <code>*/15 * * * *</code> 表示每 15 分钟。
                      </p>
                    </div>
                  ) : (
                    <p className="text-[10px] text-ink-3">
                      Cron <code className="ml-1 font-mono">{previewExpression}</code>
                    </p>
                  )}
                  <div className="space-y-1.5">
                    <Label htmlFor={`${id}-timezone`}>执行时区</Label>
                    <Select
                      disabled={saving}
                      onValueChange={(value) => {
                        if (value !== null) updateDraft('timezone', value);
                      }}
                      value={draft.timezone}
                    >
                      <SelectTrigger id={`${id}-timezone`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {[...new Set([...TIMEZONES, draft.timezone])].map((zone) => (
                          <SelectItem key={zone} value={zone}>
                            {zone === 'Asia/Shanghai' ? 'Asia/Shanghai · 北京时间' : zone}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </section>
                <div className="flex items-start justify-between gap-3 border-t border-rule pt-4">
                  <div>
                    <Label htmlFor={`${id}-enabled`}>{isEdit ? '启用调度' : '创建后启用'}</Label>
                    <p className="mt-1 text-[11px] leading-5 text-ink-3">
                      {draft.enabled
                        ? '按规则等待下次触发，保存不会立即执行。'
                        : '保存为停用状态，启用后才会定时执行。'}
                    </p>
                  </div>
                  <Switch
                    checked={draft.enabled}
                    disabled={saving}
                    id={`${id}-enabled`}
                    onCheckedChange={(checked) => updateDraft('enabled', checked)}
                  />
                </div>
              </div>
              <aside
                className="min-w-0 border-t border-rule pt-4 sm:border-t-0 sm:border-l sm:pl-5 sm:pt-0"
                aria-labelledby={`${id}-preview`}
              >
                <h3 className="flex items-center gap-2 text-[12px] font-semibold text-ink-2" id={`${id}-preview`}>
                  <CalendarClock className="h-3.5 w-3.5 text-ink-3" strokeWidth={1.5} />
                  接下来 5 次触发
                </h3>
                <p className="mt-2 break-words text-[11px] leading-5 text-ink-2">
                  {previewReady ? describeCronExpression(previewExpression) : '等待有效的触发规则'}
                </p>
                <p className="mt-1 break-all text-[10px] text-ink-3">{draft.timezone}</p>
                <div className="mt-3" aria-live="polite" aria-busy={previewIsValid && previewRuns === undefined}>
                  {!previewIsValid ? (
                    <p className="text-[11px] leading-5 text-red-700">Cron 表达式需要 5 段，请检查填写内容。</p>
                  ) : previewRuns === undefined ? (
                    <p className="flex items-center gap-2 text-[11px] text-ink-3">
                      <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" strokeWidth={1.5} />
                      正在计算触发时间…
                    </p>
                  ) : !previewReady ? (
                    <div className="space-y-2">
                      <p className="text-[11px] leading-5 text-red-700">无法生成预览，请检查规则和时区，或重试。</p>
                      <Button onClick={() => setPreviewRetry((value) => value + 1)} variant="outline">
                        <RefreshCcw className="h-3 w-3" strokeWidth={1.5} />
                        重试预览
                      </Button>
                    </div>
                  ) : (
                    <ol className="divide-y divide-rule">
                      {previewRuns.map((run, index) => (
                        <li className="flex items-center gap-3 py-2.5" key={`${run}:${index}`}>
                          <span className="text-[10px] tabular-nums text-ink-3">{index + 1}</span>
                          <time
                            className={cn(
                              'font-mono text-[11px] tabular-nums',
                              index === 0 ? 'font-medium text-ink' : 'text-ink-2',
                            )}
                            dateTime={run}
                          >
                            {formatScheduleDateTime(run, draft.timezone)}
                          </time>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
                <p className="mt-3 text-[10px] leading-5 text-ink-3">
                  {draft.enabled ? '预览按所选时区计算。' : '仅预览时间，当前设置不会自动触发。'}
                </p>
              </aside>
            </fieldset>
          </DialogBody>
          {error !== null && (
            <p
              className="shrink-0 border-t border-rule bg-red-50 px-4 py-2 text-[11px] leading-5 text-red-700 sm:px-5"
              id={`${id}-error`}
              role="alert"
            >
              {error.message}
            </p>
          )}
          <DialogFooter className="px-4 sm:px-5">
            <span className="mr-auto text-[10px] text-ink-3">{draft.enabled ? '保存并启用' : '保存为停用'}</span>
            <Button disabled={saving} onClick={() => handleOpenChange(false)} variant="outline">
              取消
            </Button>
            <Button disabled={saving || !previewReady} type="submit" variant="primary">
              {saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" strokeWidth={1.5} />
              ) : (
                !isEdit && <Plus className="h-3.5 w-3.5" strokeWidth={1.5} />
              )}
              {saving ? '保存中…' : isEdit ? '保存修改' : '创建调度'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function CronNumberSelect({
  id,
  min,
  max,
  value,
  disabled,
  onChange,
}: {
  id: string;
  min: number;
  max: number;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}): ReactElement {
  return (
    <Select
      disabled={disabled}
      onValueChange={(next) => {
        if (next !== null) onChange(next);
      }}
      value={String(Number(value))}
    >
      <SelectTrigger id={id}>
        <SelectValue>{Number(value)}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        {Array.from({ length: max - min + 1 }, (_, index) => String(index + min)).map((item) => (
          <SelectItem key={item} value={item}>
            {item}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
