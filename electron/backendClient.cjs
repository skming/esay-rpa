const DEFAULT_BACKEND_URL = process.env.RPA_BACKEND_URL || 'http://127.0.0.1:8765';

class BackendClient {
  constructor(baseUrl = DEFAULT_BACKEND_URL) {
    this.baseUrl = String(baseUrl).replace(/\/$/, '');
  }

  async health() {
    return this.#request('/api/health', { method: 'GET', timeoutMs: 1200 });
  }

  async generateScript(payload = {}) {
    return this.#request('/api/code/generate', {
      method: 'POST',
      body: this.#normalizeScriptPayload(payload),
      timeoutMs: 5000
    });
  }

  async analyzeSite(payload = {}) {
    return this.#request('/api/site/analyze', {
      method: 'POST',
      body: this.#normalizeAnalyzePayload(payload),
      timeoutMs: 8000
    });
  }

  async listFlows() {
    return this.#request('/api/flows', {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async createFlow(payload = {}) {
    return this.#request('/api/flows', {
      method: 'POST',
      body: this.#normalizeFlowPayload(payload),
      timeoutMs: 10000
    });
  }

  async updateFlow(flowId, payload = {}) {
    if (typeof flowId !== 'string' || flowId.length === 0) {
      throw new Error('flowId is required.');
    }
    return this.#request(`/api/flows/${encodeURIComponent(flowId)}`, {
      method: 'PATCH',
      body: payload,
      timeoutMs: 5000
    });
  }

  async archiveFlow(flowId) {
    if (typeof flowId !== 'string' || flowId.length === 0) {
      throw new Error('flowId is required.');
    }
    return this.#request(`/api/flows/${encodeURIComponent(flowId)}/archive`, {
      method: 'POST',
      timeoutMs: 5000
    });
  }

  async setFlowStatus(flowId, status) {
    if (typeof flowId !== 'string' || flowId.length === 0) {
      throw new Error('flowId is required.');
    }
    return this.#request(`/api/flows/${encodeURIComponent(flowId)}/status`, {
      method: 'PATCH',
      body: { status },
      timeoutMs: 5000
    });
  }

  async deleteFlow(flowId) {
    if (typeof flowId !== 'string' || flowId.length === 0) {
      throw new Error('flowId is required.');
    }
    return this.#request(`/api/flows/${encodeURIComponent(flowId)}`, {
      method: 'DELETE',
      timeoutMs: 3000
    });
  }

  async runFlow(flowId, payload = {}) {
    if (typeof flowId !== 'string' || flowId.length === 0) {
      throw new Error('flowId is required.');
    }
    return this.#request(`/api/flows/${encodeURIComponent(flowId)}/run`, {
      method: 'POST',
      body: this.#normalizeFlowRunPayload(payload),
      timeoutMs: 5000
    });
  }

  async startTask(payload = {}) {
    return this.#request('/api/tasks', {
      method: 'POST',
      body: this.#normalizeRunPayload(payload),
      timeoutMs: 5000
    });
  }

  async stopTask(taskId) {
    if (typeof taskId !== 'string' || taskId.length === 0) {
      throw new Error('taskId is required.');
    }
    return this.#request(`/api/tasks/${encodeURIComponent(taskId)}/stop`, {
      method: 'POST',
      timeoutMs: 3000
    });
  }

  async provideInput(taskId, value) {
    if (typeof taskId !== 'string' || taskId.length === 0) {
      throw new Error('taskId is required.');
    }
    return this.#request(`/api/tasks/${encodeURIComponent(taskId)}/input`, {
      method: 'POST',
      body: { value: typeof value === 'string' ? value : '' },
      timeoutMs: 5000
    });
  }

  async debugTask(taskId, command) {
    if (typeof taskId !== 'string' || taskId.length === 0) {
      throw new Error('taskId is required.');
    }
    return this.#request(`/api/tasks/${encodeURIComponent(taskId)}/debug`, {
      method: 'POST',
      body: { command: this.#normalizeDebugCommand(command) },
      timeoutMs: 3000
    });
  }

  async getTask(taskId) {
    return this.#request(`/api/tasks/${encodeURIComponent(taskId)}`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async listTasks(options = {}) {
    const limit = Number.isInteger(options.limit) ? Math.min(Math.max(options.limit, 1), 200) : 50;
    const params = new URLSearchParams({ limit: String(limit) });
    if (typeof options.flowId === 'string' && options.flowId.trim()) {
      params.set('flowId', options.flowId.trim());
    }
    return this.#request(`/api/tasks?${params.toString()}`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async listFlowRuns(flowId, options = {}) {
    if (typeof flowId !== 'string' || flowId.length === 0) {
      throw new Error('flowId is required.');
    }
    const limit = Number.isInteger(options.limit) ? Math.min(Math.max(options.limit, 1), 200) : 50;
    return this.#request(`/api/flows/${encodeURIComponent(flowId)}/runs?limit=${encodeURIComponent(String(limit))}`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async getLogs(taskId) {
    return this.#request(`/api/tasks/${encodeURIComponent(taskId)}/logs`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async getVariables(taskId) {
    return this.#request(`/api/tasks/${encodeURIComponent(taskId)}/variables`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async getArtifacts(taskId) {
    return this.#request(`/api/tasks/${encodeURIComponent(taskId)}/artifacts`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async readArtifact(taskId, artifactId) {
    return this.#request(`/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}`, {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async getQueueStats() {
    return this.#request('/api/queue', {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async getAiConfig() {
    return this.#request('/api/ai/config', {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async setAiConfig(payload = {}) {
    return this.#request('/api/ai/config', {
      method: 'PUT',
      body: payload,
      timeoutMs: 5000
    });
  }

  async listAiModels() {
    return this.#request('/api/ai/models', {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async listSchedules() {
    return this.#request('/api/schedules', {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async createSchedule(payload = {}) {
    return this.#request('/api/schedules', {
      method: 'POST',
      body: this.#normalizeSchedulePayload(payload),
      timeoutMs: 5000
    });
  }

  async updateSchedule(scheduleId, payload = {}) {
    return this.#request(`/api/schedules/${encodeURIComponent(scheduleId)}`, {
      method: 'PATCH',
      body: payload,
      timeoutMs: 5000
    });
  }

  async deleteSchedule(scheduleId) {
    return this.#request(`/api/schedules/${encodeURIComponent(scheduleId)}`, {
      method: 'DELETE',
      timeoutMs: 3000
    });
  }

  async triggerSchedule(scheduleId) {
    return this.#request(`/api/schedules/${encodeURIComponent(scheduleId)}/trigger`, {
      method: 'POST',
      timeoutMs: 5000
    });
  }

  createLogSocket(taskId) {
    const wsUrl = this.baseUrl.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:');
    return new WebSocket(`${wsUrl}/ws/tasks/${encodeURIComponent(taskId)}/logs`);
  }

  #normalizeScriptPayload(payload) {
    return {
      flowName: typeof payload.flowName === 'string' && payload.flowName.trim() ? payload.flowName : '未命名流程',
      targetUrl: typeof payload.targetUrl === 'string' && payload.targetUrl.trim() ? payload.targetUrl : '',
      selector: typeof payload.selector === 'string' && payload.selector.trim() ? payload.selector : '',
      fetcher: payload.fetcher ?? 'static',
      extractMode: payload.extractMode ?? 'text',
      attribute: payload.attribute ?? undefined,
      adaptive: Boolean(payload.adaptive),
      autoSave: Boolean(payload.autoSave)
    };
  }

  #normalizeRunPayload(payload) {
    return {
      ...this.#normalizeScriptPayload(payload),
      mode: payload.mode === 'debug' ? 'debug' : 'run',
      flowId: typeof payload.flowId === 'string' && payload.flowId.trim() ? payload.flowId : undefined,
      flowDefinition: payload.flowDefinition && typeof payload.flowDefinition === 'object' && !Array.isArray(payload.flowDefinition) ? payload.flowDefinition : undefined,
      scope: this.#normalizeRunScope(payload.scope),
      startNodeId: typeof payload.startNodeId === 'string' && payload.startNodeId.trim() ? payload.startNodeId.trim() : undefined,
      failureStrategy: this.#normalizeFailureStrategy(payload.failureStrategy),
      screenshot: payload.screenshot !== false,
      concurrency: this.#normalizeConcurrency(payload.concurrency),
      timeoutMs: Number.isInteger(payload.timeoutMs) ? payload.timeoutMs : 30_000,
      variables: payload.variables && typeof payload.variables === 'object' && !Array.isArray(payload.variables) ? payload.variables : {}
    };
  }

  #normalizeFlowRunPayload(payload) {
    return {
      mode: payload.mode === 'debug' ? 'debug' : 'run',
      variables: payload.variables && typeof payload.variables === 'object' && !Array.isArray(payload.variables) ? payload.variables : {},
      timeoutMs: Number.isInteger(payload.timeoutMs) ? payload.timeoutMs : 30_000,
      scope: this.#normalizeRunScope(payload.scope),
      screenshot: payload.screenshot !== false,
      startNodeId: typeof payload.startNodeId === 'string' && payload.startNodeId.trim() ? payload.startNodeId.trim() : undefined,
      failureStrategy: this.#normalizeFailureStrategy(payload.failureStrategy),
      concurrency: this.#normalizeConcurrency(payload.concurrency)
    };
  }

  #normalizeAnalyzePayload(payload) {
    return {
      targetUrl: typeof payload.targetUrl === 'string' && payload.targetUrl.trim() ? payload.targetUrl : '',
      selector: typeof payload.selector === 'string' && payload.selector.trim() ? payload.selector : undefined,
      fetcher: payload.fetcher ?? 'static',
      timeoutMs: Number.isInteger(payload.timeoutMs) ? payload.timeoutMs : 30_000,
      maxCandidates: Number.isInteger(payload.maxCandidates) ? payload.maxCandidates : 8
    };
  }

  #normalizeFlowPayload(payload) {
    return {
      name: typeof payload.name === 'string' && payload.name.trim() ? payload.name : '未命名流程',
      version: typeof payload.version === 'string' && payload.version.trim() ? payload.version : 'v1.0.0',
      description: typeof payload.description === 'string' ? payload.description : undefined,
      definition: payload.definition && typeof payload.definition === 'object' ? payload.definition : {},
      inputVariables: Array.isArray(payload.inputVariables) ? payload.inputVariables : [],
      folderPath: typeof payload.folderPath === 'string' && payload.folderPath.trim() ? payload.folderPath : undefined,
      status: payload.status === 'active' || payload.status === 'archived' ? payload.status : 'draft'
    };
  }

  #normalizeSchedulePayload(payload) {
    return {
      name: typeof payload.name === 'string' && payload.name.trim() ? payload.name : '未命名计划',
      cronExpression: typeof payload.cronExpression === 'string' && payload.cronExpression.trim() ? payload.cronExpression : '0 9 * * *',
      timezone: typeof payload.timezone === 'string' && payload.timezone.trim() ? payload.timezone : 'Asia/Shanghai',
      enabled: payload.enabled !== false,
      task: this.#normalizeRunPayload(payload.task ?? {})
    };
  }

  #normalizeConcurrency(value) {
    if (!Number.isFinite(value)) {
      return 1;
    }
    return Math.min(20, Math.max(1, Math.round(value)));
  }

  #normalizeRunScope(value) {
    return value === 'from-selection' || value === 'selected-only' ? value : 'full';
  }

  #normalizeFailureStrategy(value) {
    return value === 'continue' || value === 'retry' ? value : 'stop';
  }

  #normalizeDebugCommand(value) {
    if (value === 'continue' || value === 'step-over' || value === 'step-into') {
      return value;
    }
    throw new Error('Debug command is invalid.');
  }

  async #request(path, { method, body, timeoutMs }) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: body === undefined ? undefined : { 'content-type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal
      });

      const text = await response.text();
      let data = null;
      try {
        data = text.length > 0 ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (!response.ok) {
        const detail = data !== null && typeof data === 'object' ? data.detail : (typeof data === 'string' ? data : null);
        throw new Error(typeof detail === 'string' ? detail : `后端请求失败：${response.status}`);
      }
      return data;
    } catch (error) {
      if (error && error.name === 'AbortError') {
        throw new Error('后端请求超时，请检查服务是否正常运行');
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }
}

module.exports = {
  BackendClient,
  DEFAULT_BACKEND_URL
};
