const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');

const { BackendClient, DEFAULT_BACKEND_URL } = require('./backendClient.cjs');
const { createFileLogger, captureProcessOutput, redactLogText } = require('./logger.cjs');

const DEFAULT_HOST = '127.0.0.1';
const DEFAULT_PORT = 8765;
// 先快速探测，再退避以覆盖冷启动；重试间隔不包含健康请求的耗时。
const HEALTH_RETRY_DELAYS_MS = [
  ...Array(5).fill(150),
  ...Array(5).fill(300),
  ...Array(10).fill(600),
  ...Array(30).fill(1000),
];

/** 应用本地数据目录，所有持久化数据统一存放在此 */
const DEFAULT_APP_DATA_DIR = path.join(os.homedir(), '.easy-rpa');

class BackendSupervisor {
  constructor({
    backendClient = new BackendClient(),
    host = DEFAULT_HOST,
    port = DEFAULT_PORT,
    projectRoot = path.join(__dirname, '..'),
    isPackaged = false,
    resourcesPath = process.resourcesPath,
    appDataDir = DEFAULT_APP_DATA_DIR,
    // 每次状态变化（含浏览器下载进度）都会同步回调，供调用方实时推送给渲染进程。
    onStatusChange = null
  } = {}) {
    this.backendClient = backendClient;
    this.onStatusChange = onStatusChange;
    this.host = host;
    this.port = port;
    this.projectRoot = projectRoot;
    this.isPackaged = isPackaged;
    this.resourcesPath = resourcesPath;
    this.appDataDir = appDataDir;
    this.backendDir = this.#resolveBackendDir();

    // 本地数据目录按职责分层：数据库、运行态、AI、工作区、日志、缓存。
    this.dbDir = path.join(appDataDir, 'db');
    this.runtimeDir = path.join(appDataDir, 'runtime');
    this.browserRuntimeDir = path.join(this.runtimeDir, 'browser');
    this.scraplingRuntimeDir = path.join(this.runtimeDir, 'scrapling');
    // Playwright 浏览器内核不打进安装包（体积太大），首次启动时按需下载到这里，
    // 随 appDataDir 持久化，升级 App 不会丢，也不占用系统级 ms-playwright 缓存。
    this.playwrightBrowsersDir = path.join(this.runtimeDir, 'playwright-browsers');
    this.aiDir = path.join(appDataDir, 'ai');
    this.aiChatsDir = path.join(this.aiDir, 'chats');
    this.workspaceRoot = path.join(appDataDir, 'workspace');
    this.runRoot = path.join(this.workspaceRoot, 'runs');
    this.logDir = path.join(appDataDir, 'logs');
    this.logger = createFileLogger(path.join(this.logDir, 'backend-process.log'));
    this.cacheDir = path.join(appDataDir, 'cache');

    this.child = null;
    this.installChild = null;
    this.startPromise = null;
    this.startController = null;
    this.stopPromise = null;
    this.status = {
      error: null,
      installProgress: null,
      installStep: null,
      installStepLabel: null,
      installStepTotal: null,
      managed: false,
      pid: null,
      source: 'unknown',
      status: 'idle',
      url: DEFAULT_BACKEND_URL
    };
  }

  getStatus() {
    return { ...this.status };
  }

  #setStatus(patch) {
    this.status = { ...this.status, ...patch };
    this.onStatusChange?.(this.getStatus());
    return this.status;
  }

  getAppDataDir() {
    return this.appDataDir;
  }

  async ensureStarted() {
    if (this.stopPromise !== null) await this.stopPromise;
    if (this.startPromise === null) {
      const controller = new AbortController();
      this.startController = controller;
      this.startPromise = Promise.resolve().then(() => this.#ensureStartedInternal(controller.signal)).catch((error) => {
        if (!controller.signal.aborted) {
          this.#setStatus({
            error: redactLogText(error.message),
            managed: this.child !== null || this.installChild !== null,
            pid: this.child?.pid ?? this.installChild?.pid ?? null,
            status: 'error'
          });
        }
        return this.getStatus();
      }).finally(() => {
        this.startPromise = null;
        this.startController = null;
      });
    }
    const signal = this.startController.signal;
    const result = await this.startPromise;
    if (signal.aborted) return this.stopPromise ?? this.getStatus();
    return result;
  }

  async stop() {
    if (this.stopPromise !== null) return this.stopPromise;
    const starting = this.startPromise;
    this.stopPromise = Promise.resolve().then(async () => {
      const results = await Promise.allSettled([
        this.#stopManagedChild('installChild'), this.#stopManagedChild('child')
      ]);
      if (starting !== null) await starting;
      const failure = results.find((result) => result.status === 'rejected');
      if (failure) {
        this.#setStatus({
          error: redactLogText(failure.reason.message),
          managed: true,
          pid: this.child?.pid ?? this.installChild?.pid ?? null,
          status: 'error'
        });
        throw failure.reason;
      }
      this.#setStatus({
        error: null,
        installProgress: null,
        installStep: null,
        installStepLabel: null,
        installStepTotal: null,
        managed: false,
        pid: null,
        status: 'stopped'
      });
      return this.getStatus();
    }).finally(() => {
      this.stopPromise = null;
    });
    this.startController?.abort();
    return this.stopPromise;
  }

  async #stopManagedChild(field) {
    const child = this[field];
    if (child === null) return;
    this[field] = null;
    try {
      await terminateChild(child);
    } catch (error) {
      this[field] = child;
      throw error;
    }
  }

  async #ensureStartedInternal(signal) {
    if (signal.aborted) return this.getStatus();
    this.#setStatus({
      error: null,
      status: 'checking'
    });

    const ownedAtCheck = this.child;
    const healthy = await this.#isBackendHealthy();
    if (signal.aborted) return this.getStatus();
    if (healthy && (ownedAtCheck === null || (
      this.child === ownedAtCheck && ownedAtCheck.exitCode == null && ownedAtCheck.signalCode == null
    ))) {
      this.#setStatus({
        error: null,
        managed: ownedAtCheck !== null,
        pid: ownedAtCheck?.pid ?? null,
        source: ownedAtCheck === null ? 'external' : 'managed',
        status: 'ready'
      });
      return this.getStatus();
    }

    await this.#stopManagedChild('child');
    if (signal.aborted) return this.getStatus();

    const pythonExecutable = this.#resolvePythonExecutable();
    if (pythonExecutable === null) {
      this.#setStatus({
        error: this.isPackaged
          ? '未找到打包后的 Python 运行时，请重新执行 Electron 打包并确认 backend 资源已包含'
          : '未找到 backend/.venv Python 运行时，请先在 backend 目录执行 uv sync',
        managed: false,
        pid: null,
        source: 'missing',
        status: 'error'
      });
      return this.getStatus();
    }

    this.#ensureRuntimeDirectories();

    const pythonPathEntries = this.#resolvePythonPathEntries();

    const installError = await this.#ensurePlaywrightBrowsers(pythonExecutable, pythonPathEntries, signal);
    if (signal.aborted) return this.getStatus();
    if (installError !== null) {
      this.#setStatus({
        error: installError,
        installProgress: null,
        installStep: null,
        installStepLabel: null,
        installStepTotal: null,
        managed: false,
        pid: null,
        source: 'managed',
        status: 'error'
      });
      return this.getStatus();
    }

    this.#setStatus({
      error: null,
      installProgress: null,
      installStep: null,
      installStepLabel: null,
      installStepTotal: null,
      managed: true,
      pid: null,
      source: 'managed',
      status: 'starting'
    });
    if (signal.aborted) return this.getStatus();
    this.logger.write('INFO', 'Starting backend');

    const child = spawn(
      pythonExecutable,
      ['-m', 'uvicorn', 'app.main:app', '--host', this.host, '--port', String(this.port), '--log-level', 'info'],
      {
        cwd: this.backendDir,
        detached: process.platform !== 'win32',
        windowsHide: true,
        env: {
          ...process.env,
          RPA_APP_DATA_DIR: this.appDataDir,
          RPA_WORKSPACE_ROOT: this.workspaceRoot,
          RPA_LOG_DIR: this.logDir,
          RPA_CACHE_DIR: this.cacheDir,
          PLAYWRIGHT_BROWSERS_PATH: this.playwrightBrowsersDir,
          PYTHONPATH: [
            ...pythonPathEntries,
            process.env.PYTHONPATH
          ].filter(Boolean).join(path.delimiter),
          PYTHONUNBUFFERED: '1'
        },
        stdio: ['ignore', 'pipe', 'pipe']
      }
    );

    this.child = child;
    let outputBuffer = '';
    let processExited = false;

    const appendOutput = (chunk) => {
      outputBuffer = `${outputBuffer}${String(chunk)}`.slice(-16000);
    };

    captureProcessOutput(child, this.logger, 'backend', appendOutput);

    child.on('error', (error) => {
      processExited = true;
      if (this.child !== child) return;
      this.child = null;
      const message = redactLogText(error.message);
      outputBuffer = message;
      this.logger.write('ERROR', `Backend spawn failed: ${message}`);
      if (!signal.aborted) this.#setStatus({ error: message, managed: false, pid: null, source: 'managed', status: 'error' });
    });

    child.on('exit', (code, exitSignal) => {
      processExited = true;
      this.logger.write(code === 0 || exitSignal === 'SIGTERM' ? 'INFO' : 'ERROR', `Backend exited (code=${code}, signal=${exitSignal})`);
      if (this.child !== child) return;
      this.child = null;
      if (signal.aborted || this.status.status === 'stopped') {
        return;
      }
      this.#setStatus({
        error: redactLogText(outputBuffer.trim()) || `Backend 进程已退出 (code=${code ?? 'null'}, signal=${exitSignal ?? 'null'})`,
        managed: false,
        pid: null,
        source: 'managed',
        status: 'error'
      });
    });

    this.#setStatus({ pid: child.pid ?? null });

    for (const retryDelayMs of HEALTH_RETRY_DELAYS_MS) {
      const healthy = await this.#isBackendHealthy();
      if (signal.aborted) return this.getStatus();
      if (healthy && !processExited && this.child === child) {
        this.#setStatus({
          error: null,
          managed: true,
          pid: child.pid ?? null,
          source: 'managed',
          status: 'ready'
        });
        this.logger.write('INFO', 'Backend ready');
        return this.getStatus();
      }
      // Process exited — stop polling immediately instead of waiting the full timeout.
      if (processExited) {
        break;
      }
      await delay(retryDelayMs, signal);
      if (signal.aborted) return this.getStatus();
    }

    const startupError = processExited
      ? redactLogText(outputBuffer.trim()) || '后端进程已退出'
      : `后端启动超时，健康检查未通过${outputBuffer.trim() ? `\n${redactLogText(outputBuffer.trim())}` : ''}`;
    await this.#stopManagedChild('child');
    if (signal.aborted) return this.getStatus();
    this.#setStatus({
      error: startupError,
      managed: false,
      pid: null,
      source: 'managed',
      status: 'error'
    });
    return this.getStatus();
  }

  // 按当前 Playwright 版本补齐浏览器；缓存命中时只做本地检查。
  async #ensurePlaywrightBrowsers(pythonExecutable, pythonPathEntries, signal) {
    this.#setStatus({
      error: null,
      installProgress: null,
      installStep: null,
      installStepLabel: null,
      installStepTotal: null,
      managed: true,
      pid: null,
      source: 'managed',
      status: 'installing-browser'
    });
    if (signal.aborted) return null;

    return new Promise((resolve) => {
      const child = spawn(pythonExecutable, ['-m', 'playwright', 'install', 'chromium'], {
        cwd: this.backendDir,
        detached: process.platform !== 'win32',
        windowsHide: true,
        env: {
          ...process.env,
          PLAYWRIGHT_BROWSERS_PATH: this.playwrightBrowsersDir,
          PYTHONPATH: [
            ...pythonPathEntries,
            process.env.PYTHONPATH
          ].filter(Boolean).join(path.delimiter)
        },
        stdio: ['ignore', 'pipe', 'pipe']
      });

      this.installChild = child;
      // CLI 进度输出稀疏；网络超时和重试交给 Playwright，不能按输出间隔误杀下载。
      let settled = false;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        signal.removeEventListener('abort', onAbort);
        resolve(error);
      };
      const onAbort = () => finish(null);
      signal.addEventListener('abort', onAbort, { once: true });

      let output = '';
      // Playwright 的下载进度条用 \r 原地重绘（"|■■■ | 30% of 171 MiB"），
      // 不是按行输出，所以要按 \r/\n 一起切分，取最新一段解析百分比。
      let progressTail = '';
      // 3 个产物各有自己的 0→100%，百分比一次安装里会跳回 0 三次，不识别就像卡死重来。
      // 产物起始行 "Downloading <name> ..." 是普通换行输出，与 \r 重绘的进度条不同路，可单独识别。
      const seenArtifacts = new Set();
      let stepIndex = 0;
      const FRIENDLY_ARTIFACT_NAMES = {
        chromium: 'Chromium',
        'chromium-headless-shell': 'Headless Shell',
        ffmpeg: 'FFmpeg'
      };
      const headerPattern = /Downloading\s+.+?\(playwright\s+([\w-]+)\s+v[\d.]+\)/g;
      const onChunk = (chunk) => {
        if (settled || signal.aborted) return;
        const text = String(chunk);
        output = `${output}${text}`.slice(-4000);

        headerPattern.lastIndex = 0;
        let headerMatch;
        while ((headerMatch = headerPattern.exec(output)) !== null) {
          const internalName = headerMatch[1];
          if (!seenArtifacts.has(internalName)) {
            seenArtifacts.add(internalName);
            stepIndex += 1;
            this.#setStatus({
              installProgress: 0,
              installStep: stepIndex,
              installStepLabel: FRIENDLY_ARTIFACT_NAMES[internalName] ?? internalName,
              installStepTotal: null
            });
          }
        }

        progressTail = `${progressTail}${text}`.slice(-200);
        const segments = progressTail.split(/[\r\n]+/).filter(Boolean);
        const lastSegment = segments[segments.length - 1] ?? '';
        const match = /(\d{1,3})%\s+of\s+[\d.]+\s*\wi?B/.exec(lastSegment);
        if (match) {
          const percent = Math.min(100, Math.max(0, Number(match[1])));
          if (percent !== this.status.installProgress) {
            this.#setStatus({ installProgress: percent });
          }
        }
      };
      captureProcessOutput(child, this.logger, 'browser-install', onChunk);

      child.on('error', (err) => {
        if (this.installChild === child) this.installChild = null;
        this.logger.write('ERROR', `Browser install failed: ${redactLogText(err.message)}`);
        finish(`浏览器组件安装失败：${redactLogText(err.message)}`);
      });

      child.on('exit', (code) => {
        if (this.installChild === child) this.installChild = null;
        if (code === 0) {
          finish(null);
          return;
        }
        finish(redactLogText(output.trim()) || `浏览器组件安装失败 (code=${code ?? 'null'})`);
      });
      if (signal.aborted) onAbort();
    });
  }

  async #isBackendHealthy() {
    try {
      const health = await this.backendClient.health();
      return health.status === 'ok';
    } catch {
      return false;
    }
  }

  #resolveBackendDir() {
    if (this.isPackaged) {
      return path.join(this.resourcesPath, 'backend');
    }
    return path.join(this.projectRoot, 'backend');
  }

  #resolvePythonExecutable() {
    if (this.isPackaged) {
      const bundledCandidates = process.platform === 'win32'
        ? [path.join(this.backendDir, 'python', 'python.exe')]
        : [
          path.join(this.backendDir, 'python', 'bin', 'python3.12'),
          path.join(this.backendDir, 'python', 'bin', 'python3'),
          path.join(this.backendDir, 'python', 'bin', 'python')
        ];

      for (const candidate of bundledCandidates) {
        if (fs.existsSync(candidate)) {
          return candidate;
        }
      }
    }

    const candidates = process.platform === 'win32'
      ? [path.join(this.backendDir, '.venv', 'Scripts', 'python.exe')]
      : [
        path.join(this.backendDir, '.venv', 'bin', 'python'),
        path.join(this.backendDir, '.venv', 'bin', 'python3')
      ];

    for (const candidate of candidates) {
      if (fs.existsSync(candidate)) {
        return candidate;
      }
    }

    return null;
  }

  #resolvePythonPathEntries() {
    if (process.platform === 'win32') {
      return [path.join(this.backendDir, '.venv', 'Lib', 'site-packages')].filter((candidate) => fs.existsSync(candidate));
    }

    const libDir = path.join(this.backendDir, '.venv', 'lib');
    if (!fs.existsSync(libDir)) {
      return [];
    }

    return fs.readdirSync(libDir)
      .filter((entry) => entry.startsWith('python'))
      .map((entry) => path.join(libDir, entry, 'site-packages'))
      .filter((candidate) => fs.existsSync(candidate));
  }

  #ensureRuntimeDirectories() {
    for (const dir of [
      this.appDataDir,
      this.dbDir,
      this.runtimeDir,
      this.browserRuntimeDir,
      this.scraplingRuntimeDir,
      this.playwrightBrowsersDir,
      this.aiDir,
      this.aiChatsDir,
      this.workspaceRoot,
      this.runRoot,
      this.logDir,
      this.cacheDir
    ]) {
      fs.mkdirSync(dir, { recursive: true });
    }
    // 写入元信息（首次创建时）
    const metaPath = path.join(this.appDataDir, '.metadata');
    if (!fs.existsSync(metaPath)) {
      fs.writeFileSync(metaPath, JSON.stringify({ createdAt: new Date().toISOString(), app: 'easy-rpa' }, null, 2));
    }
  }
}

function signalChild(child, signal) {
  if (process.platform !== 'win32') {
    try {
      // 受管进程以独立进程组启动，同时终止 Python 拉起的 Node 安装器。
      process.kill(-child.pid, signal);
    } catch (error) {
      if (error.code !== 'ESRCH') throw error;
    }
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const executable = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe');
    const args = ['/PID', String(child.pid), '/T'];
    if (signal === 'SIGKILL') args.push('/F');
    const killer = spawn(executable, args, { windowsHide: true, stdio: 'ignore' });
    killer.once('error', reject);
    killer.once('exit', (code) => {
      if (code === 0 || child.exitCode != null || child.signalCode != null) resolve();
      else reject(new Error(`无法终止后端进程树 (code=${code ?? 'null'})`));
    });
  });
}

function terminateChild(child) {
  if (child.exitCode != null || child.signalCode != null || !child.pid) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let settled = false;
    let forceTimer = null;
    let killTimer = null;
    let forced = false;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(forceTimer);
      clearTimeout(killTimer);
      child.removeListener('exit', onExit);
      child.removeListener('error', onError);
      if (error) reject(error);
      else resolve();
    };
    const onExit = () => finish();
    const onError = (error) => finish(error);
    const force = () => {
      if (settled || forced) return;
      forced = true;
      clearTimeout(forceTimer);
      try {
        void signalChild(child, 'SIGKILL').catch(finish);
      } catch (error) {
        finish(error);
      }
      if (!settled) killTimer = setTimeout(() => finish(new Error('无法确认后端进程已退出')), 5000);
    };
    child.once('exit', onExit);
    child.once('error', onError);
    forceTimer = setTimeout(force, 3000);
    try {
      void signalChild(child, 'SIGTERM').catch(force);
    } catch {
      force();
    }
  });
}

function delay(timeoutMs, signal) {
  if (signal?.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const finish = () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', finish);
      resolve();
    };
    const timer = setTimeout(finish, timeoutMs);
    signal?.addEventListener('abort', finish, { once: true });
  });
}

module.exports = {
  BackendSupervisor,
  DEFAULT_APP_DATA_DIR
};
