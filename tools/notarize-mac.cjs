'use strict';

const { notarize } = require('@electron/notarize');
const pc = require('picocolors');
const { w } = require('./lib/ui.cjs');

module.exports = async function notarizeMac(context) {
  if (process.platform !== 'darwin') return;
  if (process.env.RPA_RELEASE_SIGN !== '1') return;

  const { appOutDir, packager } = context;
  const appName = packager.appInfo.productFilename;
  const appPath = `${appOutDir}/${appName}.app`;
  const arch = packager.platform.name === 'mac'
    ? context.arch ?? 'unknown'
    : 'unknown';

  const appleId = requireEnv('APPLE_ID');
  const appleIdPassword = requireEnv('APPLE_APP_SPECIFIC_PASSWORD');
  const teamId = requireEnv('APPLE_TEAM_ID');

  w(`\n     ${pc.dim('→')}  公证 ${pc.bold(appName)}  ${pc.dim(`(${arch})`)}\n`);
  w(`        ${pc.dim('Apple ID: ' + appleId)}\n`);

  const t0 = Date.now();
  try {
    await notarize({
      appBundleId: packager.appInfo.appId,
      appPath,
      appleId,
      appleIdPassword,
      teamId,
    });
    const elapsed = Math.round((Date.now() - t0) / 1000);
    w(`     ${pc.green('✓')}  公证完成  ${pc.dim(elapsed + 's')}\n`);
  } catch (e) {
    w(`     ${pc.red('✗')}  公证失败: ${e.message}\n`);
    throw e;
  }
};

function requireEnv(name) {
  const value = process.env[name];
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`缺少 macOS 公证环境变量: ${pc.bold(name)}`);
  }
  return value;
}
