#!/usr/bin/env node
'use strict';

/**
 * Windows / Linux 打包脚本，带控制台动画进度。
 *
 * 用法：
 *   node tools/dist-other.cjs --win
 *   node tools/dist-other.cjs --linux
 *   node tools/dist-other.cjs --win --clean
 *   node tools/dist-other.cjs --win --skip-build
 *   node tools/dist-other.cjs --win --skip-bundle
 */

const path = require('node:path');
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const pc = require('picocolors');
const { w, fmtMs, fmtBytes, fileSize, makeSubSpinner, runSilent, runStep } = require('./lib/ui.cjs');

const ROOT = path.resolve(__dirname, '..');

const flags = {
  win:        process.argv.includes('--win'),
  linux:      process.argv.includes('--linux'),
  clean:      process.argv.includes('--clean'),
  skipBuild:  process.argv.includes('--skip-build'),
  skipBundle: process.argv.includes('--skip-bundle'),
};

if (!flags.win && !flags.linux) {
  process.stderr.write(pc.red('请指定 --win 或 --linux\n'));
  process.exit(1);
}

if (flags.win && process.platform !== 'win32') {
  process.stderr.write(
    pc.red('Windows exe 必须在 Windows 环境构建。\n') +
    pc.dim('原因：桌面包内包含平台相关的 Python/Playwright 原生运行时；在 macOS 上交叉构建会产出不可运行的 Windows 后端。\n') +
    pc.dim('请在 Windows CI 或 Windows 本机执行：pnpm electron:dist:win\n')
  );
  process.exit(1);
}

if (flags.linux && process.platform !== 'linux') {
  process.stderr.write(
    pc.red('Linux AppImage 必须在 Linux 环境构建。\n') +
    pc.dim('原因：桌面包内包含平台相关的 Python/Playwright 原生运行时；跨平台构建会产出不可运行的后端。\n') +
    pc.dim('请在 Linux CI 或 Linux 本机执行：pnpm electron:dist:linux\n')
  );
  process.exit(1);
}

const PLATFORM   = flags.win ? 'win' : 'linux';
const LABEL      = flags.win ? 'Windows (x64)' : 'Linux (x64 AppImage)';
const EB_FLAG    = flags.win ? '--win' : '--linux';

const PKG_VERSION = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8')
).version;

const EB = path.join(ROOT, 'node_modules', '.bin', 'electron-builder');

// ── electron-builder 进度追踪 ─────────────────────────────────────────
function runElectronBuilder() {
  return new Promise((resolve, reject) => {
    w(`\n  ${pc.bold(pc.blue('▶'))}  打包 ${pc.bold(LABEL)}\n`);
    w(`  ${'─'.repeat(46)}\n`);

    const sub = makeSubSpinner();
    const targets = new Set();
    let inBuildPhase = false;
    const t0 = Date.now();

    const parseLine = (line) => {
      const m = line.match(/•\s+(\S+)\s*(.*)/);
      if (!m) return;
      const [, key, rest] = m;

      if (key === 'packaging') {
        if (!sub.active()) sub.start('应用打包');
      } else if (key === 'building' && !/block\s*map/i.test(rest)) {
        const t = (rest.match(/target=(\S+)/) || [])[1];
        if (t) targets.add(t);
        const targetLabel = targets.size ? `生成 ${[...targets].join(' + ')}` : '生成安装包';

        if (!inBuildPhase) {
          inBuildPhase = true;
          if (sub.active()) sub.finish();
          sub.start(targetLabel);
        } else if (sub.active()) {
          sub.updateLabel(targetLabel);
        }
      }
    };

    const child = spawn(EB, ['--config', 'electron-builder.config.cjs', EB_FLAG], {
      cwd: ROOT,
      env: { ...process.env },
      stdio: ['ignore', 'pipe', 'pipe'],
      shell: process.platform === 'win32',
    });

    let buf = '';
    const onData = (chunk) => {
      buf += chunk.toString();
      const lines = buf.split('\n');
      buf = lines.pop();
      lines.forEach(parseLine);
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);

    // 仅在进程真正退出后标记完成
    child.on('close', (code) => {
      if (buf) parseLine(buf);
      if (sub.active()) sub.finish();

      const total = fmtMs(Date.now() - t0);
      if (code === 0) {
        w(`  ${pc.green('✓')}  打包 ${LABEL}  ${pc.dim(total)}\n`);
        resolve();
      } else {
        w(`  ${pc.red('✗')}  打包 ${LABEL} 失败\n`);
        reject(new Error(`electron-builder (${PLATFORM}) 退出码 ${code}`));
      }
    });
  });
}

// ── Main ──────────────────────────────────────────────────────────────
async function main() {
  w(`\n  ${pc.bold('Easy RPA')}  ${pc.dim('→')}  ${LABEL} 打包  ${pc.dim('v' + PKG_VERSION)}\n`);
  w(`  ${'─'.repeat(46)}\n\n`);

  const t0 = Date.now();

  if (flags.clean) {
    await runSilent('node', ['tools/clean.cjs'], { cwd: ROOT });
    w(`  ${pc.green('✓')}  旧产物已清理\n`);
  }

  if (flags.skipBuild) {
    w(`  ${pc.dim('·')}  前端构建  ${pc.dim('(已跳过 --skip-build)')}\n`);
  } else {
    await runStep('前端构建 (tsc + vite)', async (sp) => {
      await runSilent('pnpm', ['build'], { cwd: ROOT });
      await runSilent('pnpm', ['build'], { cwd: path.join(ROOT, 'extension') });
      sp.done();
    });
  }

  if (flags.skipBundle) {
    w(`  ${pc.dim('·')}  Python 运行时准备  ${pc.dim('(已跳过 --skip-bundle)')}\n`);
  } else {
    await runStep('Python 运行时准备', async (sp) => {
      await runSilent('node', ['tools/prepare_backend_bundle.cjs'], { cwd: ROOT });
      sp.done();
    });
  }

  await runStep('Bundle 完整性校验', async (sp) => {
    await runSilent('node', ['tools/verify_backend_bundle.cjs'], { cwd: ROOT });
    sp.done();
  });

  await runElectronBuilder().catch((e) => {
    w(pc.red(e.message) + '\n');
    process.exit(1);
  });

  const total = fmtMs(Date.now() - t0);
  w(`\n  ${'─'.repeat(46)}\n`);
  w(`  ${pc.green('✓')}  ${pc.bold('全部完成')}  ${pc.dim(total)}\n\n`);

  const outFile = flags.win
    ? `Easy RPA Setup ${PKG_VERSION}.exe`
    : `Easy RPA-${PKG_VERSION}.AppImage`;
  const outPath = path.join(ROOT, 'release', outFile);
  const size = fileSize(outPath);
  const sizeStr = size ? pc.dim(`  ${fmtBytes(size)}`) : '';
  w(`     ${PLATFORM === 'win' ? 'win  ' : 'linux'}  ${pc.dim('→')}  release/${outFile}${sizeStr}\n\n`);
}

main();
