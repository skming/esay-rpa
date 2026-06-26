const { app, BrowserWindow, dialog, ipcMain, session, shell } = require('electron');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { BackendClient } = require('./backendClient.cjs');
const { BackendSupervisor, DEFAULT_APP_DATA_DIR } = require('./backendSupervisor.cjs');
const { createPickerService } = require('./pickerService.cjs');
const { createRuntimeController, generateScraplingScript } = require('./runtime.cjs');
const { initUpdater } = require('./updater.cjs');

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:19174/rpa-studio/';
const APP_ICON_PATH = app.isPackaged
  ? path.join(process.resourcesPath, 'icon.png')
  : path.join(__dirname, '../buildResources/icon.png');

if (!app.isPackaged && process.env.ELECTRON_REMOTE_DEBUGGING_PORT !== undefined) {
  app.commandLine.appendSwitch('remote-debugging-port', process.env.ELECTRON_REMOTE_DEBUGGING_PORT);
}

// Disable Chromium HTTP disk cache in dev so code changes are always picked up on reload.
if (!app.isPackaged) {
  app.commandLine.appendSwitch('disable-http-cache');
}

function success(data) {
  return { ok: true, data };
}

function failure(error) {
  return { ok: false, error: error instanceof Error ? error.message : String(error) };
}

function getSenderWindow(event) {
  return BrowserWindow.fromWebContents(event.sender);
}

function getDefaultFlowSnapshot() {
  return JSON.stringify(
    {
      version: 'v1.0.0',
      name: '未命名流程',
      updatedAt: new Date().toISOString(),
      runtime: 'electron',
      nodeCount: 0
    },
    null,
    2
  );
}

function createMainWindow() {
  const startupStartedAt = Number(process.env.RPA_STARTUP_STARTED_AT || Date.now());
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 720,
    icon: APP_ICON_PATH,
    show: false,
    titleBarStyle: 'hidden',
    trafficLightPosition: { x: 12, y: 10 },
    backgroundColor: '#f8fafc',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });

  // Defer show until the renderer has painted its first frame.
  // This replaces the immediate white window with a clean first-paint reveal.
  win.once('ready-to-show', () => {
    win.show();
    if (process.env.RPA_STARTUP_PROBE === '1') {
      const readyAt = Date.now();
      console.log(
        JSON.stringify({
          event: 'RPA_STARTUP_READY',
          startupMs: readyAt - startupStartedAt,
          readyAt,
          packaged: app.isPackaged,
          platform: process.platform,
          arch: process.arch
        })
      );
    }
  });

  // Open all target="_blank" links in the system default browser, not in a new Electron window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://') || url.startsWith('https://')) {
      void shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  if (!app.isPackaged) {
    void win.loadURL(DEV_SERVER_URL);
    return win;
  }

  void win.loadFile(path.join(__dirname, '../dist/index.html'));
  return win;
}

app.whenReady().then(() => {
  // In production, prevent Electron from caching index.html so the app always loads the
  // latest entry point after an update.  Vite emits content-hashed filenames for every
  // other asset, so their URLs change on rebuild — stale caches can never be misdelivered
  // and blocking their cache would force V8 to re-parse the full JS bundle on every cold
  // start instead of deserialising the much-faster bytecode cache.
  if (app.isPackaged) {
    session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
      const isEntry = details.url.endsWith('/index.html')
        || details.url.endsWith('/')
        || details.url === `file://${path.join(__dirname, '../dist/index.html')}`;
      if (!isEntry) {
        callback({});
        return;
      }
      callback({
        responseHeaders: {
          ...details.responseHeaders,
          'Cache-Control': ['no-store']
        }
      });
    });
  }

  const backendClient = new BackendClient();
  const backendSupervisor = new BackendSupervisor({
    backendClient,
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    appDataDir: DEFAULT_APP_DATA_DIR
  });
  let mainWindow = createMainWindow();

  const pickerService = createPickerService({
    onResult: (result) => {
      if (mainWindow !== null && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('picker:result', result);
      }
    },
    onCancel: () => {
      if (mainWindow !== null && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('picker:cancelled');
      }
    }
  });
  const runtimeController = createRuntimeController({ backendClient });

  const broadcastBackendStatus = (status) => {
    for (const win of BrowserWindow.getAllWindows()) {
      if (!win.isDestroyed()) {
        win.webContents.send('backend:status-changed', status);
      }
    }
  };

  setImmediate(() => {
    void backendSupervisor.ensureStarted().then(broadcastBackendStatus);
  });

  ipcMain.handle('window:getId', (event) => success(getSenderWindow(event)?.id ?? null));
  ipcMain.handle('window:minimize', (event) => {
    getSenderWindow(event)?.minimize();
    return success({ minimized: true });
  });
  ipcMain.handle('window:toggleMaximize', (event) => {
    const win = getSenderWindow(event);
    if (win === null) {
      return failure('Window is unavailable.');
    }
    if (win.isMaximized()) {
      win.unmaximize();
      return success({ maximized: false });
    }
    win.maximize();
    return success({ maximized: true });
  });
  ipcMain.handle('window:close', (event) => {
    getSenderWindow(event)?.close();
    return success({ closed: true });
  });
  ipcMain.handle('app:getVersion', () =>
    success({
      version: app.getVersion(),
      platform: process.platform,
      arch: process.arch,
      hostname: os.hostname(),
      appDataDir: backendSupervisor.getAppDataDir()
    })
  );

  // ── Auto-updater ────────────────────────────────────────────────────
  const updater = initUpdater();
  ipcMain.handle('update:check', () => { updater.checkForUpdates(); return success(null); });
  ipcMain.handle('update:download', () => { updater.downloadUpdate(); return success(null); });
  ipcMain.handle('update:install', () => { updater.quitAndInstall(); return success(null); });

  ipcMain.handle('app:openDataDir', async (_event, subDir) => {
    try {
      const base = backendSupervisor.getAppDataDir();
      const target = typeof subDir === 'string' && subDir.trim()
        ? require('node:path').join(base, subDir.trim())
        : base;
      const err = await shell.openPath(target);
      return err ? failure(err) : success({ opened: target });
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('app:showInFinder', async (_event, filePath) => {
    try {
      if (typeof filePath !== 'string' || !filePath.trim()) {
        return failure('路径不能为空');
      }
      shell.showItemInFolder(filePath.trim());
      return success({ opened: filePath.trim() });
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('backend:getStatus', async () => {
    try {
      return success(await backendSupervisor.ensureStarted());
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('backend:restart', async () => {
    try {
      await backendSupervisor.stop();
      const status = await backendSupervisor.ensureStarted();
      broadcastBackendStatus(status);
      return success(status);
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('flow:open', async (event) => {
    try {
      const win = getSenderWindow(event);
      const result = await dialog.showOpenDialog(win ?? undefined, {
        title: '打开 RPA 流程',
        properties: ['openFile'],
        filters: [
          { name: 'RPA Flow', extensions: ['json', 'yaml', 'yml'] },
          { name: 'All Files', extensions: ['*'] }
        ]
      });
      if (result.canceled || result.filePaths.length === 0) {
        return success({ canceled: true });
      }
      const filePath = result.filePaths[0];
      const content = await fs.readFile(filePath, 'utf8');
      return success({ canceled: false, path: filePath, name: path.basename(filePath), content });
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('flow:save', async (event, payload = {}) => {
    try {
      const win = getSenderWindow(event);
      const content = typeof payload.content === 'string' ? payload.content : getDefaultFlowSnapshot();
      const suggestedName = typeof payload.suggestedName === 'string' ? payload.suggestedName : '未命名流程.rpa.json';
      const result = await dialog.showSaveDialog(win ?? undefined, {
        title: '保存 RPA 流程',
        defaultPath: suggestedName,
        filters: [{ name: 'RPA Flow', extensions: ['json'] }]
      });
      if (result.canceled || result.filePath === undefined) {
        return success({ canceled: true });
      }
      await fs.writeFile(result.filePath, content, 'utf8');
      return success({ canceled: false, path: result.filePath, name: path.basename(result.filePath) });
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('logs:export', async (event, payload = {}) => {
    try {
      const win = getSenderWindow(event);
      const content = typeof payload.content === 'string' ? payload.content : '';
      const defaultName = typeof payload.filename === 'string' && payload.filename.trim() ? payload.filename : '运行日志.log';
      const result = await dialog.showSaveDialog(win ?? undefined, {
        title: '导出运行日志',
        defaultPath: defaultName,
        filters: [{ name: 'Log File', extensions: ['log', 'txt'] }]
      });
      if (result.canceled || result.filePath === undefined) {
        return success({ canceled: true });
      }
      await fs.writeFile(result.filePath, content, 'utf8');
      return success({ canceled: false, path: result.filePath, name: path.basename(result.filePath) });
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('picker:open', async (event, payload = {}) => {
    try {
      mainWindow = getSenderWindow(event);
      return success(await pickerService.openPicker(mainWindow, payload));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('picker:close', async () => success(pickerService.closePicker()));
  ipcMain.handle('run:start', async (event, payload) => {
    try {
      return success(await runtimeController.startRun(getSenderWindow(event), payload));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('run:stop', async (_event, runId) => {
    try {
      return success(await runtimeController.stopRun(runId));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('input:provide', async (_event, runId, value) => {
    try {
      return success(await backendClient.provideInput(runId, value));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('run:debug', async (_event, runId, command) => {
    try {
      return success(await runtimeController.debugRun(runId, command));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('runs:list', async (_event, options) => {
    try {
      return success(await backendClient.listTasks(options));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('flows:runs', async (_event, flowId, options) => {
    try {
      return success(await backendClient.listFlowRuns(flowId, options));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('script:generate', async (_event, payload) => {
    try {
      return success(await generateScraplingScript(payload));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('site:analyze', async (_event, payload) => {
    try {
      return success(await backendClient.analyzeSite(payload));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('flows:list', async () => {
    try {
      return success(await backendClient.listFlows());
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('flows:create', async (_event, payload) => {
    try {
      return success(await backendClient.createFlow(payload));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('flows:update', async (_event, flowId, payload) => {
    try {
      return success(await backendClient.updateFlow(flowId, payload));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('flows:archive', async (_event, flowId) => {
    try {
      return success(await backendClient.archiveFlow(flowId));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('flows:setStatus', async (_event, flowId, status) => {
    try {
      return success(await backendClient.setFlowStatus(flowId, status));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('flows:delete', async (_event, flowId) => {
    try {
      return success(await backendClient.deleteFlow(flowId));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('flows:run', async (_event, flowId, payload) => {
    try {
      return success(await backendClient.runFlow(flowId, payload));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('variables:list', async (_event, taskId) => {
    try {
      return success(await backendClient.getVariables(taskId));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('artifacts:list', async (_event, taskId) => {
    try {
      return success(await backendClient.getArtifacts(taskId));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('artifacts:read', async (_event, taskId, artifactId) => {
    try {
      return success(await backendClient.readArtifact(taskId, artifactId));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('queue:getStats', async () => {
    try {
      return success(await backendClient.getQueueStats());
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('ai:getConfig', async () => {
    try {
      return success(await backendClient.getAiConfig());
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('ai:setConfig', async (_event, payload) => {
    try {
      return success(await backendClient.setAiConfig(payload));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('ai:listModels', async () => {
    try {
      return success(await backendClient.listAiModels());
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('schedules:list', async () => {
    try {
      return success(await backendClient.listSchedules());
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('schedules:create', async (_event, payload) => {
    try {
      return success(await backendClient.createSchedule(payload));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('schedules:update', async (_event, scheduleId, payload) => {
    try {
      return success(await backendClient.updateSchedule(scheduleId, payload));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('schedules:delete', async (_event, scheduleId) => {
    try {
      return success(await backendClient.deleteSchedule(scheduleId));
    } catch (error) {
      return failure(error);
    }
  });
  ipcMain.handle('schedules:trigger', async (event, scheduleId) => {
    try {
      const schedule = await backendClient.triggerSchedule(scheduleId);
      let run = null;
      if (typeof schedule.lastTaskId === 'string' && schedule.lastTaskId.length > 0) {
        const task = await backendClient.getTask(schedule.lastTaskId);
        run = runtimeController.watchBackendRun(getSenderWindow(event), task, schedule.task ?? {});
      }
      return success({ schedule, run });
    } catch (error) {
      return failure(error);
    }
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createMainWindow();
    }
  });

  app.on('before-quit', (event) => {
    if (app.__rpaBackendStopping === true) {
      return;
    }
    app.__rpaBackendStopping = true;
    event.preventDefault();
    void backendSupervisor.stop().finally(() => {
      app.exit(0);
    });
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
