function normalizeString(value, fallback) {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function normalizeOptionalString(value) {
  return normalizeString(value, undefined);
}

function normalizeObject(value, fallback) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value : fallback;
}

function normalizeConcurrency(value) {
  return Number.isFinite(value) ? Math.min(20, Math.max(1, Math.round(value))) : 1;
}

function normalizeLimit(value) {
  return Number.isFinite(value) ? Math.min(200, Math.max(1, Math.round(value))) : 50;
}

function normalizeDebugCommand(value) {
  if (value === 'continue' || value === 'step-into' || value === 'step-over') {
    return value;
  }
  throw new Error('调试命令不合法');
}

function normalizeScriptPayload(payload = {}) {
  return {
    adaptive: Boolean(payload.adaptive),
    attribute: normalizeOptionalString(payload.attribute),
    autoSave: Boolean(payload.autoSave),
    extractMode: payload.extractMode ?? 'text',
    fetcher: payload.fetcher ?? 'static',
    flowDefinition: normalizeObject(payload.flowDefinition, {}),
    flowName: normalizeString(payload.flowName, '未命名流程'),
    selector: normalizeOptionalString(payload.selector),
    targetUrl: normalizeOptionalString(payload.targetUrl)
  };
}

function normalizeFlowRunPayload(payload = {}) {
  return {
    browserExecutor: payload.browserExecutor === 'extension' ? 'extension' : 'playwright',
    concurrency: normalizeConcurrency(payload.concurrency),
    failureStrategy: payload.failureStrategy === 'continue' || payload.failureStrategy === 'retry' ? payload.failureStrategy : 'stop',
    mode: payload.mode === 'debug' ? 'debug' : 'run',
    scope: payload.scope === 'from-selection' || payload.scope === 'selected-only' ? payload.scope : 'full',
    screenshot: payload.screenshot !== false,
    startNodeId: normalizeOptionalString(payload.startNodeId),
    timeoutMs: Number.isInteger(payload.timeoutMs) ? payload.timeoutMs : 30_000,
    variables: normalizeObject(payload.variables, {})
  };
}

function normalizeRunPayload(payload = {}) {
  return {
    ...normalizeScriptPayload(payload),
    ...normalizeFlowRunPayload(payload),
    flowDefinition: normalizeObject(payload.flowDefinition, undefined),
    flowId: normalizeOptionalString(payload.flowId)
  };
}

function normalizeAnalyzePayload(payload = {}) {
  return {
    fetcher: payload.fetcher ?? 'static',
    maxCandidates: Number.isInteger(payload.maxCandidates) ? payload.maxCandidates : 8,
    selector: normalizeOptionalString(payload.selector),
    targetUrl: normalizeString(payload.targetUrl, ''),
    timeoutMs: Number.isInteger(payload.timeoutMs) ? payload.timeoutMs : 30_000
  };
}

function normalizeFlowPayload(payload = {}) {
  return {
    acceptanceContract: payload.acceptanceContract,
    definition: normalizeObject(payload.definition, {}),
    description: normalizeOptionalString(payload.description),
    folderPath: normalizeOptionalString(payload.folderPath),
    inputVariables: Array.isArray(payload.inputVariables) ? payload.inputVariables : [],
    name: normalizeString(payload.name, '未命名流程'),
    status: ['active', 'paused', 'disabled', 'archived'].includes(payload.status) ? payload.status : 'draft',
    version: normalizeString(payload.version, 'v1.0.0'),
    defaultBrowserExecutor: payload.defaultBrowserExecutor === 'extension' ? 'extension' : undefined
  };
}

function normalizeSchedulePayload(payload = {}) {
  const task = payload.task ?? {};
  return {
    enabled: payload.enabled !== false,
    cronExpression: normalizeString(payload.cronExpression, '0 9 * * *'),
    name: normalizeString(payload.name, '未命名计划'),
    task: {
      ...task,
      ...normalizeRunPayload({ ...task, adaptive: task.adaptive ?? true, autoSave: task.autoSave ?? true }),
      flowIds: Array.isArray(task.flowIds) ? task.flowIds : []
    },
    timezone: normalizeString(payload.timezone, 'Asia/Shanghai')
  };
}

module.exports = {
  normalizeAnalyzePayload,
  normalizeConcurrency,
  normalizeDebugCommand,
  normalizeFlowPayload,
  normalizeFlowRunPayload,
  normalizeLimit,
  normalizeRunPayload,
  normalizeSchedulePayload,
  normalizeScriptPayload
};
