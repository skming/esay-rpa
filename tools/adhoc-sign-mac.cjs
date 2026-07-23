'use strict';

const path = require('node:path');
const { spawnSync } = require('node:child_process');
const pc = require('picocolors');
const { w } = require('./lib/ui.cjs');

const ROOT = path.resolve(__dirname, '..');
const ENTITLEMENTS = path.join(ROOT, 'buildResources', 'entitlements.mac.plist');

module.exports = async function adhocSignMac(context) {
  if (process.platform !== 'darwin') return;
  if (process.env.RPA_RELEASE_SIGN === '1') return;
  if (context.electronPlatformName !== 'darwin') return;

  const appName = context.packager.appInfo.productFilename;
  const appPath = `${context.appOutDir}/${appName}.app`;

  w(`\n     ${pc.dim('→')}  adhoc 重签 ${pc.bold(appName)}\n`);

  // --options runtime enables Hardened Runtime so --entitlements take effect.
  // --deep is deprecated but acceptable for internal adhoc builds.
  const result = spawnSync('codesign', [
    '--force', '--deep', '--sign', '-',
    '--entitlements', ENTITLEMENTS,
    '--options', 'runtime',
    appPath,
  ], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (result.status !== 0) {
    throw new Error(`adhoc 重签失败\n${result.stderr || result.stdout}`);
  }

  const verify = spawnSync('codesign', ['--verify', '--deep', '--strict', '--verbose=2', appPath], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (verify.status !== 0) {
    throw new Error(`adhoc 签名校验失败\n${verify.stderr || verify.stdout}`);
  }

  w(`     ${pc.green('✓')}  adhoc 签名校验通过（含 entitlements）\n`);
};
