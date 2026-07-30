import type {
  AiConfig,
  AiConfigPatch,
  AiModelCatalogPatch,
  AiModelCatalogUpdatePatch,
  AiModelsResult,
  AiModelTestPayload,
  AiModelTestResult,
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
  ScheduleCreatePayload,
  ScheduleSnapshot,
  ScheduleUpdatePayload,
  SiteAnalysisResult,
  TaskSnapshot
} from '../types/electron';
import type { RuntimeVariable } from '../types/rpa';
import { buildWebSocketUrl } from './websocket';

type HealthResponse = {
  status: 'ok';
  service: string;
};

/** 带状态码的后端错误。断网、超时抛的是普通 Error，据此才能把"后端说没有"和"没问到后端"分开。 */
export class BackendHttpError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'BackendHttpError';
    this.status = status;
  }
}

type RequestOptions = {
  method: 'DELETE' | 'GET' | 'PATCH' | 'POST' | 'PUT';
  body?: unknown;
  timeoutMs: number;
  /** 调用方自己的取消信号（组件卸载等），与超时信号一起生效。 */
  signal?: AbortSignal;
};

export type ExtensionStatus = {
  connected: boolean;
  enabled: boolean;
  connectedSince: string | null;
};

export type NotificationConfig = {
  dingtalk_enabled: boolean;
  dingtalk_webhook_url: string;
  dingtalk_secret: string;
};

export type CsvPreviewData = {
  path: string;
  headers: string[];
  rows: string[][];
  total_rows: number;
  truncated: boolean;
};

/** Base URL used when running in a plain browser without the Electron preload. */
export const DEFAULT_BROWSER_BACKEND_URL = import.meta.env.VITE_RPA_BACKEND_URL ?? 'http://127.0.0.1:8765';

/** Browser-only HTTP client for the RPA backend; Electron routes the same calls through IPC instead. */
export class BackendClient {
  readonly baseUrl: string;

  constructor(baseUrl = DEFAULT_BROWSER_BACKEND_URL) {
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

  async getFlow(flowId: string): Promise<FlowSnapshot> {
    assertId(flowId, 'flowId');
    return await this.request<FlowSnapshot>(`/api/flows/${encodeURIComponent(flowId)}`, {
      method: 'GET',
      timeoutMs: 3000
    });
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
    payload: Pick<RunStartPayload, 'browserExecutor' | 'concurrency' | 'failureStrategy' | 'mode' | 'scope' | 'screenshot' | 'startNodeId' | 'variables'>
  ): Promise<TaskSnapshot> {
    assertId(flowId, 'flowId');
    return await this.request<TaskSnapshot>(`/api/flows/${encodeURIComponent(flowId)}/run`, {
      body: {
        mode: payload.mode === 'debug' ? 'debug' : 'run',
        browserExecutor: payload.browserExecutor === 'extension' ? 'extension' : 'playwright',
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

  async resumeHumanTakeover(taskId: string, resumeMode: string): Promise<TaskSnapshot> {
    assertId(taskId, 'taskId');
    return await this.request<TaskSnapshot>(`/api/tasks/${encodeURIComponent(taskId)}/resume`, {
      body: { resume_mode: resumeMode },
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

  async getAiConfig(): Promise<AiConfig> {
    return await this.request<AiConfig>('/api/ai/config', { method: 'GET', timeoutMs: 3000 });
  }

  async setAiConfig(payload: AiConfigPatch): Promise<AiConfig> {
    return await this.request<AiConfig>('/api/ai/config', { body: payload, method: 'PUT', timeoutMs: 5000 });
  }

  async listAiModels(signal?: AbortSignal): Promise<AiModelsResult> {
    return await this.request<AiModelsResult>('/api/ai/models', { method: 'GET', signal, timeoutMs: 3000 });
  }

  async addAiModel(payload: AiModelCatalogPatch): Promise<AiModelsResult> {
    return await this.request<AiModelsResult>('/api/ai/models', { body: payload, method: 'POST', timeoutMs: 5000 });
  }

  async updateAiModel(payload: AiModelCatalogUpdatePatch): Promise<AiModelsResult> {
    return await this.request<AiModelsResult>('/api/ai/models', { body: payload, method: 'PUT', timeoutMs: 5000 });
  }

  async deleteAiModel(modelId: string): Promise<AiModelsResult> {
    return await this.request<AiModelsResult>('/api/ai/models', { body: { id: modelId }, method: 'DELETE', timeoutMs: 5000 });
  }

  async testAiModel(payload: AiModelTestPayload): Promise<AiModelTestResult> {
    return await this.request<AiModelTestResult>('/api/ai/test-model', { body: payload, method: 'POST', timeoutMs: 30000 });
  }

  /** 消息形态由 AI 面板定义，不让 HTTP 层依赖它。 */
  async getAiChat<T>(key: string): Promise<{ messages: T[] }> {
    assertId(key, 'chatKey');
    return await this.request<{ messages: T[] }>(`/api/ai/chats/${encodeURIComponent(key)}`, { method: 'GET', timeoutMs: 3000 });
  }

  async saveAiChat(key: string, messages: unknown[]): Promise<void> {
    assertId(key, 'chatKey');
    await this.request(`/api/ai/chats/${encodeURIComponent(key)}`, { body: { messages }, method: 'PUT', timeoutMs: 5000 });
  }

  /** 草稿保存成流程后把对话搬到新 key；目标已有对话时后端不搬，返回 moved=false。 */
  async renameAiChat(fromKey: string, toKey: string): Promise<boolean> {
    assertId(fromKey, 'chatKey');
    assertId(toKey, 'chatKey');
    const result = await this.request<{ moved: boolean }>(`/api/ai/chats/${encodeURIComponent(fromKey)}/rename`, {
      body: { toKey },
      method: 'POST',
      timeoutMs: 5000
    });
    return result.moved;
  }

  async deleteAiChat(key: string): Promise<void> {
    assertId(key, 'chatKey');
    await this.request(`/api/ai/chats/${encodeURIComponent(key)}`, { method: 'DELETE', timeoutMs: 3000 });
  }

  async applyAiDiff(diff: unknown): Promise<void> {
    await this.request('/api/ai/diff/apply', { body: { diff }, method: 'POST', timeoutMs: 8000 });
  }

  /** 不走 request()：SSE 要流式读 response.body，且存活时长由调用方 signal 定，套不了固定超时。 */
  async streamAiChat(body: unknown, signal: AbortSignal): Promise<Response> {
    const response = await fetch(`${this.baseUrl}/api/ai/chat`, {
      body: JSON.stringify(body),
      headers: { 'content-type': 'application/json' },
      method: 'POST',
      signal
    });
    if (!response.ok) {
      throw new Error(readErrorDetail(parseJson(await response.text())) ?? `HTTP ${response.status}`);
    }
    return response;
  }

  async getExtensionStatus(): Promise<ExtensionStatus> {
    return await this.request<ExtensionStatus>('/api/extension/status', { method: 'GET', timeoutMs: 2000 });
  }

  async setExtensionConfig(payload: { enabled: boolean }): Promise<{ enabled: boolean }> {
    return await this.request('/api/extension/config', { body: payload, method: 'PUT', timeoutMs: 5000 });
  }

  async getNotificationConfig(): Promise<NotificationConfig> {
    return await this.request<NotificationConfig>('/api/notifications/config', { method: 'GET', timeoutMs: 3000 });
  }

  async setNotificationConfig(payload: Record<string, unknown>): Promise<NotificationConfig> {
    return await this.request<NotificationConfig>('/api/notifications/config', { body: payload, method: 'PUT', timeoutMs: 5000 });
  }

  async previewCsv(path: string): Promise<CsvPreviewData> {
    return await this.request<CsvPreviewData>(`/api/workspace/preview-csv?path=${encodeURIComponent(path)}`, {
      method: 'GET',
      timeoutMs: 8000
    });
  }

  createLogSocket(taskId: string): WebSocket {
    assertId(taskId, 'taskId');
    return new WebSocket(buildWebSocketUrl(this.baseUrl, `/ws/tasks/${encodeURIComponent(taskId)}/logs`));
  }

  private async request<T>(path: string, { body, method, signal, timeoutMs }: RequestOptions): Promise<T> {
    const controller = new AbortController();
    let timedOut = false;
    const timer = globalThis.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    signal?.addEventListener('abort', () => controller.abort(), { once: true });

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
        throw new BackendHttpError(readErrorDetail(data) ?? `后端请求失败：${response.status}`, response.status);
      }
      return data as T;
    } catch (error) {
      // 调用方主动取消时原样抛出 AbortError，让上层能跟"后端真的没响应"区分开
      if (timedOut && error instanceof DOMException && error.name === 'AbortError') {
        throw new Error('后端请求超时', { cause: error });
      }
      throw error;
    } finally {
      globalThis.clearTimeout(timer);
    }
  }
}

/** 全应用唯一的后端 HTTP 入口。自己拼 URL 调 fetch 会绕开超时和错误解析。 */
export const backend = new BackendClient();

export type FlowFetchResult =
  | { kind: 'ok'; flow: FlowSnapshot }
  /** 后端明确回 404：流程已被删除，指向它的持久化引用应当就地清掉 */
  | { kind: 'missing' }
  /** 超时、断网、5xx：流程可能还在，据此清引用会把后端抖动变成用户数据丢失 */
  | { kind: 'unavailable' };

/** 取单个流程。区分 404 与"没问到后端"：只有前者才允许调用方丢弃对该流程的引用。 */
export async function fetchFlowSnapshot(flowId: string): Promise<FlowFetchResult> {
  try {
    return { flow: await backend.getFlow(flowId), kind: 'ok' };
  } catch (error) {
    if (error instanceof BackendHttpError && error.status === 404) {
      return { kind: 'missing' };
    }
    return { kind: 'unavailable' };
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
    flowDefinition: normalizeFlowDefinition(payload.flowDefinition) ?? {},
    flowName: normalizeString(payload.flowName, '未命名流程'),
    selector: normalizeOptionalString(payload.selector),
    targetUrl: normalizeOptionalString(payload.targetUrl)
  };
}

function normalizeRunPayload(payload: RunStartPayload): Record<string, unknown> {
  const scriptPayload = normalizeScriptPayload(payload);
  return {
    ...scriptPayload,
    browserExecutor: payload.browserExecutor === 'extension' ? 'extension' : 'playwright',
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
    acceptanceContract: payload.acceptanceContract,
    definition: payload.definition && typeof payload.definition === 'object' ? payload.definition : {},
    description: normalizeOptionalString(payload.description),
    inputVariables: Array.isArray(payload.inputVariables) ? payload.inputVariables : [],
    name: normalizeString(payload.name, '未命名流程'),
    status: (['active', 'paused', 'disabled', 'archived'] as const).includes(payload.status as never) ? payload.status as import('../types/electron').FlowStatus : 'draft',
    version: normalizeString(payload.version, 'v1.0.0'),
    defaultBrowserExecutor: payload.defaultBrowserExecutor === 'extension' ? 'extension' : undefined
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

/** 并发上限硬编码为 20，防止误配置打满后端浏览器实例池。 */
function normalizeConcurrency(value: number | undefined): number {
  if (value === undefined || !Number.isFinite(value)) {
    return 1;
  }
  return Math.min(20, Math.max(1, Math.round(value)));
}

/** 分页上限硬编码为 200，避免一次性拉取过多历史记录拖慢列表页。 */
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

/** FastAPI error bodies are `{"detail": ...}`. */
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
