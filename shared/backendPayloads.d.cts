import type {
  AnalyzeSitePayload,
  DebugControlCommand,
  FlowSavePayload,
  GenerateScriptPayload,
  RunStartPayload,
  ScheduleCreatePayload
} from '../src/types/electron';

export function normalizeAnalyzePayload(payload?: Partial<AnalyzeSitePayload>): Record<string, unknown>;
export function normalizeConcurrency(value?: number): number;
export function normalizeDebugCommand(value: DebugControlCommand): DebugControlCommand;
export function normalizeFlowPayload(payload?: Partial<FlowSavePayload>): FlowSavePayload;
export function normalizeFlowRunPayload(payload?: Partial<RunStartPayload>): Record<string, unknown>;
export function normalizeLimit(value?: number): number;
export function normalizeRunPayload(payload?: Partial<RunStartPayload>): Record<string, unknown>;
export function normalizeSchedulePayload(payload?: Partial<ScheduleCreatePayload>): ScheduleCreatePayload;
export function normalizeScriptPayload(payload?: Partial<GenerateScriptPayload>): GenerateScriptPayload;
