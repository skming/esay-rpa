#!/usr/bin/env node
'use strict';

/**
 * 交互式版本号升级工具。
 * 用法：node tools/bump-version.cjs
 */

const fs = require('node:fs');
const path = require('node:path');
const pc = require('picocolors');
const prompts = require('prompts');
const { w } = require('./lib/ui.cjs');

const PKG_PATH = path.resolve(__dirname, '..', 'package.json');

function parseSemver(v) {
  const m = v.replace(/^v/, '').match(/^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$/);
  if (!m) throw new Error(`无法解析版本号: ${v}`);
  return { major: +m[1], minor: +m[2], patch: +m[3], pre: m[4] ?? null };
}

function bumpVersion(current, type) {
  const { major, minor, patch } = parseSemver(current);
  if (type === 'major') return `${major + 1}.0.0`;
  if (type === 'minor') return `${major}.${minor + 1}.0`;
  if (type === 'patch') return `${major}.${minor}.${patch + 1}`;
  throw new Error(`未知 bump 类型: ${type}`);
}

async function main() {
  const pkg = JSON.parse(fs.readFileSync(PKG_PATH, 'utf8'));
  const current = pkg.version;

  w(`\n  ${pc.bold('Easy RPA')}  ${pc.dim('→')}  版本管理\n`);
  w(`  ${'─'.repeat(40)}\n\n`);
  w(`  当前版本  ${pc.bold(pc.cyan('v' + current))}\n\n`);

  const nextPatch = bumpVersion(current, 'patch');
  const nextMinor = bumpVersion(current, 'minor');
  const nextMajor = bumpVersion(current, 'major');

  const { bumpType } = await prompts({
    type: 'select',
    name: 'bumpType',
    message: '选择版本升级类型',
    choices: [
      { title: `patch  ${pc.dim(current + ' → ')}${pc.green(nextPatch)}  ${pc.dim('（缺陷修复）')}`,  value: 'patch'  },
      { title: `minor  ${pc.dim(current + ' → ')}${pc.yellow(nextMinor)}  ${pc.dim('（新功能，向下兼容）')}`, value: 'minor' },
      { title: `major  ${pc.dim(current + ' → ')}${pc.red(nextMajor)}  ${pc.dim('（破坏性变更）')}`,   value: 'major'  },
      { title: `custom  ${pc.dim('手动输入')}`, value: 'custom' },
    ],
    initial: 0,
  });

  if (!bumpType) { w(pc.dim('\n  已取消。\n\n')); process.exit(0); }

  let nextVersion;
  if (bumpType === 'custom') {
    const { custom } = await prompts({
      type: 'text',
      name: 'custom',
      message: '输入新版本号',
      validate: (v) => {
        try { parseSemver(v); return true; }
        catch { return '请输入有效的 semver 版本号，如 1.2.3'; }
      },
    });
    if (!custom) { w(pc.dim('\n  已取消。\n\n')); process.exit(0); }
    nextVersion = custom.replace(/^v/, '');
  } else {
    nextVersion = bumpVersion(current, bumpType);
  }

  w(`\n  升级预览  ${pc.dim('v' + current)}  ${pc.dim('→')}  ${pc.bold(pc.green('v' + nextVersion))}\n\n`);

  const { confirm } = await prompts({
    type: 'confirm',
    name: 'confirm',
    message: `确认写入 package.json？`,
    initial: true,
  });

  if (!confirm) { w(pc.dim('\n  已取消。\n\n')); process.exit(0); }

  pkg.version = nextVersion;
  fs.writeFileSync(PKG_PATH, JSON.stringify(pkg, null, 2) + '\n', 'utf8');

  w(`\n  ${pc.green('✓')}  package.json 已更新  ${pc.dim('v' + current + ' → v' + nextVersion)}\n`);
  w(`\n  ${'─'.repeat(40)}\n`);
  w(`  下一步：\n\n`);
  w(`     ${pc.dim('$')} pnpm electron:dist          ${pc.dim('# 打包新版本')}\n`);
  w(`\n`);
}

main().catch((e) => {
  process.stderr.write(pc.red(e.message) + '\n');
  process.exit(1);
});
