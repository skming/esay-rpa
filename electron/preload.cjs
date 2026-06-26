const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('rpaBridge', {
  openPicker: (payload) => ipcRenderer.invoke('picker:open', payload),
  closePicker: () => ipcRenderer.invoke('picker:close'),
  openFlow: () => ipcRenderer.invoke('flow:open'),
  saveFlow: (payload) => ipcRenderer.invoke('flow:save', payload),
  exportLogs: (payload) => ipcRenderer.invoke('logs:export', payload),
  startRun: (payload) => ipcRenderer.invoke('run:start', payload),
  stopRun: (runId) => ipcRenderer.invoke('run:stop', runId),
  provideInput: (runId, value) => ipcRenderer.invoke('input:provide', runId, value),
  debugRun: (runId, command) => ipcRenderer.invoke('run:debug', runId, command),
  listRuns: (options) => ipcRenderer.invoke('runs:list', options),
  listFlowRuns: (flowId, options) => ipcRenderer.invoke('flows:runs', flowId, options),
  generateScraplingScript: (payload) => ipcRenderer.invoke('script:generate', payload),
  analyzeSite: (payload) => ipcRenderer.invoke('site:analyze', payload),
  listFlows: () => ipcRenderer.invoke('flows:list'),
  createFlow: (payload) => ipcRenderer.invoke('flows:create', payload),
  updateFlow: (flowId, payload) => ipcRenderer.invoke('flows:update', flowId, payload),
  archiveFlow: (flowId) => ipcRenderer.invoke('flows:archive', flowId),
  setFlowStatus: (flowId, status) => ipcRenderer.invoke('flows:setStatus', flowId, status),
  deleteFlow: (flowId) => ipcRenderer.invoke('flows:delete', flowId),
  runFlow: (flowId, payload) => ipcRenderer.invoke('flows:run', flowId, payload),
  listTaskVariables: (taskId) => ipcRenderer.invoke('variables:list', taskId),
  listArtifacts: (taskId) => ipcRenderer.invoke('artifacts:list', taskId),
  readArtifact: (taskId, artifactId) => ipcRenderer.invoke('artifacts:read', taskId, artifactId),
  getQueueStats: () => ipcRenderer.invoke('queue:getStats'),
  getAiConfig: () => ipcRenderer.invoke('ai:getConfig'),
  setAiConfig: (payload) => ipcRenderer.invoke('ai:setConfig', payload),
  listAiModels: () => ipcRenderer.invoke('ai:listModels'),
  listSchedules: () => ipcRenderer.invoke('schedules:list'),
  createSchedule: (payload) => ipcRenderer.invoke('schedules:create', payload),
  updateSchedule: (scheduleId, payload) => ipcRenderer.invoke('schedules:update', scheduleId, payload),
  deleteSchedule: (scheduleId) => ipcRenderer.invoke('schedules:delete', scheduleId),
  triggerSchedule: (scheduleId) => ipcRenderer.invoke('schedules:trigger', scheduleId),
  getWindowId: () => ipcRenderer.invoke('window:getId'),
  getAppVersion: () => ipcRenderer.invoke('app:getVersion'),
  checkForUpdates: () => ipcRenderer.invoke('update:check'),
  downloadUpdate: () => ipcRenderer.invoke('update:download'),
  quitAndInstall: () => ipcRenderer.invoke('update:install'),
  onUpdateStatus: (callback) => {
    const listener = (_event, status) => callback(status);
    ipcRenderer.on('update:status', listener);
    return () => ipcRenderer.removeListener('update:status', listener);
  },
  openDataDir: (subDir) => ipcRenderer.invoke('app:openDataDir', subDir),
  showInFinder: (filePath) => ipcRenderer.invoke('app:showInFinder', filePath),
  getBackendStatus: () => ipcRenderer.invoke('backend:getStatus'),
  restartBackend: () => ipcRenderer.invoke('backend:restart'),
  minimizeWindow: () => ipcRenderer.invoke('window:minimize'),
  toggleMaximizeWindow: () => ipcRenderer.invoke('window:toggleMaximize'),
  closeWindow: () => ipcRenderer.invoke('window:close'),
  onPickerResult: (callback) => {
    const listener = (_event, selector) => callback(selector);
    ipcRenderer.on('picker:result', listener);
    return () => ipcRenderer.removeListener('picker:result', listener);
  },
  onPickerCancel: (callback) => {
    const listener = () => callback();
    ipcRenderer.on('picker:cancelled', listener);
    return () => ipcRenderer.removeListener('picker:cancelled', listener);
  },
  onRunEvent: (callback) => {
    const listener = (_event, runEvent) => callback(runEvent);
    ipcRenderer.on('run:event', listener);
    return () => ipcRenderer.removeListener('run:event', listener);
  },
  onBackendStatusChanged: (callback) => {
    const listener = (_event, status) => callback(status);
    ipcRenderer.on('backend:status-changed', listener);
    return () => ipcRenderer.removeListener('backend:status-changed', listener);
  },
});
