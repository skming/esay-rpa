import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { Segmented } from './FormControls';

/** 这两条钉的是 Segmented 的无障碍语义。坏了不会报错也不会变形：
 *  role 退回 tab/tablist 时读屏会播报「标签页 1/2」并去找不存在的面板，
 *  tab 停靠点全变 -1 时整组只有鼠标点得到——两种都只有用读屏或键盘才发现。 */
const OPTIONS = [
  { label: '点「下一页」', value: 'click' },
  { label: '按地址翻页', value: 'url' },
];

const tabStops = (html: string): string[] => html.match(/tabindex="[^"]*"/g) ?? [];

describe('Segmented', () => {
  it('单选语义走 radiogroup，且整组只留一个 tab 停靠点', () => {
    const html = renderToStaticMarkup(<Segmented label="翻页方式" options={OPTIONS} value="url" />);

    expect(html).toContain('role="radiogroup"');
    expect(html).toContain('aria-label="翻页方式"');
    expect(html).not.toContain('role="tab"');
    expect(html.match(/role="radio"/g)).toHaveLength(2);
    expect(html).toContain('aria-checked="true"');
    expect(tabStops(html)).toEqual(['tabindex="-1"', 'tabindex="0"']);
  });

  it('取值不在选项里时第一项接住 tab 停靠点，否则键盘进不来', () => {
    const html = renderToStaticMarkup(<Segmented options={OPTIONS} value="gone" />);

    expect(html).not.toContain('aria-checked="true"');
    expect(tabStops(html)).toEqual(['tabindex="0"', 'tabindex="-1"']);
  });
});
