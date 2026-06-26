import type {
  AnalyzeSitePayload,
  ArtifactContent,
  ArtifactSnapshot,
  DebugControlCommand,
  FlowSavePayload,
  FlowSnapshot,
  FlowStatus,
  FlowUpdatePayload,
  GeneratedScriptResult,
  GenerateScriptPayload,
  QueueStats,
  RunStartPayload,
  RunMode,
  ScheduleCreatePayload,
  ScheduleSnapshot,
  ScheduleUpdatePayload,
  SiteAnalysisResult,
  TaskSnapshot
} from '../types/electron';
import type { RuntimeVariable } from '../types/rpa';

type HealthResponse = {
  status: 'ok';
  service: string;
};

type RequestOptions = {
  method: 'DELETE' | 'GET' | 'PATCH' | 'POST';
  body?: unknown;
  timeoutMs: number;
};

/** Base URL used when running in a plain browser without the Electron preload. */
export const DEFAULT_BROWSER_BACKEND_URL = import.meta.env.VITE_RPA_BACKEND_URL ?? 'http://127.0.0.1:8765';

/**
 * Thin HTTP client for the RPA backend REST API. Used in browser-only mode;
 * the Electron app routes the same calls through the IPC bridge instead.
 */
export class BackendClient {
  readonly baseUrl: string;

  constructor(baseUrl = DEFAULT_BROWSER_BACKEND_URL) {
    // Strip trailing slash so path concatenation is always consistent.
    this.baseUrl = String(baseUrl).replace(/\/$/, '');
  }

  async health(): Promise<HealthResponse> {
    return await this.request<HealthResponse>('/api/health', { method: 'GET', timeoutMs: 1200 });
  }

  async generateScript(payload: Partial<GenerateScriptPayload> = {}): Promise<GeneratedScriptResult> {
    return await this.request<GeneratedScriptResult>('/api/code/generate', {
      body: normalizeScriptPayload(payload),
      method: 'POST',
      timeoutMs: 5000
    });
  }

  async analyzeSite(payload: Partial<AnalyzeSitePayload> = {}): Promise<SiteAnalysisResult> {
    return await this.request<SiteAnalysisResult>('/api/site/analyze', {
      body: normalizeAnalyzePayload(payload),
      method: 'POST',
      timeoutMs: 8000
    });
  }

  async listFlows(): Promise<FlowSnapshot[]> {
    return await this.request<FlowSnapshot[]>('/api/flows', { method: 'GET', timeoutMs: 3000 });
  }

  async createFlow(payload: FlowSavePayload): Promise<FlowSnapshot> {
    return await this.request<FlowSnapshot>('/api/flows', {
      body: normalizeFlowPayload(payload),
      method: 'POST',
      timeoutMs: 5000
    });
  }

  async updateFlow(flowId: string, payload: FlowUpdatePayload): Promise<FlowSnapshot> {
    assertId(flowId, 'flowId');
    return await this.request<FlowSnapshot>(`/api/flows/${encodeURIComponent(flowId)}`, {
      body: payload,
      method: 'PATCH',
      timeoutMs: 5000
    });
  }

  async archiveFlow(flowId: string): Promise<FlowSnapshot> {
    assertId(flowId, 'flowId');
    return await this.request<FlowSnapshot>(`/api/flows/${encodeURIComponent(flowId)}/archive`, {
      method: 'POST',
      timeoutMs: 5000
    });
  }

  async duplicateFlow(flowId: string): Promise<FlowSnapshot> {
    assertId(flowId, 'flowId');
    return await this.request<FlowSnapshot>(`/api/flows/${encodeURIComponent(flowId)}/duplicate`, {
      method: 'POST',
      timeoutMs: 5000
    });
  }

  async moveFlow(flowId: string, folderPath: string): Promise<FlowSnapshot> {
    assertId(flowId, 'flowId');
    return await this.request<FlowSnapshot>(`/api/flows/${encodeURIComponent(flowId)}/move`, {
      method: 'PATCH',
      body: { folderPath },
      timeoutMs: 5000
    });
  }

  async setFlowStatus(flowId: string, status: FlowStatus): Promise<FlowSnapshot> {
    assertId(flowId, 'flowId');
    return await this.request<FlowSnapshot>(`/api/flows/${encodeURIComponent(flowId)}/status`, {
      method: 'PATCH',
      body: { status },
      timeoutMs: 5000
    });
  }

  async deleteFlow(flowId: string): Promise<{ deleted: boolean }> {
    assertId(flowId, 'flowId');
    return await this.request<{ deleted: boolean }>(`/api/flows/${encodeURIComponent(flowId)}`, {
      method: 'DELETE',
      timeoutMs: 3000
    });
  }

  async runFlow(
    flowId: string,
    payload: Pick<RunStartPayload, 'concurrency' | 'failureStrategy' | 'mode' | 'scope' | 'screenshot' | 'startNodeId' | 'variables'>
  ): Promise<TaskSnapshot> {
    assertId(flowId, 'flowId');
    return await this.request<TaskSnapshot>(`/api/flows/${encodeURIComponent(flowId)}/run`, {
      body: {
        mode: payload.mode === 'debug' ? 'debug' : 'run',
        variables: normalizeVariables(payload.variables),
        timeoutMs: 30_000,
        scope: normalizeRunScope(payload.scope),
        screenshot: payload.screenshot !== false,
        startNodeId: normalizeOptionalString(payload.startNodeId),
        failureStrategy: normalizeFailureStrategy(payload.failureStrategy),
        concurrency: normalizeConcurrency(payload.concurrency)
      },
      method: 'POST',
      timeoutMs: 5000
    });
  }

  async startTask(payload: RunStartPayload): Promise<TaskSnapshot> {
    return await this.request<TaskSnapshot>('/api/tasks', {
      body: normalizeRunPayload(payload),
      method: 'POST',
      timeoutMs: 5000
    });
  }

  async stopTask(taskId: string): Promise<TaskSnapshot> {
    assertId(taskId, 'taskId');
    return await this.request<TaskSnapshot>(`/api/tasks/${encodeURIComponent(taskId)}/stop`, {
      method: 'POST',
      timeoutMs: 3000
    });
  }

  async provideInput(taskId: string, value: string): Promise<TaskSnapshot> {
    assertId(taskId, 'taskId');
    return await this.request<TaskSnapshot>(`/api/tasks/${encodeURIComponent(taskId)}/input`, {
      body: { value },
      method: 'POST',
      timeoutMs: 5000
    });
  }

  async debugTask(taskId: string, command: DebugControlCommand): Promise<TaskSnapshot> {
    assertId(taskId, 'taskId');
    return await this.request<TaskSnapshot>(`/api/tasks/${encodeURIComponent(taskId)}/debug`, {
      body: { command: normalizeDebugCommand(command) },
      method: 'POST',
      timeoutMs: 3000
    });
  }

  async getTask(taskId: string): Promise<TaskSnapshot> {
    assertId(taskId, 'taskId');
    return await this.request<TaskSnapshot>(`/api/tasks/${encodeURIComponent(taskId)}`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async listTasks(options: { flowId?: string; limit?: number } = {}): Promise<TaskSnapshot[]> {
    const params = new URLSearchParams({ limit: String(normalizeLimit(options.limit)) });
    if (typeof options.flowId === 'string' && options.flowId.trim()) {
      params.set('flowId', options.flowId.trim());
    }
    return await this.request<TaskSnapshot[]>(`/api/tasks?${params.toString()}`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async listFlowRuns(flowId: string, options: { limit?: number } = {}): Promise<TaskSnapshot[]> {
    assertId(flowId, 'flowId');
    const params = new URLSearchParams({ limit: String(normalizeLimit(options.limit)) });
    return await this.request<TaskSnapshot[]>(`/api/flows/${encodeURIComponent(flowId)}/runs?${params.toString()}`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async getLogs(taskId: string): Promise<BackendTaskLogEntry[]> {
    assertId(taskId, 'taskId');
    return await this.request<BackendTaskLogEntry[]>(`/api/tasks/${encodeURIComponent(taskId)}/logs`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async getVariables(taskId: string): Promise<RuntimeVariable[]> {
    assertId(taskId, 'taskId');
    return await this.request<RuntimeVariable[]>(`/api/tasks/${encodeURIComponent(taskId)}/variables`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async getArtifacts(taskId: string): Promise<ArtifactSnapshot[]> {
    assertId(taskId, 'taskId');
    return await this.request<ArtifactSnapshot[]>(`/api/tasks/${encodeURIComponent(taskId)}/artifacts`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async readArtifact(taskId: string, artifactId: string): Promise<ArtifactContent> {
    assertId(taskId, 'taskId');
    assertId(artifactId, 'artifactId');
    return await this.request<ArtifactContent>(`/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async getQueueStats(): Promise<QueueStats> {
    return await this.request<QueueStats>('/api/queue', { method: 'GET', timeoutMs: 3000 });
  }

  async listSchedules(): Promise<ScheduleSnapshot[]> {
    return await this.request<ScheduleSnapshot[]>('/api/schedules', { method: 'GET', timeoutMs: 3000 });
  }

  async createSchedule(payload: ScheduleCreatePayload): Promise<ScheduleSnapshot> {
    return await this.request<ScheduleSnapshot>('/api/schedules', {
      body: normalizeSchedulePayload(payload),
      method: 'POST',
      timeoutMs: 5000
    });
  }

  async updateSchedule(scheduleId: string, payload: ScheduleUpdatePayload): Promise<ScheduleSnapshot> {
    assertId(scheduleId, 'scheduleId');
    return await this.request<ScheduleSnapshot>(`/api/schedules/${encodeURIComponent(scheduleId)}`, {
      body: payload,
      method: 'PATCH',
      timeoutMs: 5000
    });
  }

  async deleteSchedule(scheduleId: string): Promise<{ deleted: boolean }> {
    assertId(scheduleId, 'scheduleId');
    return await this.request<{ deleted: boolean }>(`/api/schedules/${encodeURIComponent(scheduleId)}`, {
      method: 'DELETE',
      timeoutMs: 3000
    });
  }

  async triggerSchedule(scheduleId: string): Promise<ScheduleSnapshot> {
    assertId(scheduleId, 'scheduleId');
    return await this.request<ScheduleSnapshot>(`/api/schedules/${encodeURIComponent(scheduleId)}/trigger`, {
      method: 'POST',
      timeoutMs: 5000
    });
  }

  /** Opens a WebSocket that streams log entries for the given task in real-time. */
  createLogSocket(taskId: string): WebSocket {
    assertId(taskId, 'taskId');
    const wsUrl = this.baseUrl.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:');
    return new WebSocket(`${wsUrl}/ws/tasks/${encodeURIComponent(taskId)}/logs`);
  }

  /** Generic fetch wrapper with per-request timeout and structured error extraction. */
  private async request<T>(path: string, { body, method, timeoutMs }: RequestOptions): Promise<T> {
    const controller = new AbortController();
    const timer = globalThis.setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        body: body === undefined ? undefined : JSON.stringify(body),
        headers: body === undefined ? undefined : { 'content-type': 'application/json' },
        method,
        signal: controller.signal
      });

      const text = await response.text();
      const data = parseJson(text);
      if (!response.ok) {
        throw new Error(readErrorDetail(data) ?? `后端请求失败：${response.status}`);
      }
      return data as T;
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new Error('后端请求超时');
      }
      throw error;
    } finally {
      globalThis.clearTimeout(timer);
    }
  }
}

export type BackendTaskLogEntry = {
  id: string;
  taskId?: string;
  time: string;
  level: string;
  message: string;
  detail?: string | null;
  nodeId?: string | null;
};

function normalizeScriptPayload(payload: Partial<GenerateScriptPayload>): GenerateScriptPayload {
  return {
    adaptive: payload.adaptive ?? false,
    attribute: normalizeOptionalString(payload.attribute),
    autoSave: payload.autoSave ?? false,
    extractMode: payload.extractMode ?? 'text',
    fetcher: payload.fetcher ?? 'static',
    flowName: normalizeString(payload.flowName, '未命名流程'),
    selector: normalizeString(payload.selector, ''),
    targetUrl: normalizeString(payload.targetUrl, '')
  };
}

function normalizeRunPayload(payload: RunStartPayload): Record<string, unknown> {
  const scriptPayload = normalizeScriptPayload(payload);
  return {
    ...scriptPayload,
    concurrency: normalizeConcurrency(payload.concurrency),
    failureStrategy: normalizeFailureStrategy(payload.failureStrategy),
    flowDefinition: normalizeFlowDefinition(payload.flowDefinition),
    flowId: normalizeOptionalString(payload.flowId),
    mode: payload.mode === 'debug' ? 'debug' : 'run',
    scope: normalizeRunScope(payload.scope),
    screenshot: payload.screenshot !== false,
    startNodeId: normalizeOptionalString(payload.startNodeId),
    timeoutMs: Number.isInteger(payload.timeoutMs) ? payload.timeoutMs : 30_000,
    variables: normalizeVariables(payload.variables)
  };
}

function normalizeAnalyzePayload(payload: Partial<AnalyzeSitePayload>): Record<string, string | number | undefined> {
  return {
    fetcher: payload.fetcher ?? 'static',
    maxCandidates: Number.isInteger(payload.maxCandidates) ? payload.maxCandidates : 8,
    selector: normalizeOptionalString(payload.selector),
    targetUrl: normalizeString(payload.targetUrl, ''),
    timeoutMs: Number.isInteger(payload.timeoutMs) ? payload.timeoutMs : 30_000
  };
}

function normalizeFlowPayload(payload: FlowSavePayload): FlowSavePayload {
  return {
    definition: payload.definition && typeof payload.definition === 'object' ? payload.definition : {},
    description: normalizeOptionalString(payload.description),
    inputVariables: Array.isArray(payload.inputVariables) ? payload.inputVariables : [],
    name: normalizeString(payload.name, '未命名流程'),
    status: (['active', 'paused', 'disabled', 'archived'] as const).includes(payload.status as never) ? payload.status as import('../types/electron').FlowStatus : 'draft',
    version: normalizeString(payload.version, 'v1.0.0')
  };
}

function normalizeSchedulePayload(payload: ScheduleCreatePayload): ScheduleCreatePayload {
  return {
    enabled: payload.enabled !== false,
    cronExpression: normalizeString(payload.cronExpression, '0 9 * * *'),
    name: normalizeString(payload.name, '未命名计划'),
    task: {
      ...payload.task,
      adaptive: payload.task.adaptive ?? true,
      autoSave: payload.task.autoSave ?? true,
      flowName: normalizeString(payload.task.flowName, '未命名流程'),
      mode: payload.task.mode === 'debug' ? 'debug' : 'run',
      selector: normalizeString(payload.task.selector, ''),
      targetUrl: normalizeString(payload.task.targetUrl, ''),
      timeoutMs: Number.isInteger(payload.task.timeoutMs) ? payload.task.timeoutMs : 30_000
    },
    timezone: normalizeString(payload.timezone, 'Asia/Shanghai')
  };
}

/** Guards against accidentally sending empty IDs as path segments. */
function assertId(value: string, label: string): void {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new Error(`${label} 不能为空`);
  }
}

function normalizeString(value: string | undefined, fallback: string): string {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : fallback;
}

function normalizeOptionalString(value: string | undefined): string | undefined {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : undefined;
}

function normalizeConcurrency(value: number | undefined): number {
  if (value === undefined || !Number.isFinite(value)) {
    return 1;
  }
  return Math.min(20, Math.max(1, Math.round(value)));
}

function normalizeLimit(value: number | undefined): number {
  if (value === undefined || !Number.isFinite(value)) {
    return 50;
  }
  return Math.min(200, Math.max(1, Math.round(value)));
}

function normalizeRunScope(value: RunStartPayload['scope']): NonNullable<RunStartPayload['scope']> {
  return value === 'from-selection' || value === 'selected-only' ? value : 'full';
}

function normalizeFailureStrategy(value: RunStartPayload['failureStrategy']): NonNullable<RunStartPayload['failureStrategy']> {
  return value === 'continue' || value === 'retry' ? value : 'stop';
}

function normalizeDebugCommand(value: DebugControlCommand): DebugControlCommand {
  if (value === 'continue' || value === 'step-into' || value === 'step-over') {
    return value;
  }
  throw new Error('调试命令不合法');
}

function normalizeFlowDefinition(value: RunStartPayload['flowDefinition']): Record<string, unknown> | undefined {
  return value !== undefined && value !== null && typeof value === 'object' && !Array.isArray(value) ? value : undefined;
}

function normalizeVariables(value: RunStartPayload['variables']): Record<string, unknown> {
  return value !== undefined && value !== null && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function parseJson(text: string): unknown {
  if (text.trim().length === 0) {
    return null;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

/** Extracts the human-readable message from a FastAPI `{"detail": ...}` error body. */
function readErrorDetail(data: unknown): string | null {
  if (data !== null && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === 'string') {
      return detail;
    }
    return JSON.stringify(detail);
  }
  return null;
}
