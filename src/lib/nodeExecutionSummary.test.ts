import { describe, expect, it } from 'vitest';

import type { Node } from '@xyflow/react';

import { buildNodeExecutionSummary } from './nodeExecutionSummary';
import type { RpaNodeData } from '../types/rpa';

function buildNode(action: NonNullable<RpaNodeData['action']>): Node<RpaNodeData> {
  return {
    id: 'n1',
    position: { x: 0, y: 0 },
    data: {
      action,
      description: '',
      kind: 'browser',
      status: 'pending',
      title: '测试节点'
    },
    type: 'rpaStep'
  };
}

describe('nodeExecutionSummary', () => {
  it('应汇总 browser.fetch 的关键执行字段', () => {
    const summary = buildNodeExecutionSummary(
      buildNode({
        type: 'browser.fetch',
        targetUrl: 'https://quotes.toscrape.com/',
        selector: '.quote .text::text',
        fetcher: 'dynamic',
        extractMode: 'text',
        timeoutMs: 30000
      })
    );

    expect(summary?.rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: 'targetUrl', description: 'https://quotes.toscrape.com/' }),
        expect.objectContaining({ name: 'selector', description: '.quote .text::text' }),
        expect.objectContaining({ name: 'timeoutMs', description: '30000 ms' })
      ])
    );
  });

  it('browser.open 只应汇总打开页面所需字段', () => {
    const summary = buildNodeExecutionSummary(
      buildNode({
        type: 'browser.open',
        targetUrl: 'https://quotes.toscrape.com/',
        selector: '.quote',
        extractMode: 'text',
        timeoutMs: 15000
      })
    );

    expect(summary?.rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: 'targetUrl', description: 'https://quotes.toscrape.com/' }),
        expect.objectContaining({ name: 'timeoutMs', description: '15000 ms' })
      ])
    );
    expect(summary?.rows).not.toEqual(expect.arrayContaining([expect.objectContaining({ name: 'selector' })]));
    expect(summary?.rows).not.toEqual(expect.arrayContaining([expect.objectContaining({ name: 'extractMode' })]));
  });

  it('应汇总 browser.extract 的属性提取字段', () => {
    const summary = buildNodeExecutionSummary(
      buildNode({
        type: 'browser.extract',
        selector: 'a.detail',
        extractMode: 'attribute',
        attribute: 'href'
      })
    );

    expect(summary?.rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: 'selector', description: 'a.detail' }),
        expect.objectContaining({ name: 'extractMode', description: 'attribute' }),
        expect.objectContaining({ name: 'attribute', description: 'href' })
      ])
    );
  });

  it('应汇总 file.copy 的源路径和目标路径', () => {
    const summary = buildNodeExecutionSummary(
      buildNode({
        type: 'file.copy',
        path: 'data/input.txt',
        targetPath: 'archive/input.txt',
        timeoutMs: 10000
      })
    );

    expect(summary?.rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: 'path', description: 'data/input.txt' }),
        expect.objectContaining({ name: 'targetPath', description: 'archive/input.txt' })
      ])
    );
  });

  it('应汇总 control.foreach 的遍历关键字段', () => {
    const summary = buildNodeExecutionSummary(
      buildNode({
        type: 'control.foreach',
        itemsVariable: 'excel_rows',
        itemVariable: 'current_row',
        indexVariable: 'loop_index',
        maxIterations: 200
      })
    );

    expect(summary?.rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: 'itemsVariable', description: 'excel_rows' }),
        expect.objectContaining({ name: 'itemVariable', description: 'current_row' }),
        expect.objectContaining({ name: 'indexVariable', description: 'loop_index' }),
        expect.objectContaining({ name: 'maxIterations', description: '200' })
      ])
    );
  });

  it('应汇总 variable.set 的目标变量和值', () => {
    const summary = buildNodeExecutionSummary(
      buildNode({
        type: 'variable.set',
        variableName: 'result_status',
        value: 'done',
        scope: '全局',
        outputVariable: 'result_status'
      })
    );

    expect(summary?.rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: 'variableName', description: 'result_status' }),
        expect.objectContaining({ name: 'value', description: 'done' }),
        expect.objectContaining({ name: 'scope', description: '全局' }),
        expect.objectContaining({ name: 'outputVariable', description: 'result_status' })
      ])
    );
  });

  it('应汇总 data.regex.match 的输入表达式和输出变量', () => {
    const summary = buildNodeExecutionSummary(
      buildNode({
        type: 'data.regex.match',
        inputValue: '${var.input_text}',
        pattern: '(\\d+)',
        outputVariable: 'regex_matches',
        firstValueVariable: 'first_match',
        countVariable: 'match_count'
      })
    );

    expect(summary?.rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: 'inputValue', description: '${var.input_text}' }),
        expect.objectContaining({ name: 'pattern', description: '(\\d+)' }),
        expect.objectContaining({ name: 'responseVariable', description: 'regex_matches' }),
        expect.objectContaining({ name: 'statusVariable', description: 'match_count' }),
        expect.objectContaining({ name: 'firstValueVariable', description: 'first_match' })
      ])
    );
  });
});
