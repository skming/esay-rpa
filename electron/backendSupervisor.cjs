const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');

const { BackendClient, DEFAULT_BACKEND_URL } = require('./backendClient.cjs');

const DEFAULT_HOST = '127.0.0.1';
const DEFAULT_PORT = 8765;
// Adaptive health-check: fast burst first, then back off. Extended to 38 s to accommodate
// first-install cold starts where Python + uvicorn + FastAPI can take 15-30 s.
// Total budget: 5×150 + 5×300 + 10×600 + 30×1000 = 38.25 s
const HEALTH_RETRY_DELAYS_MS = [
  ...Array(5).fill(150),    // 0–0.75 s   — fast burst
  ...Array(5).fill(300),    // 0.75–2.25 s
  ...Array(10).fill(600),   // 2.25–8.25 s
  ...Array(30).fill(1000),  // 8.25–38.25 s — first-install cold start
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
    this.cacheDir = path.join(appDataDir, 'cache');

    this.child = null;
    this.startPromise = null;
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
    if (this.startPromise !== null) {
      return this.startPromise;
    }

    this.startPromise = this.#ensureStartedInternal().finally(() => {
      this.startPromise = null;
    });
    return this.startPromise;
  }

  async stop() {
    if (this.child === null) {
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
    }

    const child = this.child;
    this.child = null;

    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        child.kill('SIGKILL');
        resolve();
      }, 3000);

      child.once('exit', () => {
        clearTimeout(timer);
        resolve();
      });

      child.kill('SIGTERM');
    });

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
  }

  async #ensureStartedInternal() {
    this.#setStatus({
      error: null,
      status: 'checking'
    });

    if (await this.#isBackendHealthy()) {
      this.#setStatus({
        error: null,
        managed: false,
        pid: null,
        source: 'external',
        status: 'ready'
      });
      return this.getStatus();
    }

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

    const installError = await this.#ensurePlaywrightBrowsers(pythonExecutable, pythonPathEntries);
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

    const child = spawn(
      pythonExecutable,
      ['-m', 'uvicorn', 'app.main:app', '--host', this.host, '--port', String(this.port), '--log-level', 'info'],
      {
        cwd: this.backendDir,
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
      outputBuffer = `${outputBuffer}${String(chunk)}`.slice(-4000);
    };

    child.stdout?.on('data', appendOutput);
    child.stderr?.on('data', appendOutput);

    child.on('exit', (code, signal) => {
      processExited = true;
      this.child = null;
      if (this.status.status === 'stopped') {
        return;
      }
      this.#setStatus({
        error: outputBuffer.trim() || `Backend 进程已退出 (code=${code ?? 'null'}, signal=${signal ?? 'null'})`,
        managed: false,
        pid: null,
        source: 'managed',
        status: 'error'
      });
    });

    this.#setStatus({ pid: child.pid ?? null });

    for (const retryDelayMs of HEALTH_RETRY_DELAYS_MS) {
      if (await this.#isBackendHealthy()) {
        this.#setStatus({
          error: null,
          managed: true,
          pid: child.pid ?? null,
          source: 'managed',
          status: 'ready'
        });
        return this.getStatus();
      }
      // Process exited — stop polling immediately instead of waiting the full timeout.
      if (processExited) {
        break;
      }
      await delay(retryDelayMs);
    }

    this.#setStatus({
      error: outputBuffer.trim() || '后端启动超时，健康检查未通过',
      managed: true,
      pid: child.pid ?? null,
      source: 'managed',
      status: 'error'
    });
    return this.getStatus();
  }

  /**
   * 浏览器内核不随安装包分发（体积会暴涨几百 MB），首启才下载。
   * 已存在时 `playwright install` 只是一次本地检查，耗时可忽略。
   */
  async #ensurePlaywrightBrowsers(pythonExecutable, pythonPathEntries) {
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

    return new Promise((resolve) => {
      const child = spawn(pythonExecutable, ['-m', 'playwright', 'install', 'chromium'], {
        cwd: this.backendDir,
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

      // 超时按"停滞"计而不是总时长：慢网络下 250MB 可能超过任何固定死线，
      // 只要进程还在持续输出（进度在推进）就不该杀；90 秒无任何输出才判定卡死。
      const STALL_TIMEOUT_MS = 90 * 1000;
      let timer = null;
      const resetTimer = () => {
        if (timer !== null) clearTimeout(timer);
        timer = setTimeout(() => {
          child.kill('SIGKILL');
          resolve(`浏览器组件下载已停滞超过 90 秒，请检查网络后点击重试\n${output.trim()}`);
        }, STALL_TIMEOUT_MS);
      };

      let output = '';
      // Playwright 的下载进度条用 \r 原地重绘（"|■■■ | 30% of 171 MiB"），
      // 不是按行输出，所以要按 \r/\n 一起切分，取最新一段解析百分比。
      let progressTail = '';
      // 3 个产物各有自己的 0→100%，百分比一次安装里会跳回 0 三次，不识别就像卡死重来。
      // 产物起始行 "Downloading <name> ..." 是普通换行输出，与 \r 重绘的进度条不同路，可单独识别。
      const seenArtifacts = new Set();
      let stepIndex = 0;
      // 仅作为文案提示的经验值：以后 Playwright 版本调整下载产物数量，最坏情况只是
      // "第 4/3 项"这种轻微文案瑕疵，不影响下载和进度本身。
      const KNOWN_STEP_TOTAL = 3;
      const FRIENDLY_ARTIFACT_NAMES = {
        chromium: 'Chromium',
        'chromium-headless-shell': 'Headless Shell',
        ffmpeg: 'FFmpeg'
      };
      const headerPattern = /Downloading\s+.+?\(playwright\s+([\w-]+)\s+v[\d.]+\)/g;
      const onChunk = (chunk) => {
        resetTimer();
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
              installStepTotal: KNOWN_STEP_TOTAL
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
      child.stdout?.on('data', onChunk);
      child.stderr?.on('data', onChunk);

      resetTimer();

      child.on('error', (err) => {
        clearTimeout(timer);
        resolve(`浏览器组件安装失败：${err.message}`);
      });

      child.on('exit', (code) => {
        clearTimeout(timer);
        if (code === 0) {
          resolve(null);
          return;
        }
        resolve(output.trim() || `浏览器组件安装失败 (code=${code ?? 'null'})`);
      });
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

function delay(timeoutMs) {
  return new Promise((resolve) => {
    setTimeout(resolve, timeoutMs);
  });
}

module.exports = {
  BackendSupervisor,
  DEFAULT_APP_DATA_DIR
};
