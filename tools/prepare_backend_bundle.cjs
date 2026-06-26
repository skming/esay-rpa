#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const pc = require('picocolors');
const { w, IS_TTY, makeSpinner } = require('./lib/ui.cjs');

const ROOT = path.resolve(__dirname, '..');
const BACKEND_DIR = path.join(ROOT, 'backend');
const TARGET_DIR = path.join(BACKEND_DIR, '.bundle-python');
const STAMP_FILE = path.join(TARGET_DIR, '.build-stamp.json');
const PYTHON_VERSION = process.env.RPA_BUNDLE_PYTHON_VERSION || '3.12.10';
const IS_WIN = process.platform === 'win32';

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: ROOT,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    ...options,
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} 失败\n${result.stderr || result.stdout}`);
  }
  return result.stdout.trim();
}

function copyDir(src, dest) {
  fs.rmSync(dest, { recursive: true, force: true });
  fs.cpSync(src, dest, {
    recursive: true,
    dereference: true,
    filter: (source) => {
      const base = path.basename(source);
      return base !== '__pycache__' && base !== '.pytest_cache'
        && !base.endsWith('.pyc') && !base.endsWith('.pyo');
    },
  });
}

function normalizePythonSymlinks(runtimeDir) {
  const binDir = path.join(runtimeDir, 'bin');
  for (const name of ['2to3', 'idle3', 'pydoc3', 'python3-config']) {
    fs.rmSync(path.join(binDir, name), { force: true });
  }
  for (const [name, target] of [['python', 'python3.12'], ['python3', 'python3.12']]) {
    const linkPath = path.join(binDir, name);
    if (!fs.existsSync(linkPath)) continue;
    fs.rmSync(linkPath, { force: true });
    fs.symlinkSync(target, linkPath);
  }
  for (const filePath of [
    path.join(runtimeDir, 'lib', 'pkgconfig', 'python3.pc'),
    path.join(runtimeDir, 'lib', 'pkgconfig', 'python3-embed.pc'),
    path.join(runtimeDir, 'share', 'man', 'man1', 'python3.1'),
  ]) {
    fs.rmSync(filePath, { force: true });
  }
}

// 检查是否已有匹配版本的缓存
function isAlreadyBuilt() {
  try {
    const stamp = JSON.parse(fs.readFileSync(STAMP_FILE, 'utf8'));
    return stamp.pythonVersion === PYTHON_VERSION && stamp.platform === process.platform;
  } catch {
    return false;
  }
}

function writeStamp(info) {
  fs.writeFileSync(STAMP_FILE, JSON.stringify({
    pythonVersion: PYTHON_VERSION,
    platform: process.platform,
    builtAt: new Date().toISOString(),
    ...info,
  }, null, 2));
}

function main() {
  const uvCmd = IS_WIN ? 'uv.exe' : 'uv';

  // 如果已缓存且版本匹配，跳过耗时的复制步骤
  if (isAlreadyBuilt()) {
    const msg = `Python ${PYTHON_VERSION} 运行时已缓存，跳过重新准备`;
    if (IS_TTY) w(`  ${pc.dim('·')}  ${pc.dim(msg)}\n`);
    else process.stdout.write(JSON.stringify({ ok: true, cached: true, pythonVersion: PYTHON_VERSION }) + '\n');
    return;
  }

  let sp;
  if (IS_TTY) {
    sp = makeSpinner(`准备 Python ${PYTHON_VERSION} 运行时`);
  }

  try {
    // 安装 & 定位
    run(uvCmd, ['python', 'install', PYTHON_VERSION]);
    const pythonPath = run(uvCmd, ['python', 'find', PYTHON_VERSION]);

    const pythonRoot = IS_WIN
      ? path.dirname(pythonPath)
      : path.dirname(path.dirname(pythonPath));
    const realPython = fs.realpathSync(pythonPath);

    if (IS_TTY) sp.update(`复制 Python ${PYTHON_VERSION} 运行时...`);

    copyDir(pythonRoot, TARGET_DIR);
    if (!IS_WIN) normalizePythonSymlinks(TARGET_DIR);

    const bundledPython = IS_WIN
      ? path.join(TARGET_DIR, 'python.exe')
      : path.join(TARGET_DIR, 'bin', 'python3.12');

    // 自检
    const check = spawnSync(bundledPython, ['-c', 'import sys; print(sys.version)'], { encoding: 'utf8' });
    if (check.status !== 0) throw new Error(`打包 Python 自检失败\n${check.stderr}`);

    writeStamp({ bundledPython });

    const result = {
      ok: true,
      cached: false,
      platform: process.platform,
      pythonVersion: PYTHON_VERSION,
      sourceRoot: pythonRoot,
      sourcePython: pythonPath,
      realPython,
      targetDir: TARGET_DIR,
      bundledPython,
      version: check.stdout.trim(),
    };

    if (IS_TTY) {
      sp.done(`Python ${PYTHON_VERSION} 运行时就绪`);
    } else {
      process.stdout.write(JSON.stringify(result, null, 2) + '\n');
    }
  } catch (e) {
    if (IS_TTY) {
      sp.fail(`Python 运行时准备失败`);
      w(pc.red(e.message) + '\n');
    } else {
      process.stderr.write(JSON.stringify({ ok: false, message: e.message }) + '\n');
    }
    process.exit(1);
  }
}

main();
