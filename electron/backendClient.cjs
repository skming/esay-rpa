const {
  normalizeAnalyzePayload,
  normalizeDebugCommand,
  normalizeFlowPayload,
  normalizeFlowRunPayload,
  normalizeLimit,
  normalizeRunPayload,
  normalizeSchedulePayload,
  normalizeScriptPayload
} = require('../shared/backendPayloads.cjs');
const DEFAULT_BACKEND_URL = process.env.RPA_BACKEND_URL || 'http://127.0.0.1:8765';
const { buildWebSocketUrl } = require('./websocket.cjs');

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
      body: normalizeScriptPayload(payload),
      timeoutMs: 5000
    });
  }

  async analyzeSite(payload = {}) {
    return this.#request('/api/site/analyze', {
      method: 'POST',
      body: normalizeAnalyzePayload(payload),
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
      body: normalizeFlowPayload(payload),
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

  async duplicateFlow(flowId) {
    if (typeof flowId !== 'string' || flowId.length === 0) {
      throw new Error('flowId is required.');
    }
    return this.#request(`/api/flows/${encodeURIComponent(flowId)}/duplicate`, {
      method: 'POST',
      timeoutMs: 5000
    });
  }

  async moveFlow(flowId, folderPath) {
    if (typeof flowId !== 'string' || flowId.length === 0) {
      throw new Error('flowId is required.');
    }
    return this.#request(`/api/flows/${encodeURIComponent(flowId)}/move`, {
      method: 'PATCH',
      body: { folderPath },
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
      body: normalizeFlowRunPayload(payload),
      timeoutMs: 5000
    });
  }

  async startTask(payload = {}) {
    return this.#request('/api/tasks', {
      method: 'POST',
      body: normalizeRunPayload(payload),
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

  async resumeConfirmation(taskId) {
    if (typeof taskId !== 'string' || taskId.length === 0) {
      throw new Error('taskId is required.');
    }
    return this.#request(`/api/tasks/${encodeURIComponent(taskId)}/resume`, {
      method: 'POST',
      timeoutMs: 5000
    });
  }

  async debugTask(taskId, command) {
    if (typeof taskId !== 'string' || taskId.length === 0) {
      throw new Error('taskId is required.');
    }
    return this.#request(`/api/tasks/${encodeURIComponent(taskId)}/debug`, {
      method: 'POST',
      body: { command: normalizeDebugCommand(command) },
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
    const limit = normalizeLimit(options.limit);
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
    const limit = normalizeLimit(options.limit);
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

  async addAiModel(payload = {}) {
    return this.#request('/api/ai/models', {
      method: 'POST',
      body: payload,
      timeoutMs: 5000
    });
  }

  async updateAiModel(payload = {}) {
    return this.#request('/api/ai/models', {
      method: 'PUT',
      body: payload,
      timeoutMs: 5000
    });
  }

  async deleteAiModel(modelId) {
    if (typeof modelId !== 'string' || modelId.length === 0) {
      throw new Error('modelId is required.');
    }
    return this.#request('/api/ai/models', {
      method: 'DELETE',
      body: { id: modelId },
      timeoutMs: 5000
    });
  }

  async testAiModel(payload = {}) {
    return this.#request('/api/ai/test-model', {
      method: 'POST',
      body: payload,
      timeoutMs: 30000
    });
  }

  async listSchedules() {
    return this.#request('/api/schedules', {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async listScheduleRunSummaries() {
    return this.#request('/api/schedules:run-summaries', {
      method: 'GET',
      timeoutMs: 3000
    });
  }

  async previewSchedule(cronExpression, timezone) {
    const query = new URLSearchParams({ cronExpression, timezone });
    return this.#request(`/api/schedules:preview?${query}`, { method: 'GET', timeoutMs: 3000 });
  }

  async createSchedule(payload = {}) {
    return this.#request('/api/schedules', {
      method: 'POST',
      body: normalizeSchedulePayload(payload),
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
    return new WebSocket(buildWebSocketUrl(this.baseUrl, `/ws/tasks/${encodeURIComponent(taskId)}/logs`));
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
        const rejected = new Error(typeof detail === 'string' ? detail : `后端请求失败：${response.status}`);
        // 带上 status 才能区分「后端答复了但拒绝这次请求」和「后端不可用」：调用方对这两者的处置相反，
        // 只看 message 分不出来，就只能把业务拒绝当成连不上处理。
        rejected.status = response.status;
        throw rejected;
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
