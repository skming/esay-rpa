const fs = require('node:fs/promises');
const path = require('node:path');
const { dialog } = require('electron');
const { IPC_CHANNELS } = require('./ipcChannels.cjs');

function success(data) {
  return { ok: true, data };
}

function failure(error) {
  return { ok: false, error: error instanceof Error ? error.message : String(error) };
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

// 主进程只负责生命周期装配；IPC 注册集中在这里，方便新增能力时同时检查
// channel、权限边界和错误信封是否一致。
function registerIpcHandlers({
  app,
  backendClient,
  backendSupervisor,
  getMainWindow,
  getSenderWindow,
  ipcMain,
  os,
  pickerService,
  runtimeController,
  setMainWindow,
  shell,
  updater,
  generateScraplingScript,
  broadcastBackendStatus
}) {
  const handle = (channel, listener) => ipcMain.handle(channel, listener);

  handle(IPC_CHANNELS.window.getId, (event) => success(getSenderWindow(event)?.id ?? null));
  handle(IPC_CHANNELS.window.minimize, (event) => {
    getSenderWindow(event)?.minimize();
    return success({ minimized: true });
  });
  handle(IPC_CHANNELS.window.toggleMaximize, (event) => {
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
  handle(IPC_CHANNELS.window.close, (event) => {
    getSenderWindow(event)?.close();
    return success({ closed: true });
  });

  handle(IPC_CHANNELS.app.getVersion, () =>
    success({
      version: app.getVersion(),
      platform: process.platform,
      arch: process.arch,
      hostname: os.hostname(),
      appDataDir: backendSupervisor.getAppDataDir()
    })
  );
  handle(IPC_CHANNELS.app.openDataDir, async (_event, subDir) => {
    try {
      const base = backendSupervisor.getAppDataDir();
      const target = typeof subDir === 'string' && subDir.trim() ? path.join(base, subDir.trim()) : base;
      const err = await shell.openPath(target);
      return err ? failure(err) : success({ opened: target });
    } catch (error) {
      return failure(error);
    }
  });
  handle(IPC_CHANNELS.app.showInFinder, async (_event, filePath) => {
    try {
      if (typeof filePath !== 'string' || !filePath.trim()) {
        return failure('路径不能为空');
      }
      const target = filePath.trim();
      const err = await shell.openPath(target);
      return err ? failure(err) : success({ opened: target });
    } catch (error) {
      return failure(error);
    }
  });

  // 扩展目前没有走 Chrome 应用商店分发，只能引导用户手动加载已解压的构建产物——
  // 优先找打包时随应用一起放进 resources 的那份，开发环境下退回到仓库里的 WXT 构建输出。
  const resolveExtensionUnpackedDir = async () => {
    const candidates = [
      path.join(process.resourcesPath ?? '', 'extension'),
      path.join(app.getAppPath(), 'extension', '.output', 'chrome-mv3')
    ];
    for (const candidate of candidates) {
      try {
        const manifestPath = path.join(candidate, 'manifest.json');
        await fs.access(manifestPath);
        return candidate;
      } catch {
        // 继续尝试下一个候选路径
      }
    }
    return null;
  };

  handle(IPC_CHANNELS.extension.getInstallInfo, async () => {
    try {
      const unpackedDir = await resolveExtensionUnpackedDir();
      return success({ unpackedDir, found: unpackedDir !== null });
    } catch (error) {
      return failure(error);
    }
  });
  handle(IPC_CHANNELS.extension.openFolder, async () => {
    try {
      const unpackedDir = await resolveExtensionUnpackedDir();
      if (unpackedDir === null) {
        return failure('未找到扩展构建产物，请先执行 pnpm build（extension 目录）');
      }
      const err = await shell.openPath(unpackedDir);
      return err ? failure(err) : success({ opened: unpackedDir });
    } catch (error) {
      return failure(error);
    }
  });
  handle(IPC_CHANNELS.extension.openChromeExtensionsPage, async () => {
    try {
      // 出于安全限制，多数系统上 shell.openExternal 无法直接打开 chrome:// 页面；
      // 失败时静默返回，前端用文字指引兜底，而不是抛出令人困惑的报错。
      await shell.openExternal('chrome://extensions/');
      return success({ opened: true });
    } catch (error) {
      return success({ opened: false, reason: error instanceof Error ? error.message : String(error) });
    }
  });

  handle(IPC_CHANNELS.update.check, () => { updater.checkForUpdates(); return success(null); });
  handle(IPC_CHANNELS.update.download, () => { updater.downloadUpdate(); return success(null); });
  handle(IPC_CHANNELS.update.install, () => { updater.quitAndInstall(); return success(null); });

  handle(IPC_CHANNELS.backend.getStatus, async () => {
    try {
      return success(await backendSupervisor.ensureStarted());
    } catch (error) {
      return failure(error);
    }
  });
  handle(IPC_CHANNELS.backend.restart, async () => {
    try {
      await backendSupervisor.stop();
      const status = await backendSupervisor.ensureStarted();
      broadcastBackendStatus(status);
      return success(status);
    } catch (error) {
      return failure(error);
    }
  });

  handle(IPC_CHANNELS.flow.open, async (event) => {
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
  handle(IPC_CHANNELS.flow.save, async (event, payload = {}) => {
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
  handle(IPC_CHANNELS.logs.export, async (event, payload = {}) => {
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

  handle(IPC_CHANNELS.picker.open, async (event, payload = {}) => {
    try {
      setMainWindow(getSenderWindow(event));
      return success(await pickerService.openPicker(getMainWindow(), payload));
    } catch (error) {
      return failure(error);
    }
  });
  handle(IPC_CHANNELS.picker.close, async () => success(pickerService.closePicker()));

  handle(IPC_CHANNELS.run.start, async (event, payload) => {
    try {
      return success(await runtimeController.startRun(getSenderWindow(event), payload));
    } catch (error) {
      return failure(error);
    }
  });
  handle(IPC_CHANNELS.run.stop, async (_event, runId) => {
    try {
      return success(await runtimeController.stopRun(runId));
    } catch (error) {
      return failure(error);
    }
  });
  handle(IPC_CHANNELS.input.provide, async (_event, runId, value) => {
    try {
      return success(await backendClient.provideInput(runId, value));
    } catch (error) {
      return failure(error);
    }
  });
  handle(IPC_CHANNELS.run.resume, async (_event, runId, resumeMode) => {
    try {
      return success(await backendClient.resumeHumanTakeover(runId, resumeMode));
    } catch (error) {
      return failure(error);
    }
  });
  handle(IPC_CHANNELS.run.debug, async (_event, runId, command) => {
    try {
      return success(await runtimeController.debugRun(runId, command));
    } catch (error) {
      return failure(error);
    }
  });

  // 纯后端转发类 IPC 统一用表驱动注册，减少重复 try/catch，也让通道清单更容易审计。
  const backendRoutes = [
    [IPC_CHANNELS.runs.list, (_event, options) => backendClient.listTasks(options)],
    [IPC_CHANNELS.flows.runs, (_event, flowId, options) => backendClient.listFlowRuns(flowId, options)],
    [IPC_CHANNELS.script.generate, (_event, payload) => generateScraplingScript(payload)],
    [IPC_CHANNELS.site.analyze, (_event, payload) => backendClient.analyzeSite(payload)],
    [IPC_CHANNELS.flows.list, () => backendClient.listFlows()],
    [IPC_CHANNELS.flows.create, (_event, payload) => backendClient.createFlow(payload)],
    [IPC_CHANNELS.flows.update, (_event, flowId, payload) => backendClient.updateFlow(flowId, payload)],
    [IPC_CHANNELS.flows.archive, (_event, flowId) => backendClient.archiveFlow(flowId)],
    [IPC_CHANNELS.flows.duplicate, (_event, flowId) => backendClient.duplicateFlow(flowId)],
    [IPC_CHANNELS.flows.move, (_event, flowId, folderPath) => backendClient.moveFlow(flowId, folderPath)],
    [IPC_CHANNELS.flows.setStatus, (_event, flowId, status) => backendClient.setFlowStatus(flowId, status)],
    [IPC_CHANNELS.flows.delete, (_event, flowId) => backendClient.deleteFlow(flowId)],
    [IPC_CHANNELS.flows.run, (_event, flowId, payload) => backendClient.runFlow(flowId, payload)],
    [IPC_CHANNELS.variables.list, (_event, taskId) => backendClient.getVariables(taskId)],
    [IPC_CHANNELS.artifacts.list, (_event, taskId) => backendClient.getArtifacts(taskId)],
    [IPC_CHANNELS.artifacts.read, (_event, taskId, artifactId) => backendClient.readArtifact(taskId, artifactId)],
    [IPC_CHANNELS.queue.getStats, () => backendClient.getQueueStats()],
    [IPC_CHANNELS.ai.getConfig, () => backendClient.getAiConfig()],
    [IPC_CHANNELS.ai.setConfig, (_event, payload) => backendClient.setAiConfig(payload)],
    [IPC_CHANNELS.ai.listModels, () => backendClient.listAiModels()],
    [IPC_CHANNELS.ai.addModel, (_event, payload) => backendClient.addAiModel(payload)],
    [IPC_CHANNELS.ai.updateModel, (_event, payload) => backendClient.updateAiModel(payload)],
    [IPC_CHANNELS.ai.deleteModel, (_event, modelId) => backendClient.deleteAiModel(modelId)],
    [IPC_CHANNELS.ai.testModel, (_event, payload) => backendClient.testAiModel(payload)],
    [IPC_CHANNELS.schedules.list, () => backendClient.listSchedules()],
    [IPC_CHANNELS.schedules.create, (_event, payload) => backendClient.createSchedule(payload)],
    [IPC_CHANNELS.schedules.update, (_event, scheduleId, payload) => backendClient.updateSchedule(scheduleId, payload)],
    [IPC_CHANNELS.schedules.delete, (_event, scheduleId) => backendClient.deleteSchedule(scheduleId)]
  ];

  for (const [channel, invoke] of backendRoutes) {
    handle(channel, async (...args) => {
      try {
        return success(await invoke(...args));
      } catch (error) {
        return failure(error);
      }
    });
  }

  handle(IPC_CHANNELS.schedules.trigger, async (event, scheduleId) => {
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
}

module.exports = {
  failure,
  registerIpcHandlers,
  success
};
