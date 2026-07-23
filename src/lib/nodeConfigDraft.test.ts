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
      firstValueVariable: 'first_detail_link',
      statusVariable: 'link_count',
      responseVariable: 'detail_links'
    });

    expect(next.action).toEqual(
      expect.objectContaining({
        attribute: 'href',
        countVariable: 'link_count',
        extractMode: 'attribute',
        firstValueVariable: 'first_detail_link',
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

  it('应保存节点失败继续策略', () => {
    const data: RpaNodeData = {
      title: '关闭可选弹窗',
      description: '.modal-close',
      kind: 'browser',
      status: 'pending',
      action: {
        type: 'browser.click',
        selector: '.modal-close',
        timeoutMs: 10_000
      }
    };

    const draft = createNodeConfigDraft(data);

    expect(draft.continueOnError).toBe(false);

    const next = applyNodeConfigDraft(data, {
      ...draft,
      continueOnError: true
    });

    expect(next.action?.continueOnError).toBe(true);
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
