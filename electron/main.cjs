const { app, BrowserWindow, ipcMain, screen, session, shell, Tray, Menu, nativeImage } = require('electron');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { BackendClient } = require('./backendClient.cjs');
const { BackendSupervisor, DEFAULT_APP_DATA_DIR } = require('./backendSupervisor.cjs');
const { IPC_CHANNELS } = require('./ipcChannels.cjs');
const { registerIpcHandlers } = require('./ipcHandlers.cjs');
const { createPickerService } = require('./pickerService.cjs');
const { createRuntimeController, generateScraplingScript } = require('./runtime.cjs');
const { initUpdater } = require('./updater.cjs');

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:19174/rpa-studio/';
// 托盘「在浏览器中打开」的目标：与主窗口加载的是同一个界面。开发态直连 dev server；
// 打包态没有本地 Web 服务，退回 file:// 入口（纯浏览器缺少 Electron 桥，桌面专属能力会降级）。
const WEB_URL = app.isPackaged
  ? pathToFileURL(path.join(__dirname, '../dist/index.html')).toString()
  : DEV_SERVER_URL;
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

function getSenderWindow(event) {
  return BrowserWindow.fromWebContents(event.sender);
}

// Held at module scope so it isn't garbage-collected once app.whenReady().then() returns —
// a Tray instance with no external reference can silently disappear even with listeners attached.
let tray = null;

function createTray(showMainWindow, openWeb) {
  // Plain silhouette + setTemplateImage(true): macOS recolors it automatically for
  // light/dark menu bars, matching the flat, monochrome look of the system's own icons.
  const trayIcon = nativeImage.createFromPath(path.join(__dirname, 'trayIconTemplate.png'));
  trayIcon.setTemplateImage(true);
  const instance = new Tray(trayIcon);
  instance.setToolTip('Easy RPA');
  const contextMenu = Menu.buildFromTemplate([
    { label: '打开主界面', click: showMainWindow },
    { label: '在浏览器中打开', click: openWeb },
    { type: 'separator' },
    {
      label: '退出',
      click: () => {
        app.__rpaQuitting = true;
        app.quit();
      }
    }
  ]);
  instance.setContextMenu(contextMenu);
  if (process.platform !== 'darwin') {
    instance.on('click', () => instance.popUpContextMenu(contextMenu));
  }
  return instance;
}

function createMainWindow() {
  const startupStartedAt = Number(process.env.RPA_STARTUP_STARTED_AT || Date.now());
  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;
  const winWidth = Math.min(1440, Math.round(sw * 0.85));
  const winHeight = Math.min(900, Math.round(sh * 0.85));
  const win = new BrowserWindow({
    width: winWidth,
    height: winHeight,
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

  // Hidden-titlebar windows sometimes don't reclaim input focus from DevTools on close,
  // leaving the main window visible but unresponsive to clicks until manually refocused.
  win.webContents.on('devtools-closed', () => {
    win.focus();
  });

  // Closing the window only hides it to the tray so background jobs (scheduled flows, etc.)
  // keep running. Real quit only happens via the tray menu or Cmd+Q, both of which set
  // __rpaQuitting first (see the before-quit handler below).
  win.on('close', (event) => {
    if (app.__rpaQuitting === true) {
      return;
    }
    event.preventDefault();
    win.hide();
  });

  if (!app.isPackaged) {
    void win.loadURL(DEV_SERVER_URL);
    return win;
  }

  void win.loadFile(path.join(__dirname, '../dist/index.html'));
  return win;
}

app.whenReady().then(() => {
  // 只禁 index.html 的缓存：不禁则更新后仍加载旧入口。其余资源带 content hash，
  // URL 必变不会串版本，且禁掉会让 V8 每次冷启重新解析整包而非复用 bytecode cache。
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

  const broadcastBackendStatus = (status) => {
    for (const win of BrowserWindow.getAllWindows()) {
      if (!win.isDestroyed()) {
        win.webContents.send(IPC_CHANNELS.backend.statusChanged, status);
      }
    }
  };

  const backendSupervisor = new BackendSupervisor({
    backendClient,
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    appDataDir: DEFAULT_APP_DATA_DIR,
    // 浏览器组件下载进度等中间状态也实时推给渲染进程，而不是只在启动完成后推一次。
    onStatusChange: broadcastBackendStatus
  });
  let mainWindow = createMainWindow();

  const showMainWindow = () => {
    if (mainWindow === null || mainWindow.isDestroyed()) {
      mainWindow = createMainWindow();
      return;
    }
    if (mainWindow.isMinimized()) {
      mainWindow.restore();
    }
    mainWindow.show();
    mainWindow.focus();
  };

  // 既然要去浏览器里用，桌面窗口就顺手收起到托盘让路（长驻后台不退出）。
  const openWeb = () => {
    void shell.openExternal(WEB_URL);
    if (mainWindow !== null && !mainWindow.isDestroyed()) {
      mainWindow.hide();
    }
  };

  tray = createTray(showMainWindow, openWeb);

  const pickerService = createPickerService({
    onResult: (result) => {
      if (mainWindow !== null && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send(IPC_CHANNELS.picker.result, result);
      }
    },
    onCancel: () => {
      if (mainWindow !== null && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send(IPC_CHANNELS.picker.cancelled);
      }
    }
  });
  const runtimeController = createRuntimeController({ backendClient });

  setImmediate(() => {
    void backendSupervisor.ensureStarted().then(broadcastBackendStatus);
  });

  const updater = initUpdater();
  registerIpcHandlers({
    app,
    backendClient,
    backendSupervisor,
    broadcastBackendStatus,
    generateScraplingScript,
    getMainWindow: () => mainWindow,
    getSenderWindow,
    ipcMain,
    os,
    pickerService,
    runtimeController,
    setMainWindow: (win) => {
      mainWindow = win;
    },
    shell,
    updater
  });

  app.on('activate', showMainWindow);

  app.on('before-quit', (event) => {
    app.__rpaQuitting = true;
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

// No window-all-closed handler: the window's own 'close' listener always hides rather than
// destroys it, so this event no longer fires during normal use — the app now only quits via
// the tray menu or Cmd+Q.
