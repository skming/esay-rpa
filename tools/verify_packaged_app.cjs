#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

function normalizeArch(machine) {
  const value = String(machine).trim().toLowerCase();
  if (value === 'arm64' || value === 'aarch64') return 'arm64';
  if (value === 'x64' || value === 'x86_64' || value === 'amd64') return 'x64';
  return value;
}

function assertExists(filePath, label) {
  if (!fs.existsSync(filePath)) throw new Error(`${label} 不存在：${filePath}`);
}

function resolveLayout({ appOutDir, appName, executableName = appName, platform }) {
  const resolvedAppOutDir = path.resolve(appOutDir);
  if (platform === 'darwin') {
    const appPath = path.join(resolvedAppOutDir, `${appName}.app`);
    return {
      appPath,
      electronExecutable: path.join(appPath, 'Contents', 'MacOS', appName),
      resourcesDir: path.join(appPath, 'Contents', 'Resources'),
    };
  }
  return {
    appPath: resolvedAppOutDir,
    electronExecutable: path.join(
      resolvedAppOutDir,
      platform === 'win32' ? `${executableName}.exe` : executableName,
    ),
    resourcesDir: path.join(resolvedAppOutDir, 'resources'),
  };
}

function resolvePython(backendDir, platform) {
  const candidates = platform === 'win32'
    ? [path.join(backendDir, 'python', 'python.exe')]
    : [
      path.join(backendDir, 'python', 'bin', 'python3.12'),
      path.join(backendDir, 'python', 'bin', 'python3'),
      path.join(backendDir, 'python', 'bin', 'python'),
    ];
  return candidates.find((candidate) => fs.existsSync(candidate)) ?? candidates[0];
}

function resolveSitePackages(backendDir, platform) {
  if (platform === 'win32') return path.join(backendDir, '.venv', 'Lib', 'site-packages');
  const libDir = path.join(backendDir, '.venv', 'lib');
  if (!fs.existsSync(libDir)) return path.join(libDir, 'python3.12', 'site-packages');
  const pythonDir = fs.readdirSync(libDir).find((entry) => entry.startsWith('python')) ?? 'python3.12';
  return path.join(libDir, pythonDir, 'site-packages');
}

function collectNativeLibraries(rootDir) {
  const found = [];
  const visit = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const entryPath = path.join(dir, entry.name);
      if (entry.isDirectory()) visit(entryPath);
      else if (entry.isFile() && (entry.name.endsWith('.so') || entry.name.endsWith('.dylib'))) found.push(entryPath);
    }
  };
  if (fs.existsSync(rootDir)) visit(rootDir);
  return found;
}

function verifyMachOArchitectures(paths, expectedArch) {
  for (const filePath of paths) {
    const result = spawnSync('lipo', ['-archs', filePath], { encoding: 'utf8' });
    if (result.status !== 0) {
      throw new Error(`无法读取 Mach-O 架构：${filePath}\n${result.stderr || result.stdout}`);
    }
    const architectures = result.stdout.trim().split(/\s+/).map(normalizeArch);
    if (!architectures.includes(expectedArch)) {
      throw new Error(`二进制架构不匹配：${filePath}\n期望 ${expectedArch}，实际 ${result.stdout.trim()}`);
    }
  }
}

function runPython(pythonExecutable, args, options) {
  const result = spawnSync(pythonExecutable, args, {
    ...options,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (result.error) throw new Error(`无法启动随包 Python\n${result.error.message}`);
  if (result.status !== 0) {
    throw new Error(`随包 Python 校验失败\n${result.stderr || result.stdout}`);
  }
  return result.stdout.trim();
}

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      server.close((error) => {
        if (error) reject(error);
        else resolve(address.port);
      });
    });
  });
}

function requestHealth(port) {
  return new Promise((resolve) => {
    const request = http.get({ host: '127.0.0.1', port, path: '/api/health', timeout: 1000 }, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(false));
  });
}

async function stopProcess(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  await new Promise((resolve) => {
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      resolve();
    }, 3000);
    child.once('exit', () => {
      clearTimeout(timer);
      resolve();
    });
    child.kill('SIGTERM');
  });
}

async function verifyHealth({ backendDir, env, pythonExecutable }) {
  const port = await reservePort();
  const child = spawn(pythonExecutable, [
    '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port), '--log-level', 'warning',
  ], {
    cwd: backendDir,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  const append = (chunk) => { output = `${output}${String(chunk)}`.slice(-32_000); };
  child.stdout.on('data', append);
  child.stderr.on('data', append);

  try {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      if (child.exitCode !== null || child.signalCode !== null) {
        throw new Error(`随包后端在健康检查前退出\n${output.trim()}`);
      }
      if (await requestHealth(port)) return;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    throw new Error(`随包后端健康检查超时\n${output.trim()}`);
  } finally {
    await stopProcess(child);
  }
}

async function verifyPackagedApp({ appName, appOutDir, executableName = appName, expectedArch, platform }) {
  const arch = normalizeArch(expectedArch);
  if (!['arm64', 'x64'].includes(arch)) throw new Error(`不支持的目标架构：${expectedArch}`);

  const layout = resolveLayout({ appName, appOutDir, executableName, platform });
  const backendDir = path.join(layout.resourcesDir, 'backend');
  const pythonExecutable = resolvePython(backendDir, platform);
  const sitePackages = resolveSitePackages(backendDir, platform);
  assertExists(layout.electronExecutable, 'Electron 主程序');
  assertExists(path.join(backendDir, 'app', 'main.py'), '后端入口');
  assertExists(path.join(backendDir, 'config', 'model_catalog.json'), 'AI 模型目录');
  assertExists(path.join(layout.resourcesDir, 'extension', 'manifest.json'), '浏览器扩展 manifest');
  assertExists(pythonExecutable, '随包 Python');
  assertExists(sitePackages, '随包生产依赖');

  if (platform === 'darwin') {
    verifyMachOArchitectures([
      layout.electronExecutable,
      pythonExecutable,
      ...collectNativeLibraries(path.join(backendDir, 'python')),
      ...collectNativeLibraries(sitePackages),
    ], arch);
  }

  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'easy-rpa-packaged-verify-'));
  const env = {
    ...process.env,
    RPA_APP_DATA_DIR: tempDir,
    RPA_WORKSPACE_ROOT: path.join(tempDir, 'workspace'),
    RPA_LOG_DIR: path.join(tempDir, 'logs'),
    RPA_CACHE_DIR: path.join(tempDir, 'cache'),
    PLAYWRIGHT_BROWSERS_PATH: path.join(tempDir, 'playwright-browsers'),
    PYTHONPATH: [sitePackages, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    PYTHONUNBUFFERED: '1',
  };

  try {
    const actualArch = normalizeArch(runPython(
      pythonExecutable,
      ['-c', 'import platform; print(platform.machine())'],
      { cwd: backendDir, env },
    ));
    if (actualArch !== arch) throw new Error(`随包 Python 架构不匹配：期望 ${arch}，实际 ${actualArch}`);
    runPython(pythonExecutable, ['-c', 'import app.main; print("ok")'], { cwd: backendDir, env });
    await verifyHealth({ backendDir, env, pythonExecutable });
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

module.exports = { normalizeArch, resolveLayout, verifyPackagedApp };
