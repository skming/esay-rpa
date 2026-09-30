const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { normalizeArch, resolveLayout } = require('../verify_packaged_app.cjs');

test('normalizes operating-system architecture names', () => {
  assert.equal(normalizeArch('aarch64'), 'arm64');
  assert.equal(normalizeArch('arm64'), 'arm64');
  assert.equal(normalizeArch('AMD64'), 'x64');
  assert.equal(normalizeArch('x86_64'), 'x64');
});

test('resolves macOS packaged resource paths', () => {
  const layout = resolveLayout({ appName: 'Easy RPA', appOutDir: '/tmp/mac-arm64', platform: 'darwin' });
  assert.equal(layout.electronExecutable, path.join('/tmp/mac-arm64', 'Easy RPA.app', 'Contents', 'MacOS', 'Easy RPA'));
  assert.equal(layout.resourcesDir, path.join('/tmp/mac-arm64', 'Easy RPA.app', 'Contents', 'Resources'));
});

test('resolves Windows packaged resource paths', () => {
  const appOutDir = 'C:\\release\\win';
  const layout = resolveLayout({ appName: 'Easy RPA', appOutDir, platform: 'win32' });
  assert.equal(layout.electronExecutable, path.join(path.resolve(appOutDir), 'Easy RPA.exe'));
  assert.equal(layout.resourcesDir, path.join(path.resolve(appOutDir), 'resources'));
});

test('uses the configured Linux executable name', () => {
  const layout = resolveLayout({
    appName: 'Easy RPA',
    appOutDir: 'release/linux-unpacked',
    executableName: 'easy-rpa',
    platform: 'linux',
  });
  assert.equal(layout.electronExecutable, path.resolve('release/linux-unpacked/easy-rpa'));
});
