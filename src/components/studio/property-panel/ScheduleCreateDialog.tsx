import { CalendarClock, Clock3, Play, TimerReset } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useMemo, useState } from 'react';

import type { CreateScheduleOptions } from '../../../hooks/useElectronBridgeActions';
import { buildCronExpression, formatZonedDateTime, parseCronFields, type CronFields } from '../../../lib/schedulePresentation';
import type { FlowSnapshot, ScheduleSnapshot } from '../../../types/electron';
import { Button } from '../../ui/button';
import { Checkbox } from '../../ui/checkbox';
import { Card, CardContent, CardHeader, CardTitle } from '../../ui/card';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../../ui/dialog';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../ui/select';
import { Switch } from '../../ui/switch';

// 后端调度只认 Cron 表达式；Interval/Date/事件触发在后端都没有落库路径，
// 之前作为标签页存在只是空壳，填了也不会生效，故收敛为 Cron + 预设的单一闭环。
type ScheduleDraft = Omit<Required<CreateScheduleOptions>, 'flowId'>;

const cronPresets = [
  { label: '每分钟', value: '* * * * *' },
  { label: '每小时', value: '0 * * * *' },
  { label: '每天', value: '0 9 * * *' },
  { label: '每周', value: '0 9 * * 1' },
  { label: '每月', value: '0 9 1 * *' },
  { label: '工作日', value: '30 9 * * 1-5' }
] as const;

const timezoneOptions = ['Asia/Shanghai', 'UTC', 'America/Los_Angeles', 'Europe/London'] as const;

function buildInitialDraft(schedule?: ScheduleSnapshot, initialFlowIds?: string[]): ScheduleDraft {
  if (schedule !== undefined) {
    return {
      cronExpression: schedule.cronExpression,
      enabled: schedule.status === 'enabled',
      flowIds: schedule.task.flowIds?.length ? schedule.task.flowIds : schedule.task.flowId ? [schedule.task.flowId] : [],
      name: schedule.name,
      timezone: schedule.timezone
    };
  }
  return {
    cronExpression: '0 9 * * *',
    enabled: true,
    flowIds: initialFlowIds ?? [],
    name: '每日订单采集',
    timezone: 'Asia/Shanghai'
  };
}

export function ScheduleCreateDialog({
  flows = [],
  initialFlowIds,
  onCreate,
  onOpenChange,
  onPreview,
  onUpdate,
  open,
  schedule
}: {
  flows?: FlowSnapshot[];
  initialFlowIds?: string[];
  onCreate?: (options: CreateScheduleOptions) => Promise<boolean>;
  onOpenChange: (open: boolean) => void;
  onPreview: (cronExpression: string, timezone: string) => Promise<string[] | null>;
  onUpdate?: (scheduleId: string, options: CreateScheduleOptions) => Promise<boolean>;
  open: boolean;
  schedule?: ScheduleSnapshot;
}): ReactElement {
  const isEdit = schedule !== undefined;
  const [draft, setDraft] = useState<ScheduleDraft>(() => buildInitialDraft(schedule, initialFlowIds));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [previewResult, setPreviewResult] = useState<{ key: string; runs: string[] | null } | null>(null);
  const cronFields = useMemo(() => parseCronFields(draft.cronExpression), [draft.cronExpression]);
  const activeFlows = useMemo(() => flows.filter((f) => f.status === 'active'), [flows]);
  const selectedFlows = draft.flowIds.length === 0 ? activeFlows : activeFlows.filter((flow) => draft.flowIds.includes(flow.flowId));
  const usesExtension = selectedFlows.some((flow) => flow.defaultBrowserExecutor === 'extension');
  const previewExpression = normalizeCronExpression(draft.cronExpression);
  const previewKey = `${previewExpression}\u0000${draft.timezone}`;
  const previewIsValid = previewExpression.split(' ').length === 5;
  const previewRuns = previewResult?.key === previewKey ? previewResult.runs : undefined;

  useEffect(() => {
    if (!open || !previewIsValid) return;
    let active = true;
    const timer = window.setTimeout(() => {
      void onPreview(previewExpression, draft.timezone).then((runs) => {
        if (active) setPreviewResult({ key: previewKey, runs });
      }).catch(() => {
        if (active) setPreviewResult({ key: previewKey, runs: null });
      });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [draft.timezone, onPreview, open, previewExpression, previewIsValid, previewKey]);

  // 按前值在渲染期重置草稿：改用 useEffect 会先绘出上一次的旧草稿，弹窗打开瞬间闪一下
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    setDraft(buildInitialDraft(schedule, initialFlowIds));
    setError(null);
  }

  const updateDraft = <K extends keyof ScheduleDraft>(key: K, value: ScheduleDraft[K]): void => {
    setDraft((current) => ({ ...current, [key]: value }));
    setError(null);
  };

  const toggleFlow = (flowId: string): void => {
    if (draft.flowIds.includes(flowId)) {
      if (draft.flowIds.length > 1) updateDraft('flowIds', draft.flowIds.filter((id) => id !== flowId));
    } else {
      updateDraft('flowIds', [...draft.flowIds, flowId]);
    }
  };

  const updateCronField = (key: keyof CronFields, value: string): void => {
    updateDraft('cronExpression', buildCronExpression({ ...cronFields, [key]: value }));
  };

  const handleSubmit = async (): Promise<void> => {
    const normalizedCron = normalizeCronExpression(draft.cronExpression);
    if (draft.name.trim().length === 0) {
      setError('调度名称不能为空');
      return;
    }
    if (normalizedCron.split(' ').length !== 5) {
      setError('Cron 表达式必须是 5 段');
      return;
    }
    if (previewRuns === null || previewRuns === undefined) {
      setError('请先确认 Cron 预览可用');
      return;
    }

    const options: CreateScheduleOptions = {
      cronExpression: normalizedCron,
      enabled: draft.enabled,
      flowIds: draft.flowIds,
      name: draft.name.trim(),
      timezone: draft.timezone.trim()
    };

    setSaving(true);
    try {
      const saved = isEdit && schedule !== undefined
        ? await onUpdate?.(schedule.scheduleId, options)
        : await onCreate?.(options);
      if (saved) onOpenChange(false);
      else setError('保存失败，请检查提示后重试');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存失败，请重试');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="w-180">
        <DialogHeader>
          <DialogTitle className="inline-flex items-center gap-2">
            <CalendarClock className="h-4 w-4 text-blue-600" strokeWidth={1.5} />
            {isEdit ? '编辑调度' : 'Cron 调度编辑器'}
          </DialogTitle>
          <DialogDescription>配置 Cron 触发规则、时区与执行流程，预览按所选时区计算，保存后写入调度中心。</DialogDescription>
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

          <div className="grid gap-1.5">
            <Label>执行流程</Label>
            <div className="max-h-36 overflow-y-auto rounded-md border border-rule p-2">
              <label className="flex cursor-pointer items-center gap-2 px-1 py-1 text-xs">
                <Checkbox checked={draft.flowIds.length === 0} onCheckedChange={() => updateDraft('flowIds', [])} />
                所有流程
              </label>
              {activeFlows.map((flow) => (
                <label className="flex cursor-pointer items-center gap-2 px-1 py-1 text-xs" key={flow.flowId}>
                  <Checkbox checked={draft.flowIds.includes(flow.flowId)} onCheckedChange={() => toggleFlow(flow.flowId)} />
                  <span className="truncate">{flow.name}</span>
                </label>
              ))}
            </div>
          </div>
          <p className="-mt-2 text-[11px] text-slate-500">
            {draft.flowIds.length === 0 ? '所有流程' : `已选 ${draft.flowIds.length} 个流程`}，分别按自身配置执行
            {usesExtension && '；触发时需保持插件连接'}
          </p>

          <div className="grid gap-3">
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
          </div>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="inline-flex items-center gap-2">
                <TimerReset className="h-3.5 w-3.5 text-blue-500" strokeWidth={1.5} />
                下次 5 次执行（{draft.timezone}）
              </CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-5 gap-1.5">
              {(!previewIsValid || previewRuns === null ? ['表达式无效或预览服务不可用'] : previewRuns === undefined ? ['计算中…'] : previewRuns.map((run) => formatZonedDateTime(new Date(run), draft.timezone))).map((item) => (
                <div className="rounded bg-slate-50 px-2 py-1 font-mono text-[10px] text-slate-600" key={item}>{item}</div>
              ))}
            </CardContent>
          </Card>

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
          <Button disabled={saving} onClick={() => { void handleSubmit(); }} variant="primary">
            <Play className="h-3.5 w-3.5 fill-current" strokeWidth={1.5} />
            {saving ? '保存中…' : isEdit ? '保存修改' : '创建调度'}
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

function normalizeCronExpression(value: string): string {
  return value.trim().replace(/\s+/g, ' ');
}
