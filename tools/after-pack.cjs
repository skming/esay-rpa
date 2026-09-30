'use strict';

const pc = require('picocolors');
const { w } = require('./lib/ui.cjs');
const adhocSignMac = require('./adhoc-sign-mac.cjs');
const { verifyPackagedApp } = require('./verify_packaged_app.cjs');

module.exports = async function afterPack(context) {
  const platform = context.electronPlatformName;
  const expectedArch = process.env.RPA_TARGET_ARCH || process.arch;
  const appName = context.packager.appInfo.productFilename;
  const executableName = context.packager.executableName || appName;

  w(`\n     ${pc.dim('→')}  校验随包后端 ${pc.dim(`(${platform}/${expectedArch})`)}\n`);
  await verifyPackagedApp({
    appName,
    appOutDir: context.appOutDir,
    executableName,
    expectedArch,
    platform,
  });
  w(`     ${pc.green('✓')}  随包后端架构、导入与健康检查通过\n`);

  await adhocSignMac(context);
};
