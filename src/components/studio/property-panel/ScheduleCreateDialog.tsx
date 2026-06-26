import { CalendarClock, Clock3, Play, RotateCcw, TimerReset } from 'lucide-react';
import type { ReactElement } from 'react';
import { useMemo, useState } from 'react';

import type { CreateScheduleOptions } from '../../../hooks/useElectronBridgeActions';
import { buildCronExpression, parseCronFields, previewNextCronRuns, type CronFields } from '../../../lib/schedulePresentation';
import type { FlowSnapshot, ScheduleSnapshot } from '../../../types/electron';
import { Button } from '../../ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../ui/card';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../../ui/dialog';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../ui/select';
import { Switch } from '../../ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../ui/tabs';

type TriggerType = 'cron' | 'interval' | 'date' | 'event';
type ConflictPolicy = 'parallel' | 'queue' | 'skip';

type ScheduleDraft = Required<CreateScheduleOptions> & {
  conflictPolicy: ConflictPolicy;
  intervalMinutes: number;
  maxInstances: number;
  retryDelaySeconds: number;
  retryTimes: number;
  triggerType: TriggerType;
};

const cronPresets = [
  { label: '每分钟', value: '* * * * *' },
  { label: '每小时', value: '0 * * * *' },
  { label: '每天', value: '0 9 * * *' },
  { label: '每周', value: '0 9 * * 1' },
  { label: '每月', value: '0 9 1 * *' },
  { label: '工作日', value: '30 9 * * 1-5' }
] as const;

const timezoneOptions = ['Asia/Shanghai', 'UTC', 'America/Los_Angeles', 'Europe/London'] as const;
const triggerTabClassName = 'min-h-8 rounded px-3 py-1.5 text-[12px] leading-none data-[state=active]:bg-white data-[state=active]:shadow-xs data-[state=active]:after:hidden';

function buildInitialDraft(schedule?: ScheduleSnapshot): ScheduleDraft {
  if (schedule !== undefined) {
    return {
      conflictPolicy: 'skip',
      cronExpression: schedule.cronExpression,
      enabled: schedule.status === 'enabled',
      flowId: schedule.task.flowId ?? '__all__',
      intervalMinutes: 60,
      maxInstances: 1,
      name: schedule.name,
      retryDelaySeconds: 30,
      retryTimes: 1,
      timezone: schedule.timezone,
      triggerType: 'cron'
    };
  }
  return {
    conflictPolicy: 'skip',
    cronExpression: '0 9 * * *',
    enabled: true,
    flowId: '__all__',
    intervalMinutes: 60,
    maxInstances: 1,
    name: '每日订单采集',
    retryDelaySeconds: 30,
    retryTimes: 1,
    timezone: 'Asia/Shanghai',
    triggerType: 'cron'
  };
}

export function ScheduleCreateDialog({
  flows = [],
  onCreate,
  onOpenChange,
  onUpdate,
  open,
  schedule
}: {
  flows?: FlowSnapshot[];
  onCreate?: (options: CreateScheduleOptions) => void;
  onOpenChange: (open: boolean) => void;
  onUpdate?: (scheduleId: string, options: CreateScheduleOptions) => void;
  open: boolean;
  schedule?: ScheduleSnapshot;
}): ReactElement {
  const isEdit = schedule !== undefined;
  const [draft, setDraft] = useState<ScheduleDraft>(() => buildInitialDraft(schedule));
  const [error, setError] = useState<string | null>(null);
  const cronFields = useMemo(() => parseCronFields(draft.cronExpression), [draft.cronExpression]);
  const previews = useMemo(() => previewNextCronRuns(draft.cronExpression), [draft.cronExpression]);
  const activeFlows = useMemo(() => flows.filter((f) => f.status !== 'archived'), [flows]);

  // Re-init when schedule prop changes (dialog reopens for different schedule)
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional reset on open
  useMemo(() => { setDraft(buildInitialDraft(schedule)); setError(null); }, [open]);

  const updateDraft = <K extends keyof ScheduleDraft>(key: K, value: ScheduleDraft[K]): void => {
    setDraft((current) => ({ ...current, [key]: value }));
    setError(null);
  };

  const updateCronField = (key: keyof CronFields, value: string): void => {
    updateDraft('cronExpression', buildCronExpression({ ...cronFields, [key]: value }));
  };

  const handleSubmit = (): void => {
    const normalizedCron = normalizeCronExpression(draft.triggerType === 'interval' ? `*/${Math.max(1, draft.intervalMinutes)} * * * *` : draft.cronExpression);
    if (draft.name.trim().length === 0) {
      setError('调度名称不能为空');
      return;
    }
    if (draft.triggerType === 'event') {
      setError('事件触发需要后端 Webhook/File watcher 支持，当前版本先保存 Cron/Interval/Date。');
      return;
    }
    if (normalizedCron.split(' ').length !== 5) {
      setError('Cron 表达式必须是 5 段');
      return;
    }

    const options: CreateScheduleOptions = {
      cronExpression: normalizedCron,
      enabled: draft.enabled,
      flowId: draft.flowId,
      name: draft.name.trim(),
      timezone: draft.timezone.trim()
    };

    if (isEdit && schedule !== undefined) {
      onUpdate?.(schedule.scheduleId, options);
    } else {
      onCreate?.(options);
    }
    onOpenChange(false);
  };

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="w-180">
        <DialogHeader>
          <DialogTitle className="inline-flex items-center gap-2">
            <CalendarClock className="h-4 w-4 text-blue-600" strokeWidth={1.5} />
            {isEdit ? '编辑调度' : 'Cron 调度编辑器'}
          </DialogTitle>
          <DialogDescription>配置触发器、时区、预览、并发与重试策略，保存后写入调度中心。</DialogDescription>
        </DialogHeader>

        <DialogBody className="grid gap-3">
          <div className="grid grid-cols-[1fr_200px] gap-3">
            <Label className="grid gap-1">
              <span>调度名称</span>
              <Input onChange={(event) => updateDraft('name', event.target.value)} value={draft.name} />
            </Label>
            <Label className="grid gap-1">
              <span>时区</span>
              <Select onValueChange={(value) => updateDraft('timezone', value)} value={draft.timezone}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {timezoneOptions.map((timezone) => (
                    <SelectItem key={timezone} value={timezone}>
                      {timezone}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Label>
          </div>

          <Label className="grid gap-1">
            <span>执行流程</span>
            <Select onValueChange={(value) => updateDraft('flowId', value)} value={draft.flowId}>
              <SelectTrigger>
                <SelectValue placeholder="选择要调度的流程" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">所有流程</SelectItem>
                {activeFlows.map((flow) => (
                  <SelectItem key={flow.flowId} value={flow.flowId}>
                    {flow.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Label>

          <Tabs onValueChange={(value) => updateDraft('triggerType', value as TriggerType)} value={draft.triggerType}>
            <TabsList className="grid min-h-10 grid-cols-4 rounded-md bg-slate-100 p-1">
              <TabsTrigger className={triggerTabClassName} value="cron">Cron 表达式</TabsTrigger>
              <TabsTrigger className={triggerTabClassName} value="interval">Interval</TabsTrigger>
              <TabsTrigger className={triggerTabClassName} value="date">Date</TabsTrigger>
              <TabsTrigger className={triggerTabClassName} value="event">事件触发</TabsTrigger>
            </TabsList>

            <TabsContent className="mt-3 grid gap-3" value="cron">
              <div className="grid grid-cols-6 gap-2">
                {cronPresets.map((preset) => (
                  <Button className="h-7" key={preset.value} onClick={() => updateDraft('cronExpression', preset.value)} variant={draft.cronExpression === preset.value ? 'secondary' : 'outline'}>
                    {preset.label}
                  </Button>
                ))}
              </div>
              <div className="grid grid-cols-5 gap-2">
                <CronInput label="分钟" onChange={(value) => updateCronField('minute', value)} value={cronFields.minute} />
                <CronInput label="小时" onChange={(value) => updateCronField('hour', value)} value={cronFields.hour} />
                <CronInput label="日期" onChange={(value) => updateCronField('dayOfMonth', value)} value={cronFields.dayOfMonth} />
                <CronInput label="月份" onChange={(value) => updateCronField('month', value)} value={cronFields.month} />
                <CronInput label="星期" onChange={(value) => updateCronField('dayOfWeek', value)} value={cronFields.dayOfWeek} />
              </div>
              <Label className="grid gap-1">
                <span>完整 Cron 表达式</span>
                <Input className="font-mono" onChange={(event) => updateDraft('cronExpression', event.target.value)} value={draft.cronExpression} />
              </Label>
            </TabsContent>

            <TabsContent className="mt-3" value="interval">
              <Label className="grid max-w-65 gap-1">
                <span>间隔分钟</span>
                <Input min={1} onChange={(event) => updateDraft('intervalMinutes', Math.max(1, Number.parseInt(event.target.value, 10) || 1))} type="number" value={String(draft.intervalMinutes)} />
              </Label>
            </TabsContent>

            <TabsContent className="mt-3" value="date">
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">单次延迟触发当前映射为最近 Cron 保存，后端接入 APScheduler DateTrigger 后可原生落库。</div>
            </TabsContent>

            <TabsContent className="mt-3" value="event">
              <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] text-slate-600">文件变化/Webhook 事件触发已预留入口，Alpha 先交付 Cron 与 Interval。</div>
            </TabsContent>
          </Tabs>

          <div className="grid grid-cols-[1fr_1fr] gap-3">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="inline-flex items-center gap-2">
                  <TimerReset className="h-3.5 w-3.5 text-blue-500" strokeWidth={1.5} />
                  下次 5 次执行
                </CardTitle>
              </CardHeader>
              <CardContent className="grid gap-1.5">
                {(previews.length > 0 ? previews : ['表达式无预览']).map((item) => (
                  <div className="rounded bg-slate-50 px-2 py-1 font-mono text-[10px] text-slate-600" key={item}>{item}</div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="inline-flex items-center gap-2">
                  <RotateCcw className="h-3.5 w-3.5 text-amber-500" strokeWidth={1.5} />
                  并发与重试
                </CardTitle>
              </CardHeader>
              <CardContent className="grid gap-2">
                <div className="grid grid-cols-2 gap-2">
                  <NumberField label="最大实例" onChange={(value) => updateDraft('maxInstances', value)} value={draft.maxInstances} />
                  <NumberField label="重试次数" onChange={(value) => updateDraft('retryTimes', value)} value={draft.retryTimes} />
                </div>
                <div className="grid grid-cols-[1fr_130px] gap-2">
                  <Label className="grid gap-1">
                    <span>冲突策略</span>
                    <Select onValueChange={(value) => updateDraft('conflictPolicy', value as ConflictPolicy)} value={draft.conflictPolicy}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="skip">跳过本次</SelectItem>
                        <SelectItem value="queue">排队等待</SelectItem>
                        <SelectItem value="parallel">强制并行</SelectItem>
                      </SelectContent>
                    </Select>
                  </Label>
                  <NumberField label="重试间隔秒" onChange={(value) => updateDraft('retryDelaySeconds', value)} value={draft.retryDelaySeconds} />
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="flex h-8 items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 text-[11px] text-slate-600">
            <span className="inline-flex items-center gap-2">
              <Clock3 className="h-3.5 w-3.5 text-blue-500" strokeWidth={1.5} />
              {isEdit ? '当前启用状态' : '保存后立即启用'}
            </span>
            <Switch aria-label="启用状态" checked={draft.enabled} onCheckedChange={(checked) => updateDraft('enabled', checked)} />
          </div>

          {error !== null && <div className="rounded-md border border-red-200 bg-red-50 px-2.5 py-2 text-[11px] text-red-700">{error}</div>}
        </DialogBody>

        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="outline">取消</Button>
          <Button onClick={handleSubmit} variant="primary">
            <Play className="h-3.5 w-3.5 fill-current" strokeWidth={1.5} />
            {isEdit ? '保存修改' : '创建调度'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CronInput({ label, onChange, value }: { label: string; onChange: (value: string) => void; value: string }): ReactElement {
  return (
    <Label className="grid gap-1">
      <span>{label}</span>
      <Input className="font-mono" onChange={(event) => onChange(event.target.value)} value={value} />
    </Label>
  );
}

function NumberField({ label, onChange, value }: { label: string; onChange: (value: number) => void; value: number }): ReactElement {
  return (
    <Label className="grid gap-1">
      <span>{label}</span>
      <Input min={0} onChange={(event) => onChange(Math.max(0, Number.parseInt(event.target.value, 10) || 0))} type="number" value={String(value)} />
    </Label>
  );
}

function normalizeCronExpression(value: string): string {
  return value.trim().replace(/\s+/g, ' ');
}
