const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter, once } = require('node:events');
const { PassThrough } = require('node:stream');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

test('backend that exits during health check is not reported ready', async () => {
  const filename = path.resolve(__dirname, '../../electron/backendSupervisor.cjs');
  const realRequire = require('node:module').createRequire(filename);
  let backendChild;
  let spawnCount = 0;
  const spawn = () => {
    const child = new EventEmitter();
    child.pid = 12345;
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = () => { child.emit('exit', null, 'SIGTERM'); };
    if (++spawnCount === 1) process.nextTick(() => child.emit('exit', 0, null));
    else backendChild = child;
    return child;
  };
  const context = {
    AbortController,
    __dirname: path.dirname(filename),
    clearTimeout,
    module: { exports: {} },
    process,
    require: (id) => id === 'node:child_process' ? { spawn } : realRequire(id),
    setTimeout
  };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), context, { filename });
  const appDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'rpa-backend-retry-'));
  try {
    const projectRoot = path.join(appDataDir, 'project');
    const pythonDir = path.join(projectRoot, 'backend', '.venv', 'bin');
    fs.mkdirSync(pythonDir, { recursive: true });
    fs.writeFileSync(path.join(pythonDir, 'python'), '');
    let checks = 0;
    const supervisor = new context.module.exports.BackendSupervisor({
      appDataDir,
      projectRoot,
      backendClient: {
        health: async () => {
          if (++checks === 1) throw new Error('not running');
          backendChild.stderr.write('ERROR Authorization: Bearer sample-credential\n');
          backendChild.emit('exit', 1, null);
          return { status: 'ok' };
        }
      }
    });

    const status = await supervisor.ensureStarted();

    assert.equal(status.status, 'error');
    assert.equal(status.pid, null);
    assert.equal(spawnCount, 2);
    assert.ok(!status.error.includes('sample-credential'));
    const diagnosticLog = fs.readFileSync(path.join(appDataDir, 'logs', 'backend-process.log'), 'utf8');
    assert.ok(diagnosticLog.includes('Backend exited'));
    assert.ok(diagnosticLog.includes('[REDACTED]'));
    assert.ok(!diagnosticLog.includes('sample-credential'));
  } finally {
    fs.rmSync(appDataDir, { recursive: true, force: true });
  }
});

function createHarness({ platform = 'darwin', autoInstall = true, healthy = true, health = null } = {}) {
  const filename = path.resolve(__dirname, '../../electron/backendSupervisor.cjs');
  const realRequire = require('node:module').createRequire(filename);
  const children = [];
  const signals = [];
  const killCommands = [];
  const statuses = [];
  const timers = new Map();
  const state = { healthy, killMode: 'auto' };
  let timerId = 0;
  const exit = (child, code = null, signal = null) => {
    child.exitCode = code;
    child.signalCode = signal;
    child.emit('exit', code, signal);
  };
  const signalProcess = (child, signal) => {
    signals.push({ pid: child.pid, signal });
    if (state.killMode === 'manual' || state.killMode === 'ignore-all') return;
    if (state.killMode === 'ignore-term' && signal === 'SIGTERM') return;
    exit(child, null, signal);
  };
  const spawn = (executable, args, options) => {
    const child = new EventEmitter();
    child.pid = 10000 + children.length;
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.exitCode = null;
    child.signalCode = null;
    if (executable.endsWith('taskkill.exe')) {
      killCommands.push({ executable, args: Array.from(args) });
      queueMicrotask(() => {
        const target = children.find((entry) => String(entry.pid) === args[1]);
        signalProcess(target, args.includes('/F') ? 'SIGKILL' : 'SIGTERM');
        child.emit('exit', target.signalCode === null ? 1 : 0);
      });
      return child;
    }
    child.args = Array.from(args);
    child.options = options;
    child.kind = args[1] === 'playwright' ? 'installer' : 'backend';
    children.push(child);
    if (child.kind === 'installer' && autoInstall) queueMicrotask(() => exit(child, 0));
    return child;
  };
  const context = {
    AbortController,
    __dirname: path.dirname(filename),
    module: { exports: {} },
    process: {
      ...process,
      platform,
      env: { SystemRoot: 'C:\\Windows' },
      kill: (pid, signal) => {
        assert.ok(pid < 0, 'only the owned process group may be signalled');
        const child = children.find((entry) => entry.pid === -pid);
        assert.ok(child, 'unknown process group');
        signalProcess(child, signal);
      }
    },
    require: (id) => {
      if (id === 'node:child_process') return { spawn };
      if (id === 'node:fs') return {
        existsSync: (value) => value.endsWith('/python') || value.endsWith('python.exe'),
        mkdirSync() {}, writeFileSync() {}, readdirSync: () => []
      };
      if (id === './logger.cjs') return {
        ...realRequire(id),
        createFileLogger: () => ({ write() {} })
      };
      return realRequire(id);
    },
    setTimeout: (callback, ms) => {
      const id = ++timerId;
      timers.set(id, { callback, ms });
      if (ms <= 1000) queueMicrotask(() => {
        if (timers.delete(id)) callback();
      });
      return id;
    },
    clearTimeout: (id) => timers.delete(id)
  };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), context, { filename });
  const supervisor = new context.module.exports.BackendSupervisor({
    appDataDir: '/virtual/rpa',
    projectRoot: '/virtual/project',
    onStatusChange: (status) => statuses.push(status),
    backendClient: {
      health: health || (async () => ({
        status: state.healthy && children.some((child) => child.kind === 'backend'
          && child.exitCode === null && child.signalCode === null) ? 'ok' : 'unhealthy'
      }))
    }
  });
  return {
    supervisor, children, signals, killCommands, statuses, timers, state, exit,
    tick: () => new Promise(setImmediate),
    fire: (ms) => {
      const entry = [...timers].find(([, timer]) => timer.ms === ms);
      assert.ok(entry, `missing ${ms}ms timer`);
      timers.delete(entry[0]);
      entry[1].callback();
    }
  };
}

test('stopping before health check prevents all child processes', async () => {
  const h = createHarness();
  const start = h.supervisor.ensureStarted();
  await h.supervisor.stop();
  assert.equal((await start).status, 'stopped');
  assert.equal(h.children.length, 0);
});

test('stopping while health is pending cannot resume startup', async () => {
  let release;
  const h = createHarness({ health: () => new Promise((resolve) => { release = resolve; }) });
  const start = h.supervisor.ensureStarted();
  await h.tick();
  const stop = h.supervisor.stop();
  release({ status: 'ok' });
  await stop;
  assert.equal((await start).status, 'stopped');
  assert.equal(h.children.length, 0);
});

test('stopping the installer cannot start a backend or publish late progress', async () => {
  const h = createHarness({ autoInstall: false });
  const start = h.supervisor.ensureStarted();
  await h.tick();
  const installer = h.children[0];
  installer.stdout.write('Downloading Chrome (playwright chromium v1248)\n| 10% of 185.8 MiB\n');
  await h.supervisor.stop();
  assert.equal((await start).status, 'stopped');
  const updates = h.statuses.length;
  installer.stdout.write('| 90% of 185.8 MiB\n');
  installer.emit('exit', 0);
  assert.equal(h.statuses.length, updates);
  assert.equal(h.children.length, 1);
  assert.deepEqual(h.signals, [{ pid: installer.pid, signal: 'SIGTERM' }]);
  assert.equal(h.timers.size, 0);
});

test('parallel starts share one install and preserve ownership on repeated health checks', async () => {
  const h = createHarness();
  const [first, second] = await Promise.all([h.supervisor.ensureStarted(), h.supervisor.ensureStarted()]);
  assert.equal(first.status, 'ready');
  assert.equal(second.pid, first.pid);
  assert.equal(h.children.length, 2);
  const again = await h.supervisor.ensureStarted();
  assert.equal(again.managed, true);
  assert.equal(again.source, 'managed');
  assert.equal(again.pid, first.pid);
  assert.equal(h.children[0].options.detached, true);
  assert.equal(h.children[1].options.detached, true);
  await h.supervisor.stop();
});

test('a retry waits for the old installer to exit before spawning another', async () => {
  const h = createHarness({ autoInstall: false });
  h.state.killMode = 'manual';
  const first = h.supervisor.ensureStarted();
  await h.tick();
  const stopping = h.supervisor.stop();
  const second = h.supervisor.ensureStarted();
  await h.tick();
  assert.equal(h.children.length, 1);
  assert.notEqual(h.supervisor.getStatus().status, 'stopped');
  h.exit(h.children[0], null, 'SIGTERM');
  await stopping;
  assert.equal((await first).status, 'stopped');
  await h.tick();
  assert.equal(h.children.length, 2);
  h.exit(h.children[1], 0);
  assert.equal((await second).status, 'ready');
  h.state.killMode = 'auto';
  await h.supervisor.stop();
});

test('health timeout retires the owned backend before a retry', async () => {
  const h = createHarness({ healthy: false });
  const failed = await h.supervisor.ensureStarted();
  assert.equal(failed.status, 'error');
  assert.match(failed.error, /健康检查/);
  assert.equal(failed.pid, null);
  assert.equal(h.children[1].signalCode, 'SIGTERM');
  h.state.healthy = true;
  assert.equal((await h.supervisor.ensureStarted()).status, 'ready');
  assert.equal(h.children.length, 4);
  await h.supervisor.stop();
});

test('an unhealthy owned backend is retired instead of running beside its replacement', async () => {
  const h = createHarness();
  await h.supervisor.ensureStarted();
  const previous = h.children[1];
  let checks = 0;
  h.supervisor.backendClient.health = async () => ({ status: ++checks === 1 ? 'unhealthy' : 'ok' });
  await h.supervisor.ensureStarted();
  assert.equal(previous.signalCode, 'SIGTERM');
  assert.equal(h.children.length, 4);
  await h.supervisor.stop();
});

test('an external healthy backend is never terminated', async () => {
  const h = createHarness({ health: async () => ({ status: 'ok' }) });
  const status = await h.supervisor.ensureStarted();
  assert.equal(status.source, 'external');
  assert.equal(status.managed, false);
  await h.supervisor.stop();
  assert.equal(h.signals.length, 0);
  assert.equal(h.children.length, 0);
});

test('an unexpected backend exit reports failure and old callbacks cannot change a retry', async () => {
  const h = createHarness();
  await h.supervisor.ensureStarted();
  const previous = h.children[1];
  assert.doesNotThrow(() => h.exit(previous, 1));
  assert.equal(h.supervisor.getStatus().status, 'error');
  assert.equal(h.supervisor.getStatus().pid, null);
  const retry = await h.supervisor.ensureStarted();
  const updates = h.statuses.length;
  previous.emit('error', new Error('old process error'));
  previous.emit('exit', 1, null);
  assert.equal(h.statuses.length, updates);
  assert.equal(h.supervisor.getStatus().pid, retry.pid);
  await h.supervisor.stop();
});

test('quiet downloads remain cancellable without an output watchdog', async () => {
  const h = createHarness({ autoInstall: false });
  const start = h.supervisor.ensureStarted();
  await h.tick();
  const installer = h.children[0];
  assert.equal(h.timers.size, 0);
  installer.stdout.write('Downloading Chrome Headless Shell (playwright chromium-headless-shell v1248)\n');
  installer.stdout.write('| 50% of 99 MiB\n');
  const status = h.supervisor.getStatus();
  assert.equal(status.installStepLabel, 'Headless Shell');
  assert.equal(status.installProgress, 50);
  assert.equal(status.installStepTotal, null);
  await h.supervisor.stop();
  await start;
});

test('a failed install reports a redacted error and can be retried', async () => {
  const h = createHarness({ autoInstall: false });
  const start = h.supervisor.ensureStarted();
  await h.tick();
  h.children[0].stderr.write('Authorization: Bearer fixture-secret\n');
  h.exit(h.children[0], 1);
  const failure = await start;
  assert.equal(failure.status, 'error');
  assert.ok(!failure.error.includes('fixture-secret'));
  assert.equal(h.children.length, 1);
  const retry = h.supervisor.ensureStarted();
  await h.tick();
  h.exit(h.children[1], 0);
  assert.equal((await retry).status, 'ready');
  await h.supervisor.stop();
});

test('a backend that ignores SIGTERM is force-stopped before reporting stopped', async () => {
  const h = createHarness();
  await h.supervisor.ensureStarted();
  h.state.killMode = 'ignore-term';
  const stopping = h.supervisor.stop();
  await h.tick();
  assert.equal(h.supervisor.getStatus().status, 'ready');
  h.fire(3000);
  await stopping;
  assert.equal(h.supervisor.getStatus().status, 'stopped');
  assert.equal(h.children[1].signalCode, 'SIGKILL');
  assert.equal(h.timers.size, 0);
});

test('failure to confirm process exit retains ownership and reports error', async () => {
  const h = createHarness();
  await h.supervisor.ensureStarted();
  h.state.killMode = 'ignore-all';
  const rejection = assert.rejects(h.supervisor.stop(), /无法确认/);
  await h.tick();
  h.fire(3000);
  await h.tick();
  h.fire(5000);
  await rejection;
  assert.equal(h.supervisor.getStatus().status, 'error');
  assert.equal(h.supervisor.child, h.children[1]);
  h.state.killMode = 'auto';
  await h.supervisor.stop();
});

test('Windows termination uses taskkill on the owned process tree with force fallback', async () => {
  const h = createHarness({ platform: 'win32' });
  await h.supervisor.ensureStarted();
  h.state.killMode = 'ignore-term';
  await h.supervisor.stop();
  assert.deepEqual(h.killCommands.map((entry) => entry.args), [
    ['/PID', String(h.children[1].pid), '/T'],
    ['/PID', String(h.children[1].pid), '/T', '/F']
  ]);
  assert.equal(h.children[1].options.detached, false);
  assert.equal(h.children[1].options.windowsHide, true);
});

test('POSIX stop terminates a real installer and its descendant', { skip: process.platform === 'win32', timeout: 10000 }, async () => {
  const filename = path.resolve(__dirname, '../../electron/backendSupervisor.cjs');
  const realRequire = require('node:module').createRequire(filename);
  const appDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'rpa-installer-group-'));
  let installer;
  let workerPid;
  let closed = false;
  let readinessTimer;
  let ready;
  const workerReady = new Promise((resolve, reject) => {
    ready = resolve;
    readinessTimer = setTimeout(() => reject(new Error('installer did not start')), 5000);
  });
  const context = {
    AbortController, __dirname: path.dirname(filename), module: { exports: {} }, process, setTimeout, clearTimeout,
    require: (id) => id === 'node:child_process' ? {
      spawn: (_executable, _args, options) => {
        assert.equal(installer, undefined, 'cancellation must not launch a backend');
        installer = require('node:child_process').spawn(process.execPath, ['-e', `
          const child = require('node:child_process').spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
          process.stdout.write('worker=' + child.pid + '\\n');
          setInterval(() => {}, 1000);
        `], options);
        let output = '';
        installer.stdout.on('data', (chunk) => {
          output += chunk;
          const match = /worker=(\d+)/.exec(output);
          if (match) {
            workerPid = Number(match[1]);
            clearTimeout(readinessTimer);
            ready();
          }
        });
        installer.on('close', () => { closed = true; });
        return installer;
      }
    } : realRequire(id)
  };
  try {
    const projectRoot = path.join(appDataDir, 'project');
    const pythonDir = path.join(projectRoot, 'backend', '.venv', 'bin');
    fs.mkdirSync(pythonDir, { recursive: true });
    fs.writeFileSync(path.join(pythonDir, 'python'), '');
    vm.runInNewContext(fs.readFileSync(filename, 'utf8'), context, { filename });
    const supervisor = new context.module.exports.BackendSupervisor({
      appDataDir, projectRoot, backendClient: { health: async () => ({ status: 'unhealthy' }) }
    });
    const starting = supervisor.ensureStarted();
    await workerReady;
    await supervisor.stop();
    assert.equal((await starting).status, 'stopped');
    if (!closed) await once(installer, 'close');
    for (let attempt = 0; attempt < 40; attempt++) {
      try {
        process.kill(workerPid, 0);
      } catch (error) {
        if (error.code === 'ESRCH') return;
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    assert.fail('installer descendant survived stop');
  } finally {
    clearTimeout(readinessTimer);
    if (installer) {
      try { process.kill(-installer.pid, 'SIGKILL'); } catch (error) {
        if (error.code !== 'ESRCH') throw error;
      }
    }
    fs.rmSync(appDataDir, { recursive: true, force: true });
  }
});
