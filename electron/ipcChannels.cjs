// 所有主进程/预加载脚本共享的 IPC channel 必须集中声明，避免字符串散落后出现
// preload、main、renderer 三端命名不一致却只能在运行时才暴露的问题。
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

module.exports = {
  IPC_CHANNELS
};
