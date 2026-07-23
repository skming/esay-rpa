#!/usr/bin/env node
'use strict';

/**
 * macOS 打包脚本，带控制台动画进度。
 *
 * 用法：
 *   node tools/dist-mac.cjs              # 交互选择架构，生成 DMG + ZIP
 *   node tools/dist-mac.cjs --dir        # 仅打包 .app，不生成 DMG/ZIP（快速预览）
 *   node tools/dist-mac.cjs --clean      # 打包前先清理 release/ + dist/
 *   node tools/dist-mac.cjs --skip-build   # 跳过前端构建（dist/ 已是最新）
 *   node tools/dist-mac.cjs --skip-bundle  # 跳过 Python 运行时准备
 *   RPA_RELEASE_SIGN=1 node tools/dist-mac.cjs  # 签名 + 公证模式
 */

const path = require('node:path');
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const pc = require('picocolors');
const prompts = require('prompts');
const { w, IS_TTY, FRAMES, fmtMs, fmtBytes, fileSize, makeSubSpinner, runSilent, runStep } = require('./lib/ui.cjs');

const ROOT = path.resolve(__dirname, '..');
const IS_SIGNED = process.env.RPA_RELEASE_SIGN === '1';

const flags = {
  clean:      process.argv.includes('--clean'),
  skipBuild:  process.argv.includes('--skip-build'),
  skipBundle: process.argv.includes('--skip-bundle'),
  dir:        process.argv.includes('--dir'),
};

const PKG_VERSION = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8')
).version;

const EB = path.join(ROOT, 'node_modules', '.bin', 'electron-builder');

// ── electron-builder 进度追踪 ─────────────────────────────────────────
// DMG 和 ZIP 在 EB 内部并发构建，无法可靠感知各自完成时刻。
// 采用两阶段模型：应用打包 → 生成安装包；仅在进程退出时标记完成。
function runElectronBuilder(arch, dirOnly) {
  const label = arch === 'arm64' ? 'Apple Silicon (arm64)' : 'Intel (x64)';
  const envExtra = IS_SIGNED
    ? { RPA_RELEASE_SIGN: '1' }
    : { CSC_IDENTITY_AUTO_DISCOVERY: 'false' };

  const ebArgs = ['--config', 'electron-builder.config.cjs', `--${arch}`];
  if (dirOnly) ebArgs.push('--dir');

  return new Promise((resolve, reject) => {
    w(`\n  ${pc.bold(pc.blue('▶'))}  打包 ${pc.bold(label)}\n`);
    w(`  ${'─'.repeat(46)}\n`);

    const sub = makeSubSpinner();
    const targets = new Set();   // 记录发现的目标（DMG / ZIP）
    let inBuildPhase = false;
    const t0 = Date.now();

    const parseLine = (line) => {
      const m = line.match(/•\s+(\S+)\s*(.*)/);
      if (!m) return;
      const [, key, rest] = m;

      if (key === 'packaging') {
        if (!sub.active()) sub.start('应用打包');
      } else if (key === 'building' && !/block\s*map/i.test(rest)) {
        // 发现构建目标，收集后更新标签
        if (/target=DMG/i.test(rest))                        targets.add('DMG');
        if (/target=macOS\s+zip|target=zip/i.test(rest))     targets.add('ZIP');
        if (/target=dir/i.test(rest))                        targets.add('App');

        const targetLabel = targets.size ? `生成 ${[...targets].join(' + ')}` : '生成安装包';

        if (!inBuildPhase) {
          inBuildPhase = true;
          if (sub.active()) sub.finish();       // 结束"应用打包"
          sub.start(targetLabel);
        } else if (sub.active()) {
          sub.updateLabel(targetLabel);         // 新目标出现时更新标签
        }
      }
    };

    const child = spawn(EB, ebArgs, {
      cwd: ROOT,
      env: { ...process.env, ...envExtra },
      stdio: ['ignore', 'pipe', 'pipe'],
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

    // 仅在进程真正退出后才标记完成，避免并发构建提前结束动画
    child.on('close', (code) => {
      if (buf) parseLine(buf);
      if (sub.active()) sub.finish();

      const total = fmtMs(Date.now() - t0);
      if (code === 0) {
        w(`  ${pc.green('✓')}  打包 ${label}  ${pc.dim(total)}\n`);
        resolve();
      } else {
        w(`  ${pc.red('✗')}  打包 ${label} 失败\n`);
        reject(new Error(`electron-builder (${arch}) 退出码 ${code}`));
      }
    });
  });
}

// ── Main ──────────────────────────────────────────────────────────────
async function main() {
  const modeTag = flags.dir
    ? pc.dim('  [--dir]')
    : IS_SIGNED ? `  ${pc.yellow('签名模式')}` : '';

  w(`\n  ${pc.bold('Easy RPA')}  ${pc.dim('→')}  macOS 打包  ${pc.dim('v' + PKG_VERSION)}${modeTag}\n`);
  w(`  ${'─'.repeat(46)}\n\n`);

  // 0. 清理旧产物
  if (flags.clean) {
    await runSilent('node', ['tools/clean.cjs'], { cwd: ROOT });
    w(`  ${pc.green('✓')}  旧产物已清理\n`);
  }

  // 选择打包架构（--dir 模式默认当前架构，不弹菜单）
  let archList;
  if (flags.dir) {
    archList = [process.arch === 'x64' ? 'x64' : 'arm64'];
  } else if (IS_TTY) {
    const { arch } = await prompts({
      type: 'select',
      name: 'arch',
      message: '选择打包目标',
      choices: [
        { title: 'arm64 + x64  (全平台)', value: 'both' },
        { title: 'Apple Silicon  (arm64)', value: 'arm64' },
        { title: 'Intel         (x64)', value: 'x64' },
      ],
      initial: 0,
    });
    if (!arch) { w(pc.dim('  已取消。\n\n')); process.exit(0); }
    archList = arch === 'both' ? ['arm64', 'x64'] : [arch];
    w('\n');
  } else {
    archList = ['arm64', 'x64'];
  }

  const t0 = Date.now();

  // 1+2. 前端构建 & Python 运行时准备（独立任务，并行执行）
  const buildSkipped  = flags.skipBuild;
  const bundleSkipped = flags.skipBundle;

  if (buildSkipped && bundleSkipped) {
    w(`  ${pc.dim('·')}  前端构建  ${pc.dim('(已跳过 --skip-build)')}\n`);
    w(`  ${pc.dim('·')}  Python 运行时准备  ${pc.dim('(已跳过 --skip-bundle)')}\n`);
  } else if (buildSkipped) {
    w(`  ${pc.dim('·')}  前端构建  ${pc.dim('(已跳过 --skip-build)')}\n`);
    await runStep('Python 运行时准备', async (sp) => {
      await runSilent('node', ['tools/prepare_backend_bundle.cjs'], { cwd: ROOT });
      sp.done();
    });
  } else if (bundleSkipped) {
    w(`  ${pc.dim('·')}  Python 运行时准备  ${pc.dim('(已跳过 --skip-bundle)')}\n`);
    await runStep('前端构建 (tsc + vite)', async (sp) => {
      await runSilent('pnpm', ['build'], { cwd: ROOT });
      await runSilent('pnpm', ['build'], { cwd: path.join(ROOT, 'extension') });
      sp.done();
    });
  } else {
    // 两者都需要执行 — 并行，单个合并 spinner 避免光标冲突
    await runStep('前端构建 + Python 运行时准备（并行）', async (sp) => {
      const [, bundleResult] = await Promise.all([
        runSilent('pnpm', ['build'], { cwd: ROOT })
          .then(() => runSilent('pnpm', ['build'], { cwd: path.join(ROOT, 'extension') })),
        runSilent('node', ['tools/prepare_backend_bundle.cjs'], { cwd: ROOT })
          .then(r => ({ cached: /cached/.test(r.out) }))
          .catch(e => { throw e; }),
      ]);
      const suffix = bundleResult?.cached ? pc.dim('  Python 运行时已缓存') : '';
      sp.done(`前端构建 + Python 运行时准备${suffix}`);
    });
  }

  // 3. Bundle 校验（始终执行）
  await runStep('Bundle 完整性校验', async (sp) => {
    await runSilent('node', ['tools/verify_backend_bundle.cjs'], { cwd: ROOT });
    sp.done();
  });

  // 4. electron-builder（顺序执行各架构）
  for (const arch of archList) {
    await runElectronBuilder(arch, flags.dir).catch((e) => {
      w(pc.red(e.message) + '\n');
      process.exit(1);
    });
  }

  // 完成摘要
  const total = fmtMs(Date.now() - t0);
  w(`\n  ${'─'.repeat(46)}\n`);
  w(`  ${pc.green('✓')}  ${pc.bold('全部完成')}  ${pc.dim(total)}\n\n`);

  if (!flags.dir) {
    const releaseDir = path.join(ROOT, 'release');
    const dmgNames = {
      arm64: `Easy RPA-${PKG_VERSION}-arm64.dmg`,
      x64: `Easy RPA-${PKG_VERSION}.dmg`,
    };
    for (const arch of archList) {
      const name = dmgNames[arch];
      const size = fileSize(path.join(releaseDir, name));
      const label = arch === 'arm64' ? 'arm64' : 'x64  ';
      const sizeStr = size ? pc.dim(`  ${fmtBytes(size)}`) : '';
      w(`     ${label}  ${pc.dim('→')}  release/${name}${sizeStr}\n`);
    }
  } else {
    const archDir = archList[0] === 'arm64' ? 'release/mac-arm64' : 'release/mac';
    w(`     app  ${pc.dim('→')}  ${archDir}/\n`);
  }
  w('\n');
}

main();
