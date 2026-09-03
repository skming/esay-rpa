import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** 全站只有一个焦点指示器：styles.css 里那条无 layer 的 2px 实心 accent 轮廓。
 *  组件自己写 focus-visible:outline-none 压不掉它（无 layer 规则赢过所有工具类），
 *  但那行字会让下一个人以为「这里自带焦点样式」——而配套的 ring 清一色是
 *  accent-soft(1.07:1) / rule(1.13) / accent-line(1.65) / accent-40(1.68)，没有一个够 3:1。
 *  所以这里钉的是「还允许在哪里写 outline-none」：清单之外新增一处就红。 */

const SRC = dirname(dirname(dirname(fileURLToPath(import.meta.url))));
const SUPPRESS = /(?:focus|focus-visible):outline-none/;

/** 相对 src/ 的路径 → 这一处为什么必须留着。 */
const ALLOWED: Record<string, string> = {
  'components/ui/tabs.tsx':
    'TabsContent 是 <div tabIndex=0>，不在全局规则的元素清单里，去掉这行 UA 会给整个面板画一个大方框。'
    + '同文件 TabsTrigger 上那处是 shadcn 源码，不改——它的 ring-rule 是死的，靠全局轮廓兜。',
  'components/ui/switch.tsx': 'shadcn 源码，不改。ring-accent-soft 是死的，靠全局轮廓兜。',
  'components/ui/input.tsx': 'shadcn 源码，不改。与同串的基础 outline-none 重复，同样是死的。',
};

const walk = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(dir, e.name)) : e.name.endsWith('.tsx') ? [join(dir, e.name)] : [],
  );

describe('唯一焦点指示器', () => {
  it('只有清单里的文件写 outline-none', () => {
    const offenders = walk(SRC)
      .filter((f) => SUPPRESS.test(readFileSync(f, 'utf-8')))
      .map((f) => relative(SRC, f).split(sep).join('/'))
      .filter((rel) => !(rel in ALLOWED))
      .sort();

    // 新增一处就要么删掉它（全局轮廓已经把焦点画好了），要么进 ALLOWED 并写清理由
    expect(offenders).toEqual([]);
  });

  it('满出血字段的抑制规则还在 —— 删了它两处编辑器会重新长出内嵌方框', () => {
    // ChatInput 的 textarea 与 CodeEditor 的 textarea 都靠它把方框交给外层卡片。
    // 抑制规则必须同样无 layer，否则压不过下面那条全局轮廓。
    const styles = readFileSync(join(SRC, 'styles.css'), 'utf-8');
    expect(styles).toContain('.focus-by-container:focus-visible {');
  });

  it('全局规则的元素清单没变 —— ALLOWED 里的理由挂在它上面', () => {
    // tabs.tsx 的豁免理由是「TabsContent 是 div，不在这份清单里」。
    // 谁往清单里加 div/[tabindex]，那条理由就失效了，得连着 ALLOWED 一起重想。
    const styles = readFileSync(join(SRC, 'styles.css'), 'utf-8');
    expect(styles).toContain(
      'button:focus-visible,\ninput:focus-visible,\ntextarea:focus-visible,\n'
      + 'select:focus-visible,\na:focus-visible,\nsummary:focus-visible {',
    );
  });

  it('画布的焦点态还在 —— 全局规则故意不管 [tabindex]', () => {
    // React Flow 给节点 div 和连线 <g> 都挂 tabIndex=0，两者都进 Tab 序。
    // 全局规则只认元素名，接不到它们，删掉下面两条画布就静默失去焦点可见性。
    const styles = readFileSync(join(SRC, 'styles.css'), 'utf-8');
    expect(styles).toContain('.react-flow__node:focus-visible {');
    expect(styles).toContain('.react-flow__edge:focus-visible .react-flow__edge-path {');
  });
});
