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
    appDataDir = DEFAULT_APP_DATA_DIR
  } = {}) {
    this.backendClient = backendClient;
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
      this.status = {
        ...this.status,
        error: null,
        managed: false,
        pid: null,
        status: 'stopped'
      };
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

    this.status = {
      ...this.status,
      error: null,
      managed: false,
      pid: null,
      status: 'stopped'
    };
    return this.getStatus();
  }

  async #ensureStartedInternal() {
    this.status = {
      ...this.status,
      error: null,
      status: 'checking'
    };

    if (await this.#isBackendHealthy()) {
      this.status = {
        ...this.status,
        error: null,
        managed: false,
        pid: null,
        source: 'external',
        status: 'ready'
      };
      return this.getStatus();
    }

    const pythonExecutable = this.#resolvePythonExecutable();
    if (pythonExecutable === null) {
      this.status = {
        ...this.status,
        error: this.isPackaged
          ? '未找到打包后的 Python 运行时，请重新执行 Electron 打包并确认 backend 资源已包含'
          : '未找到 backend/.venv Python 运行时，请先在 backend 目录执行 uv sync',
        managed: false,
        pid: null,
        source: 'missing',
        status: 'error'
      };
      return this.getStatus();
    }

    this.status = {
      ...this.status,
      error: null,
      managed: true,
      pid: null,
      source: 'managed',
      status: 'starting'
    };

    this.#ensureRuntimeDirectories();

    const pythonPathEntries = this.#resolvePythonPathEntries();

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
    let stderrBuffer = '';

    child.stderr?.on('data', (chunk) => {
      stderrBuffer = `${stderrBuffer}${String(chunk)}`.slice(-4000);
    });

    child.on('exit', (code, signal) => {
      this.child = null;
      if (this.status.status === 'stopped') {
        return;
      }
      this.status = {
        ...this.status,
        error: stderrBuffer.trim() || `Backend 进程已退出 (code=${code ?? 'null'}, signal=${signal ?? 'null'})`,
        managed: false,
        pid: null,
        source: 'managed',
        status: 'error'
      };
    });

    this.status = {
      ...this.status,
      pid: child.pid ?? null
    };

    for (const retryDelayMs of HEALTH_RETRY_DELAYS_MS) {
      if (await this.#isBackendHealthy()) {
        this.status = {
          ...this.status,
          error: null,
          managed: true,
          pid: child.pid ?? null,
          source: 'managed',
          status: 'ready'
        };
        return this.getStatus();
      }
      await delay(retryDelayMs);
    }

    this.status = {
      ...this.status,
      error: stderrBuffer.trim() || '后端启动超时，健康检查未通过',
      managed: true,
      pid: child.pid ?? null,
      source: 'managed',
      status: 'error'
    };
    return this.getStatus();
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
