#!/usr/bin/env node
'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const pc = require('picocolors');
const { w, IS_TTY, makeSpinner } = require('./lib/ui.cjs');

const ROOT = path.resolve(__dirname, '..');
const BACKEND_DIR = path.join(ROOT, 'backend');
const RUNTIME_DIR = path.join(BACKEND_DIR, '.bundle-python');
const VENV_DIR = path.join(BACKEND_DIR, '.bundle-venv');
const STAMP_FILE = path.join(VENV_DIR, '.build-stamp.json');
const LOCK_FILE = path.join(BACKEND_DIR, 'uv.lock');
const PROJECT_FILE = path.join(BACKEND_DIR, 'pyproject.toml');
const PYTHON_VERSION = process.env.RPA_BUNDLE_PYTHON_VERSION || '3.12';
const BUNDLE_SCHEMA_VERSION = 1;
const IS_WIN = process.platform === 'win32';

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: ROOT,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    ...options,
  });
  if (result.error) {
    throw new Error(`${command} 启动失败\n${result.error.message}`);
  }
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} 失败\n${result.stderr || result.stdout}`);
  }
  return result.stdout.trim();
}

function hashFile(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function normalizeArch(machine) {
  const value = String(machine).trim().toLowerCase();
  if (value === 'arm64' || value === 'aarch64') return 'arm64';
  if (value === 'x64' || value === 'x86_64' || value === 'amd64') return 'x64';
  return value;
}

function bundledPythonPath() {
  return IS_WIN
    ? path.join(RUNTIME_DIR, 'python.exe')
    : path.join(RUNTIME_DIR, 'bin', 'python3.12');
}

function sitePackagesPath() {
  if (IS_WIN) return path.join(VENV_DIR, 'Lib', 'site-packages');
  return path.join(VENV_DIR, 'lib', 'python3.12', 'site-packages');
}

function readPythonInfo(pythonExecutable) {
  const output = run(pythonExecutable, [
    '-c',
    'import json, platform, sys; print(json.dumps({"arch": platform.machine(), "version": platform.python_version(), "executable": sys.executable}))',
  ]);
  return JSON.parse(output);
}

function expectedFingerprint() {
  return {
    bundleSchemaVersion: BUNDLE_SCHEMA_VERSION,
    platform: process.platform,
    arch: process.arch,
    pythonVersion: PYTHON_VERSION,
    lockHash: hashFile(LOCK_FILE),
    projectHash: hashFile(PROJECT_FILE),
  };
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

function isAlreadyBuilt(fingerprint) {
  try {
    const stamp = JSON.parse(fs.readFileSync(STAMP_FILE, 'utf8'));
    for (const [key, value] of Object.entries(fingerprint)) {
      if (stamp[key] !== value) return false;
    }
    if (!stamp.sourceRoot || !fs.existsSync(stamp.sourceRoot)) return false;
    const pythonExecutable = bundledPythonPath();
    if (!fs.existsSync(pythonExecutable) || !fs.existsSync(sitePackagesPath())) return false;
    const info = readPythonInfo(pythonExecutable);
    return normalizeArch(info.arch) === process.arch && info.version === stamp.exactPythonVersion;
  } catch {
    return false;
  }
}

function writeStamp(info) {
  fs.writeFileSync(STAMP_FILE, JSON.stringify({
    ...expectedFingerprint(),
    builtAt: new Date().toISOString(),
    ...info,
  }, null, 2));
}

function main() {
  const uvCmd = IS_WIN ? 'uv.exe' : 'uv';
  const fingerprint = expectedFingerprint();

  if (isAlreadyBuilt(fingerprint)) {
    const msg = `Python ${PYTHON_VERSION} ${process.arch} 生产 Bundle 已缓存`;
    if (IS_TTY) w(`  ${pc.dim('·')}  ${pc.dim(msg)}\n`);
    else process.stdout.write(JSON.stringify({ ok: true, cached: true, ...fingerprint }) + '\n');
    return;
  }

  const sp = IS_TTY ? makeSpinner(`准备 Python ${PYTHON_VERSION} ${process.arch} 生产 Bundle`) : null;

  try {
    const uvEnv = { ...process.env, UV_PYTHON_PREFERENCE: 'only-managed' };
    run(uvCmd, ['python', 'install', PYTHON_VERSION], { env: uvEnv });
    const pythonPath = run(uvCmd, ['python', 'find', PYTHON_VERSION], { env: uvEnv });
    const pythonRoot = IS_WIN ? path.dirname(pythonPath) : path.dirname(path.dirname(pythonPath));

    if (sp) sp.update(`复制 Python ${PYTHON_VERSION} ${process.arch} 运行时...`);
    copyDir(pythonRoot, RUNTIME_DIR);
    if (!IS_WIN) normalizePythonSymlinks(RUNTIME_DIR);

    const pythonExecutable = bundledPythonPath();
    const pythonInfo = readPythonInfo(pythonExecutable);
    const runtimeArch = normalizeArch(pythonInfo.arch);
    if (runtimeArch !== process.arch) {
      throw new Error(`Python 运行时架构不匹配：主机 ${process.arch}，运行时 ${runtimeArch}`);
    }

    if (sp) sp.update('安装锁定的生产依赖...');
    fs.rmSync(VENV_DIR, { recursive: true, force: true });
    run(uvCmd, [
      'sync',
      '--project', BACKEND_DIR,
      '--frozen',
      '--no-dev',
      '--no-install-project',
      '--link-mode', 'copy',
      '--python', pythonExecutable,
    ], {
      env: {
        ...uvEnv,
        UV_PROJECT_ENVIRONMENT: VENV_DIR,
      },
    });

    if (!fs.existsSync(sitePackagesPath())) {
      throw new Error(`生产依赖目录不存在：${sitePackagesPath()}`);
    }

    writeStamp({
      exactPythonVersion: pythonInfo.version,
      sourceRoot: pythonRoot,
      sourcePython: pythonPath,
      bundledPython: pythonExecutable,
      sitePackages: sitePackagesPath(),
    });

    const result = {
      ok: true,
      cached: false,
      ...fingerprint,
      exactPythonVersion: pythonInfo.version,
      sourceRoot: pythonRoot,
      targetRuntimeDir: RUNTIME_DIR,
      targetVenvDir: VENV_DIR,
      bundledPython: pythonExecutable,
      sitePackages: sitePackagesPath(),
    };

    if (sp) sp.done(`Python ${pythonInfo.version} ${process.arch} 生产 Bundle 就绪`);
    else process.stdout.write(JSON.stringify(result, null, 2) + '\n');
  } catch (error) {
    if (sp) {
      sp.fail('Python 生产 Bundle 准备失败');
      w(pc.red(error.message) + '\n');
    } else {
      process.stderr.write(JSON.stringify({ ok: false, message: error.message }) + '\n');
    }
    process.exit(1);
  }
}

if (require.main === module) main();

module.exports = { expectedFingerprint, isAlreadyBuilt, normalizeArch };
