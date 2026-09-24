const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
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
          backendChild.emit('exit', 1, null);
          return { status: 'ok' };
        }
      }
    });

    const status = await supervisor.ensureStarted();

    assert.equal(status.status, 'error');
    assert.equal(status.pid, null);
    assert.equal(spawnCount, 2);
  } finally {
    fs.rmSync(appDataDir, { recursive: true, force: true });
  }
});
