import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** button.tsx 与 badge.tsx 是 vendored 源码，不改，几处对比度不足只能由 styles.css 的
 *  无 layer 规则按类名签名压掉。断点很安静：上游把 bg-red-500 换成 bg-destructive，
 *  选择器就再也命中不了——不报错、不变形，白字重新掉回 3.82:1。
 *  所以钉的不是颜色，而是「覆盖还接得上」：两侧任何一边改了名字都要在这里红。 */

const read = (rel: string): string =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf-8');

const BUTTON = read('./button.tsx');
const BADGE = read('./badge.tsx');
const STYLES = read('../../styles.css');

/** 从 cva 变体表里取出某个变体的类名串。 */
const variant = (src: string, name: string): string => {
  const m = new RegExp(`\\b${name}:\\s*'([^']*)'`).exec(src);
  expect(m, `变体 ${name} 不在表里了`).not.toBeNull();
  return m?.[1] ?? '';
};

describe('vendored primitive 的对比度覆盖', () => {
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
