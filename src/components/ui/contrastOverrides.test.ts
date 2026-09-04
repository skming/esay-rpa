import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** button.tsx 与 badge.tsx 的配色写在 cva 变体表里，几处对比度不足由 styles.css 的
 *  无 layer 规则按类名签名压掉。断点很安静：变体表里把 bg-red-500 换成语义 token，
 *  选择器就再也命中不了——不报错、不变形，白字重新掉回 3.82:1。
 *  所以钉的不是颜色，而是「覆盖还接得上」：两侧任何一边改了名字都要在这里红。
 *  签名选择器的依赖方还不止这两个组件——下面两处裸元素不经过 Badge，覆盖规则是它们
 *  唯一的修复路径，所以调用点那侧也一起钉。 */

const read = (rel: string): string =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf-8');

const BUTTON = read('./button.tsx');
const BADGE = read('./badge.tsx');
const STYLES = read('../../styles.css');
const FLOW_NODES = read('../studio/FlowNodes.tsx');
const DASHBOARD = read('../workspace/DashboardPage.tsx');

/** 从 cva 变体表里取出某个变体的类名串。 */
const variant = (src: string, name: string): string => {
  const m = new RegExp(`\\b${name}:\\s*'([^']*)'`).exec(src);
  expect(m, `变体 ${name} 不在表里了`).not.toBeNull();
  return m?.[1] ?? '';
};

describe('配色签名的对比度覆盖', () => {
  it('danger 按钮的签名类还在，styles.css 的三条状态规则也还在', () => {
    const danger = variant(BUTTON, 'danger');
    expect(danger).toContain('bg-red-500');
    expect(danger).toContain('text-white');

    // hover/active 的工具类在 layer 里，会被无 layer 的基础规则吃掉，
    // 所以三条必须成套存在，少一条就是「按下去没反应」
    expect(STYLES).toContain('.bg-red-500.text-white {');
    expect(STYLES).toContain('.bg-red-500.text-white:hover {');
    expect(STYLES).toContain('.bg-red-500.text-white:active {');
  });

  it('Badge 的 default / red 变体签名还在，styles.css 两条覆盖也还在', () => {
    expect(variant(BADGE, 'default')).toContain('bg-slate-100');
    expect(variant(BADGE, 'default')).toContain('text-slate-500');
    expect(variant(BADGE, 'red')).toContain('bg-red-50');
    expect(variant(BADGE, 'red')).toContain('text-red-600');

    expect(STYLES).toContain('.bg-slate-100.text-slate-500 {');
    expect(STYLES).toContain('.bg-red-50.text-red-600 {');
  });

  it('FlowNodes 的状态 pill 还写着这两组签名 —— 它是裸 span，不经过 Badge', () => {
    // 待运行/跳过是 slate-100 底 slate-500 字(4.35)，失败是 red-50 底 red-600 字(4.36)，
    // 各差一档，而这里没有 Badge 可改，styles.css 那两条是唯一的修复路径。
    // 换成语义 token 就静默失配：pill 照样渲染，对比度悄悄掉回去。
    const pills = [...FLOW_NODES.matchAll(/pill:\s*'([^']*)'/g)].map((m) =>
      (m[1] ?? '').split(/\s+/),
    );
    expect(pills.length, 'NODE_STATUS 里没有 pill 类名串了').toBeGreaterThan(0);
    const pairedOn = (bg: string, fg: string): boolean =>
      pills.some((cls) => cls.includes(bg) && cls.includes(fg));

    expect(pairedOn('bg-slate-100', 'text-slate-500'), '待运行/跳过的 pill 改了配色').toBe(true);
    expect(pairedOn('bg-red-50', 'text-red-600'), '失败的 pill 改了配色').toBe(true);
    expect(STYLES).toContain('.bg-slate-100.text-slate-500 {');
    expect(STYLES).toContain('.bg-red-50.text-red-600 {');
  });

  it('DashboardPage 的异常图标还把 surface 与 text 合到同一个元素上', () => {
    // 签名选择器要求两个类落在同一个元素：这里 surface/text 是 ATTENTION_META 的两个键，
    // 靠 cn(..., config.surface, config.text) 才合到一个 span 上。拆成底色一层、图标一层，
    // 选择器立刻不命中——所以合并那一行和配色值要一起钉。
    const metas = [...DASHBOARD.matchAll(/surface:\s*'([^']*)',\s*text:\s*'([^']*)'/g)];
    expect(
      metas.some((m) => m[1] === 'bg-red-50' && m[2] === 'text-red-600'),
      'ATTENTION_META 里 red-50 + red-600 那组不在了',
    ).toBe(true);
    expect(DASHBOARD).toContain('config.surface, config.text');
    expect(STYLES).toContain('.bg-red-50.text-red-600 {');
  });

  it('选择器只挂配色签名，不挂元素名或圆角', () => {
    // 加回 button. / span. / .rounded-full 会重新引入两条静默失配路径：
    // <Button asChild> 把标签换成 <a>，twMerge 让调用点把 rounded-full 换成 rounded-md。
    expect(STYLES).not.toContain('button.bg-red-500');
    expect(STYLES).not.toContain('span.rounded-full');
  });

  it('覆盖规则没有被塞进 @layer —— 进了 layer 就压不住工具类', () => {
    // 必须先剥注释再数：本文件的注释里就在讲 @layer 机制，连着注释一起扫会把散文当成
    // 开着的 layer 块，测试红在一个不存在的问题上。
    const css = STYLES.replace(/\/\*[\s\S]*?\*\//g, '');
    const at = css.indexOf('.bg-red-500.text-white {');
    expect(at).toBeGreaterThan(-1);
    // 该规则之前不能有未闭合的 @layer 块
    const before = css.slice(0, at);
    const opens = (before.match(/@layer[^;{]*\{/g) ?? []).length;
    expect(opens).toBe(0);
  });
});
