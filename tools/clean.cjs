#!/usr/bin/env node
'use strict';

/**
 * 清理打包产物。
 *
 * 用法：
 *   node tools/clean.cjs           # 清理 release/ + dist/
 *   node tools/clean.cjs --release # 仅清理 release/
 *   node tools/clean.cjs --dist    # 仅清理 dist/
 */

const fs = require('node:fs');
const path = require('node:path');
const pc = require('picocolors');
const { w, fmtBytes } = require('./lib/ui.cjs');

const ROOT = path.resolve(__dirname, '..');

const flags = {
  release: process.argv.includes('--release'),
  dist:    process.argv.includes('--dist'),
};
// 不带参数时清全部
const cleanAll = !flags.release && !flags.dist;

function dirSize(dir) {
  let total = 0;
  try {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) total += dirSize(full);
      else { try { total += fs.statSync(full).size; } catch {} }
    }
  } catch {}
  return total;
}

function remove(targetPath, label) {
  if (!fs.existsSync(targetPath)) {
    w(`  ${pc.dim('·')}  ${label}  ${pc.dim('(不存在，跳过)')}\n`);
    return;
  }
  const size = dirSize(targetPath);
  fs.rmSync(targetPath, { recursive: true, force: true });
  w(`  ${pc.green('✓')}  ${label}  ${pc.dim(fmtBytes(size) + ' 已清理')}\n`);
}

w(`\n  ${pc.bold('Easy RPA')}  ${pc.dim('→')}  清理打包产物\n`);
w(`  ${'─'.repeat(46)}\n\n`);

if (cleanAll || flags.release) remove(path.join(ROOT, 'release'), 'release/');
if (cleanAll || flags.dist)    remove(path.join(ROOT, 'dist'),    'dist/');

w('\n');
