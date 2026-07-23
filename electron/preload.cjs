const { contextBridge, ipcRenderer } = require('electron');

// sandbox: true 下 Electron 的 preload 加载器不支持 require() 本地相对路径文件，
// 只能内置模块——之前拆成多文件互相 require 会导致 window.rpaBridge 静默未定义，
// 所有桌面端 IPC 调用无声失效。这里全部内联进单文件规避。
const IPC_CHANNELS = Object.freeze({
  app: Object.freeze({
    getVersion: 'app:getVersion',
    openDataDir: 'app:openDataDir',
    showInFinder: 'app:showInFinder'
  }),
  update: Object.freeze({
    check: 'update:check',
    download: 'update:download',
    install: 'update:install',
    status: 'update:status'
  }),
  backend: Object.freeze({
    getStatus: 'backend:getStatus',
    restart: 'backend:restart',
    statusChanged: 'backend:status-changed'
  }),
  window: Object.freeze({
    getId: 'window:getId',
    minimize: 'window:minimize',
    toggleMaximize: 'window:toggleMaximize',
    close: 'window:close'
  }),
  picker: Object.freeze({
    open: 'picker:open',
    close: 'picker:close',
    cancel: 'picker:cancel',
    capture: 'picker:capture',
    result: 'picker:result',
    cancelled: 'picker:cancelled'
  }),
  flow: Object.freeze({
    open: 'flow:open',
    save: 'flow:save'
  }),
  logs: Object.freeze({
    export: 'logs:export'
  }),
  run: Object.freeze({
    start: 'run:start',
    stop: 'run:stop',
    resume: 'run:resume',
    debug: 'run:debug',
    event: 'run:event'
  }),
  input: Object.freeze({
    provide: 'input:provide'
  }),
  runs: Object.freeze({
    list: 'runs:list'
  }),
  script: Object.freeze({
    generate: 'script:generate'
  }),
  site: Object.freeze({
    analyze: 'site:analyze'
  }),
  flows: Object.freeze({
    list: 'flows:list',
    create: 'flows:create',
    update: 'flows:update',
    archive: 'flows:archive',
    duplicate: 'flows:duplicate',
    move: 'flows:move',
    setStatus: 'flows:setStatus',
    delete: 'flows:delete',
    run: 'flows:run',
    runs: 'flows:runs'
  }),
  variables: Object.freeze({
    list: 'variables:list'
  }),
  artifacts: Object.freeze({
    list: 'artifacts:list',
    read: 'artifacts:read'
  }),
  queue: Object.freeze({
    getStats: 'queue:getStats'
  }),
  ai: Object.freeze({
    getConfig: 'ai:getConfig',
    setConfig: 'ai:setConfig',
    listModels: 'ai:listModels',
    addModel: 'ai:addModel',
    updateModel: 'ai:updateModel',
    deleteModel: 'ai:deleteModel',
    testModel: 'ai:testModel'
  }),
  schedules: Object.freeze({
    list: 'schedules:list',
    create: 'schedules:create',
    update: 'schedules:update',
    delete: 'schedules:delete',
    trigger: 'schedules:trigger'
  }),
  extension: Object.freeze({
    getInstallInfo: 'extension:getInstallInfo',
    openFolder: 'extension:openFolder',
    openChromeExtensionsPage: 'extension:openChromeExtensionsPage'
  })
});

function createRpaBridge(ipcRenderer) {
  const invoke = (channel, ...args) => ipcRenderer.invoke(channel, ...args);
  // 统一封装订阅/取消订阅，避免 renderer 页面反复挂载后遗留 IPC listener。
  const subscribe = (channel, callback, mapPayload = (_event, payload) => payload) => {
    const listener = (event, payload) => callback(mapPayload(event, payload));
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  };

  return {
    openPicker: (payload) => invoke(IPC_CHANNELS.picker.open, payload),
    closePicker: () => invoke(IPC_CHANNELS.picker.close),
    openFlow: () => invoke(IPC_CHANNELS.flow.open),
    saveFlow: (payload) => invoke(IPC_CHANNELS.flow.save, payload),
    exportLogs: (payload) => invoke(IPC_CHANNELS.logs.export, payload),
    startRun: (payload) => invoke(IPC_CHANNELS.run.start, payload),
    stopRun: (runId) => invoke(IPC_CHANNELS.run.stop, runId),
    provideInput: (runId, value) => invoke(IPC_CHANNELS.input.provide, runId, value),
    resumeHumanTakeover: (runId, resumeMode) => invoke(IPC_CHANNELS.run.resume, runId, resumeMode),
    debugRun: (runId, command) => invoke(IPC_CHANNELS.run.debug, runId, command),
    listRuns: (options) => invoke(IPC_CHANNELS.runs.list, options),
    listFlowRuns: (flowId, options) => invoke(IPC_CHANNELS.flows.runs, flowId, options),
    generateScraplingScript: (payload) => invoke(IPC_CHANNELS.script.generate, payload),
    analyzeSite: (payload) => invoke(IPC_CHANNELS.site.analyze, payload),
    listFlows: () => invoke(IPC_CHANNELS.flows.list),
    createFlow: (payload) => invoke(IPC_CHANNELS.flows.create, payload),
    updateFlow: (flowId, payload) => invoke(IPC_CHANNELS.flows.update, flowId, payload),
    archiveFlow: (flowId) => invoke(IPC_CHANNELS.flows.archive, flowId),
    duplicateFlow: (flowId) => invoke(IPC_CHANNELS.flows.duplicate, flowId),
    moveFlow: (flowId, folderPath) => invoke(IPC_CHANNELS.flows.move, flowId, folderPath),
    setFlowStatus: (flowId, status) => invoke(IPC_CHANNELS.flows.setStatus, flowId, status),
    deleteFlow: (flowId) => invoke(IPC_CHANNELS.flows.delete, flowId),
    runFlow: (flowId, payload) => invoke(IPC_CHANNELS.flows.run, flowId, payload),
    listTaskVariables: (taskId) => invoke(IPC_CHANNELS.variables.list, taskId),
    listArtifacts: (taskId) => invoke(IPC_CHANNELS.artifacts.list, taskId),
    readArtifact: (taskId, artifactId) => invoke(IPC_CHANNELS.artifacts.read, taskId, artifactId),
    getQueueStats: () => invoke(IPC_CHANNELS.queue.getStats),
    getAiConfig: () => invoke(IPC_CHANNELS.ai.getConfig),
    setAiConfig: (payload) => invoke(IPC_CHANNELS.ai.setConfig, payload),
    listAiModels: () => invoke(IPC_CHANNELS.ai.listModels),
    addAiModel: (payload) => invoke(IPC_CHANNELS.ai.addModel, payload),
    updateAiModel: (payload) => invoke(IPC_CHANNELS.ai.updateModel, payload),
    deleteAiModel: (modelId) => invoke(IPC_CHANNELS.ai.deleteModel, modelId),
    testAiModel: (payload) => invoke(IPC_CHANNELS.ai.testModel, payload),
    listSchedules: () => invoke(IPC_CHANNELS.schedules.list),
    createSchedule: (payload) => invoke(IPC_CHANNELS.schedules.create, payload),
    updateSchedule: (scheduleId, payload) => invoke(IPC_CHANNELS.schedules.update, scheduleId, payload),
    deleteSchedule: (scheduleId) => invoke(IPC_CHANNELS.schedules.delete, scheduleId),
    triggerSchedule: (scheduleId) => invoke(IPC_CHANNELS.schedules.trigger, scheduleId),
    getWindowId: () => invoke(IPC_CHANNELS.window.getId),
    getAppVersion: () => invoke(IPC_CHANNELS.app.getVersion),
    checkForUpdates: () => invoke(IPC_CHANNELS.update.check),
    downloadUpdate: () => invoke(IPC_CHANNELS.update.download),
    quitAndInstall: () => invoke(IPC_CHANNELS.update.install),
    onUpdateStatus: (callback) => subscribe(IPC_CHANNELS.update.status, callback),
    openDataDir: (subDir) => invoke(IPC_CHANNELS.app.openDataDir, subDir),
    showInFinder: (filePath) => invoke(IPC_CHANNELS.app.showInFinder, filePath),
    getBackendStatus: () => invoke(IPC_CHANNELS.backend.getStatus),
    restartBackend: () => invoke(IPC_CHANNELS.backend.restart),
    getExtensionInstallInfo: () => invoke(IPC_CHANNELS.extension.getInstallInfo),
    openExtensionFolder: () => invoke(IPC_CHANNELS.extension.openFolder),
    openChromeExtensionsPage: () => invoke(IPC_CHANNELS.extension.openChromeExtensionsPage),
    minimizeWindow: () => invoke(IPC_CHANNELS.window.minimize),
    toggleMaximizeWindow: () => invoke(IPC_CHANNELS.window.toggleMaximize),
    closeWindow: () => invoke(IPC_CHANNELS.window.close),
    onPickerResult: (callback) => subscribe(IPC_CHANNELS.picker.result, callback),
    onPickerCancel: (callback) => {
      const listener = () => callback();
      ipcRenderer.on(IPC_CHANNELS.picker.cancelled, listener);
      return () => ipcRenderer.removeListener(IPC_CHANNELS.picker.cancelled, listener);
    },
    onRunEvent: (callback) => subscribe(IPC_CHANNELS.run.event, callback),
    onBackendStatusChanged: (callback) => subscribe(IPC_CHANNELS.backend.statusChanged, callback)
  };
}

contextBridge.exposeInMainWorld('rpaBridge', createRpaBridge(ipcRenderer));
