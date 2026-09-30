#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const pc = require('picocolors');
const { w, IS_TTY, makeSpinner } = require('./lib/ui.cjs');

const ROOT = path.resolve(__dirname, '..');
const BACKEND_DIR = path.join(ROOT, 'backend');
const VENV_DIR = process.env.RPA_BACKEND_BUNDLE_VENV
  ? path.resolve(process.env.RPA_BACKEND_BUNDLE_VENV)
  : path.join(BACKEND_DIR, '.bundle-venv');
const PYTHON_RUNTIME_DIR = process.env.RPA_BACKEND_BUNDLE_PYTHON
  ? path.resolve(process.env.RPA_BACKEND_BUNDLE_PYTHON)
  : path.join(BACKEND_DIR, '.bundle-python');

function fail(message, details = {}) {
  if (IS_TTY) {
    w(`  ${pc.red('✗')}  ${message}\n`);
    for (const [k, v] of Object.entries(details)) {
      if (Array.isArray(v)) v.forEach(l => w(`     ${pc.dim(l)}\n`));
      else w(`     ${pc.dim(k + ': ' + v)}\n`);
    }
  } else {
    process.stderr.write(JSON.stringify({ ok: false, message, ...details }, null, 2) + '\n');
  }
  process.exit(1);
}

function assertExists(filePath, label) {
  if (!fs.existsSync(filePath)) fail(`${label} 不存在`, { path: filePath });
}

function normalizeArch(machine) {
  const value = String(machine).trim().toLowerCase();
  if (value === 'arm64' || value === 'aarch64') return 'arm64';
  if (value === 'x64' || value === 'x86_64' || value === 'amd64') return 'x64';
  return value;
}

function findAbsoluteSymlinks(rootDir) {
  const found = [];
  const visit = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const entryPath = path.join(dir, entry.name);
      if (entry.isSymbolicLink()) {
        const target = fs.readlinkSync(entryPath);
        if (path.isAbsolute(target)) found.push(`${entryPath} -> ${target}`);
      } else if (entry.isDirectory()) {
        visit(entryPath);
      }
    }
  };
  if (fs.existsSync(rootDir)) visit(rootDir);
  return found;
}

function resolveSitePackages() {
  if (process.platform === 'win32') return path.join(VENV_DIR, 'Lib', 'site-packages');
  const libDir = path.join(VENV_DIR, 'lib');
  if (!fs.existsSync(libDir)) return path.join(VENV_DIR, 'lib', 'python3.12', 'site-packages');
  const pythonDirs = fs.readdirSync(libDir).filter(e => e.startsWith('python')).sort();
  const ver = pythonDirs.find(e => e === 'python3.12') || pythonDirs[0] || 'python3.12';
  return path.join(libDir, ver, 'site-packages');
}

function main() {
  const sp = IS_TTY ? makeSpinner('Bundle 完整性校验') : null;

  const bundledPython = process.platform === 'win32'
    ? path.join(PYTHON_RUNTIME_DIR, 'python.exe')
    : path.join(PYTHON_RUNTIME_DIR, 'bin', 'python3.12');
  const sitePackages = resolveSitePackages();

  assertExists(path.join(BACKEND_DIR, 'app', 'main.py'),              '后端入口 app/main.py');
  assertExists(path.join(BACKEND_DIR, 'config', 'model_catalog.json'), 'AI 模型目录 config/model_catalog.json');
  assertExists(path.join(BACKEND_DIR, 'pyproject.toml'),               '后端 pyproject.toml');
  assertExists(bundledPython, '可随包 Python 运行时');
  assertExists(sitePackages,  '后端依赖 site-packages');

  const absoluteRuntimeSymlinks = findAbsoluteSymlinks(PYTHON_RUNTIME_DIR);
  if (absoluteRuntimeSymlinks.length > 0) {
    fail('Python 运行时包含绝对路径软链接，复制到其他机器后会失效', {
      links: absoluteRuntimeSymlinks.slice(0, 20),
      mitigation: '重新执行 pnpm backend:bundle:prepare',
    });
  }

  const absoluteDependencySymlinks = findAbsoluteSymlinks(sitePackages);
  if (absoluteDependencySymlinks.length > 0) {
    fail('生产依赖包含绝对路径软链接，复制到其他机器后会失效', {
      links: absoluteDependencySymlinks.slice(0, 20),
      mitigation: '重新执行 pnpm backend:bundle:prepare',
    });
  }

  const CHECKED_IMPORTS = [
    'fastapi', 'uvicorn', 'playwright', 'scrapling', 'openpyxl',
    'litellm', 'sqlalchemy', 'redis', 'minio', 'jsonschema', 'asyncpg', 'app.main',
  ];
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'easy-rpa-bundle-verify-'));
  const env = {
    ...process.env,
    RPA_APP_DATA_DIR: tempDir,
    RPA_WORKSPACE_ROOT: path.join(tempDir, 'workspace'),
    RPA_LOG_DIR: path.join(tempDir, 'logs'),
    RPA_CACHE_DIR: path.join(tempDir, 'cache'),
    PYTHONPATH: [sitePackages, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
  };
  const archResult = spawnSync(
    bundledPython,
    ['-c', 'import platform; print(platform.machine())'],
    { cwd: BACKEND_DIR, env, encoding: 'utf8' },
  );
  if (archResult.status !== 0 || normalizeArch(archResult.stdout) !== process.arch) {
    fs.rmSync(tempDir, { recursive: true, force: true });
    fail('Python 运行时架构与构建主机不一致', {
      expected: process.arch,
      actual: archResult.stdout.trim() || 'unknown',
      stderr: archResult.stderr.trim(),
    });
  }
  const result = spawnSync(
    bundledPython,
    ['-c', `import ${CHECKED_IMPORTS.join(', ')}; print("ok")`],
    { cwd: BACKEND_DIR, env, encoding: 'utf8' },
  );
  fs.rmSync(tempDir, { recursive: true, force: true });

  if (result.status !== 0) {
    if (sp) sp.fail();
    fail('后端依赖导入检查失败', {
      python: bundledPython,
      stderr: result.stderr.trim(),
    });
  }

  const summary = {
    ok: true,
    backendDir: BACKEND_DIR,
    venvDir: VENV_DIR,
    pythonRuntimeDir: PYTHON_RUNTIME_DIR,
    bundledPython,
    sitePackages,
    arch: process.arch,
    checkedImports: CHECKED_IMPORTS,
  };

  if (IS_TTY) {
    sp.done(`Bundle 校验通过  ${CHECKED_IMPORTS.map(m => pc.dim(m)).join(' ')}`);
  } else {
    process.stdout.write(JSON.stringify(summary, null, 2) + '\n');
  }
}

main();
