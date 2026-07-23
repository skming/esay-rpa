'use strict';

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const pc = require('picocolors');

const IS_TTY = process.stdout.isTTY;
const FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

function w(s) { process.stdout.write(s); }

function fmtMs(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m${s % 60 ? (s % 60) + 's' : ''}`;
}

function fmtBytes(b) {
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`;
  if (b >= 1e6) return `${Math.round(b / 1e6)} MB`;
  if (b >= 1e3) return `${Math.round(b / 1e3)} KB`;
  return `${b} B`;
}

function fileSize(p) {
  try { return fs.statSync(p).size; } catch { return 0; }
}

// 取最后 N 行，用于错误截断
function tailLines(text, n = 15) {
  const lines = (text || '').trim().split('\n');
  return (lines.length > n ? ['…', ...lines.slice(-n)] : lines).join('\n');
}

// ── Spinner ───────────────────────────────────────────────────────────
function makeSpinner(label) {
  let idx = 0, timer = null;
  const t0 = Date.now();

  const draw = () => {
    if (!IS_TTY) return;
    w(`\x1b[2K\x1b[G  ${pc.cyan(FRAMES[idx++ % FRAMES.length])}  ${label}`);
  };

  draw();
  if (IS_TTY) timer = setInterval(draw, 80);

  return {
    done(msg) {
      clearInterval(timer);
      if (IS_TTY) w('\x1b[2K\x1b[G');
      w(`  ${pc.green('✓')}  ${msg ?? label}  ${pc.dim(fmtMs(Date.now() - t0))}\n`);
    },
    fail(msg) {
      clearInterval(timer);
      if (IS_TTY) w('\x1b[2K\x1b[G');
      w(`  ${pc.red('✗')}  ${msg ?? label}\n`);
    },
    update(newLabel) { label = newLabel; },
  };
}

// ── 子步骤 Spinner（用于 electron-builder 内部进度）──────────────────
function makeSubSpinner() {
  let idx = 0, timer = null, label = '', t0 = null, active = false;

  const draw = () => {
    if (!IS_TTY || !active) return;
    w(`\x1b[2K\x1b[G     ${pc.cyan(FRAMES[idx++ % FRAMES.length])}  ${label}...`);
  };

  return {
    start(name) {
      if (timer) { clearInterval(timer); timer = null; }
      label = name; t0 = Date.now(); active = true;
      if (IS_TTY) { draw(); timer = setInterval(draw, 80); }
      else w(`     ${pc.dim('·')}  ${name}...\n`);
    },
    finish(nameOverride) {
      if (!active) return;
      clearInterval(timer); timer = null; active = false;
      const t = t0 ? fmtMs(Date.now() - t0) : '';
      if (IS_TTY) w('\x1b[2K\x1b[G');
      w(`     ${pc.green('✓')}  ${nameOverride ?? label}  ${pc.dim(t)}\n`);
    },
    updateLabel(newLabel) { label = newLabel; },
    active() { return active; },
  };
}

// ── 运行命令（捕获输出）──────────────────────────────────────────────
function runSilent(cmd, args, { cwd, env = {} } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      cwd,
      env: { ...process.env, ...env },
      stdio: ['ignore', 'pipe', 'pipe'],
      shell: process.platform === 'win32',
    });
    let out = '', err = '';
    child.stdout.on('data', d => (out += d));
    child.stderr.on('data', d => (err += d));
    child.on('close', code => {
      if (code === 0) resolve({ out, err });
      else reject(Object.assign(new Error(err || out || `exit ${code}`), { out, err }));
    });
  });
}

// ── 带 spinner 的步骤包装 ─────────────────────────────────────────────
async function runStep(label, fn) {
  const sp = makeSpinner(label);
  try {
    return await fn(sp);
  } catch (e) {
    sp.fail();
    const detail = tailLines(e.err || e.message || String(e));
    if (detail) w(pc.red(detail) + '\n');
    process.exit(1);
  }
}

module.exports = {
  w, IS_TTY, FRAMES,
  fmtMs, fmtBytes, fileSize, tailLines,
  makeSpinner, makeSubSpinner,
  runSilent, runStep,
};
