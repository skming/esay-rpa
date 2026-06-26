import { describe, expect, it } from 'vitest';

import type { RpaNodeData } from '../types/rpa';
import { applyNodeConfigDraft, createNodeConfigDraft } from './nodeConfigDraft';

describe('nodeConfigDraft', () => {
  it('应保存浏览器属性提取配置', () => {
    const data: RpaNodeData = {
      title: '提取链接',
      description: 'a.article',
      kind: 'browser',
      status: 'pending',
      action: {
        type: 'browser.extract',
        selector: 'a.article',
        outputVariable: 'links',
        timeoutMs: 30_000
      }
    };

    const draft = createNodeConfigDraft(data);
    const next = applyNodeConfigDraft(data, {
      ...draft,
      attribute: 'href',
      extractMode: 'attribute',
      responseVariable: 'detail_links'
    });

    expect(next.action).toEqual(
      expect.objectContaining({
        attribute: 'href',
        extractMode: 'attribute',
        outputVariable: 'detail_links',
        selector: 'a.article'
      })
    );
  });

  it('不应把属性名写入非提取类浏览器动作', () => {
    const data: RpaNodeData = {
      title: '点击按钮',
      description: '#submit',
      kind: 'browser',
      status: 'pending',
      action: {
        type: 'browser.click',
        selector: '#submit',
        timeoutMs: 30_000
      }
    };

    const draft = createNodeConfigDraft(data);
    const next = applyNodeConfigDraft(data, {
      ...draft,
      attribute: 'href',
      extractMode: 'attribute'
    });

    expect(next.action?.attribute).toBeUndefined();
  });

  it('应保存浏览器按键提交配置', () => {
    const data: RpaNodeData = {
      title: '提交搜索',
      description: 'input.search',
      kind: 'browser',
      status: 'pending',
      action: {
        type: 'browser.press',
        selector: 'input.search',
        inputValue: 'Enter',
        timeoutMs: 30_000
      }
    };

    const draft = createNodeConfigDraft(data);
    const next = applyNodeConfigDraft(data, {
      ...draft,
      inputValue: 'Tab',
      responseVariable: 'submit_key_result'
    });

    expect(next.action).toEqual(
      expect.objectContaining({
        inputValue: 'Tab',
        outputVariable: 'submit_key_result',
        selector: 'input.search',
        type: 'browser.press'
      })
    );
  });
});
