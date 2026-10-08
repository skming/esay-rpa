import type { BridgeCallOptions } from './electronBridgeTypes';
import type { FlowSnapshot, ScheduleTaskPayload } from '../types/electron';
import type { UseElectronBridgeActionsParams, ElectronBridgeActions, CreateScheduleOptions } from './electronBridgeActionTypes';
import { persistCurrentFlow } from './flowBridgeActions';

export function createScheduleBridgeActions({
  callBridge,
  currentFlow,
  flowCanvas,
  flows,
  pushToast,
  inputVariables,
  setCurrentFlow,
  setFlows,
  setActiveRunId,
  setActiveRunFlowId,
  setRuntimeStatus,
  setSchedules,
  setScheduleRunSummaries,
}: Pick<
  UseElectronBridgeActionsParams,
  | 'callBridge'
  | 'currentFlow'
  | 'flowCanvas'
  | 'flows'
  | 'pushToast'
  | 'inputVariables'
  | 'setCurrentFlow'
  | 'setFlows'
  | 'setActiveRunId'
  | 'setActiveRunFlowId'
  | 'setRuntimeStatus'
  | 'setSchedules'
  | 'setScheduleRunSummaries'
>): Pick<
  ElectronBridgeActions,
  | 'loadSchedules'
  | 'loadScheduleRunSummaries'
  | 'previewSchedule'
  | 'createDefaultSchedule'
  | 'updateScheduleEnabled'
  | 'updateSchedule'
  | 'deleteSchedule'
  | 'triggerSchedule'
> {
  return {
    loadSchedules: async (options?: BridgeCallOptions) => {
      const result = await callBridge((api) => api.listSchedules(), undefined, options);
      if (result !== null) {
        setSchedules(result);
      }
    },
    loadScheduleRunSummaries: async (options?: BridgeCallOptions) => {
      const result = await callBridge((api) => api.listScheduleRunSummaries(), undefined, options);
      if (result !== null) {
        setScheduleRunSummaries(result);
      }
    },
    previewSchedule: async (cronExpression: string, timezone: string) =>
      await callBridge((api) => api.previewSchedule(cronExpression, timezone), undefined, { silent: true }),
    createDefaultSchedule: async (options = {}) => {
      let target: ScheduleTarget | null;
      try {
        target = resolveScheduleTarget(flows, options);
      } catch (error) {
        pushToast('error', error instanceof Error ? error.message : '未找到所选流程');
        return false;
      }
      if (target === null) {
        const flow = await persistCurrentFlow({ callBridge, currentFlow, flows, flowCanvas, inputVariables });
        if (flow === null) {
          pushToast('error', '创建调度前需要先保存当前流程');
          return false;
        }
        setCurrentFlow(flow);
        setFlows((current) => [flow, ...current.filter((item) => item.flowId !== flow.flowId)]);
        target = { flowId: flow.flowId, flowIds: [], flowName: flow.name, browserExecutor: flow.defaultBrowserExecutor };
      }

      const result = await callBridge((api) =>
        api.createSchedule({
          name: normalizeScheduleName(options.name),
          cronExpression: normalizeCronExpression(options.cronExpression),
          timezone: normalizeScheduleTimezone(options.timezone),
          enabled: options.enabled ?? true,
          task: {
            mode: 'run',
            adaptive: true,
            autoSave: true,
            ...target,
            timeoutMs: 30_000
          }
        })
      );
      if (result !== null) {
        setSchedules((current) => [...current.filter((item) => item.scheduleId !== result.scheduleId), result]);
        pushToast('success', `已创建调度 ${result.name}`);
      }
      return result !== null;
    },
    updateScheduleEnabled: async (scheduleId: string, enabled: boolean) => {
      const result = await callBridge((api) => api.updateSchedule(scheduleId, { enabled }));
      if (result !== null) {
        setSchedules((current) => [...current.filter((item) => item.scheduleId !== result.scheduleId), result]);
        pushToast('success', `${result.name} 已${result.status === 'enabled' ? '启用' : '停用'}`);
      }
    },
    updateSchedule: async (scheduleId: string, options: CreateScheduleOptions) => {
      const existing = (await callBridge((api) => api.listSchedules()))?.find((schedule) => schedule.scheduleId === scheduleId);
      if (existing === undefined) return false;
      let target: ScheduleTarget | null;
      try {
        target = resolveScheduleTarget(flows, options);
      } catch (error) {
        pushToast('error', error instanceof Error ? error.message : '未找到所选流程');
        return false;
      }
      // null 表示未编辑绑定；所有流程则显式清空 flowId 和 flowIds。
      const result = await callBridge((api) =>
        api.updateSchedule(scheduleId, {
          name: options.name?.trim() ? options.name.trim() : undefined,
          cronExpression: options.cronExpression ? normalizeCronExpression(options.cronExpression) : undefined,
          timezone: options.timezone?.trim() ? options.timezone.trim() : undefined,
          enabled: options.enabled,
          task: target === null ? undefined : { ...existing.task, ...target }
        })
      );
      if (result !== null) {
        setSchedules((current) => [...current.filter((item) => item.scheduleId !== result.scheduleId), result]);
        pushToast('success', `调度 ${result.name} 已更新`);
      }
      return result !== null;
    },
    deleteSchedule: async (scheduleId: string) => {
      const result = await callBridge((api) => api.deleteSchedule(scheduleId));
      if (result !== null && result.deleted) {
        setSchedules((current) => current.filter((item) => item.scheduleId !== scheduleId));
        pushToast('success', '调度已删除');
      }
    },
    triggerSchedule: async (scheduleId: string) => {
      const result = await callBridge((api) => api.triggerSchedule(scheduleId));
      if (result !== null) {
        setSchedules((current) => [...current.filter((item) => item.scheduleId !== result.schedule.scheduleId), result.schedule]);
        if (result.run !== null && result.run !== undefined) {
          setActiveRunId(result.run.runId);
          setActiveRunFlowId(result.run.flowId ?? result.schedule.task.flowId ?? null);
          setRuntimeStatus(result.run.status);
        }
        pushToast('info', `已触发调度 ${result.schedule.name}`);
      }
    }
  };
}

function normalizeScheduleName(value: string | undefined): string {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : '未命名计划';
}

function normalizeScheduleTimezone(value: string | undefined): string {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : 'Asia/Shanghai';
}

function normalizeCronExpression(value: string | undefined): string {
  const normalized = typeof value === 'string' ? value.trim().replace(/\s+/g, ' ') : '';
  return normalized.length > 0 ? normalized : '0 9 * * *';
}

type ScheduleTarget = Pick<ScheduleTaskPayload, 'flowId' | 'flowIds' | 'flowName' | 'browserExecutor'>;

export function resolveScheduleTarget(flows: FlowSnapshot[], options: Pick<CreateScheduleOptions, 'flowId' | 'flowIds'>): ScheduleTarget | null {
  const ids = options.flowIds ?? (options.flowId === '__all__' ? [] : options.flowId ? [options.flowId] : undefined);
  if (ids === undefined) return null;
  const selected = ids.map((id) => flows.find((flow) => flow.flowId === id));
  if (selected.some((flow) => flow === undefined)) throw new Error('未找到所选流程');
  const single = selected.length === 1 ? selected[0] : undefined;
  return {
    flowId: single?.flowId,
    flowIds: ids.length > 1 ? ids : [],
    flowName: single?.name ?? (ids.length === 0 ? '所有流程' : `${ids.length} 个流程`),
    browserExecutor: single?.defaultBrowserExecutor ?? 'playwright',
  };
}
