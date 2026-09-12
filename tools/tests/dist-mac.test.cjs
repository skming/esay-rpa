const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function loadBuilder(code, output, spawnError) {
  let args;
  const states = [];
  const filename = path.resolve(__dirname, '../dist-mac.cjs');
  const realRequire = require('node:module').createRequire(filename);
  const fakeRequire = id => {
    if (id === 'node:child_process') return { spawn: (_cmd, passedArgs) => {
      args = passedArgs;
      const child = new EventEmitter();
      child.stdout = new PassThrough();
      child.stderr = new PassThrough();
      process.nextTick(() => {
        child.stdout.end('  • building target=DMG\n');
        child.stderr.end(output);
        if (spawnError) child.emit('error', new Error(spawnError));
        setImmediate(() => child.emit('close', code, null));
      });
      return child;
    }};
    if (id === './lib/ui.cjs') return {
      ...realRequire(id), w() {},
      makeSubSpinner: () => ({ active: () => true, start() {}, updateLabel() {},
        finish: () => states.push('success'), fail: () => states.push('failure') }),
    };
    return realRequire(id);
  };
  const context = { require: fakeRequire, module: { exports: {} }, __dirname: path.dirname(filename), process };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), context);
  return { run: context.module.exports.runElectronBuilder, states, args: () => Array.from(args) };
}

test('failed artifact generation retains raw error and marks failure', async () => {
  const builder = loadBuilder(1, 'hdiutil: create failed - device error\n');
  await assert.rejects(builder.run('arm64', false), /hdiutil: create failed - device error/);
  assert.equal(builder.states.at(-1), 'failure');
  assert.deepEqual(builder.args(), ['--config', 'electron-builder.config.cjs', '--mac', 'dmg', 'zip', '--arm64', '--publish', 'never']);
});

test('successful directory build uses only the requested architecture and target', async () => {
  const builder = loadBuilder(0, '');
  await builder.run('x64', true);
  assert.equal(builder.states.at(-1), 'success');
  assert.deepEqual(builder.args(), ['--config', 'electron-builder.config.cjs', '--mac', 'dir', '--x64', '--publish', 'never']);
});

test('spawn failure is returned to the caller', async () => {
  const builder = loadBuilder(-2, '', 'spawn ENOENT');
  await assert.rejects(builder.run('arm64', false), /spawn ENOENT/);
  assert.equal(builder.states.at(-1), 'failure');
});
