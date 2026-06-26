import { describe, expect, it } from 'vitest';

import { buildEffectiveRunConfigSummary, buildRunConfigLogMessage, buildRunConfigVariables } from './runConfigPresentation';

describe('runConfigPresentation', () => {
  it('运行日志摘要应包含默认超时', () => {
    const message = buildRunConfigLogMessage({
      concurrency: 2,
      failureStrategy: 'retry',
      flowName: '订单自动处理',
      mode: 'run',
      scope: 'from-selection',
      screenshot: false,
      selector: '.quote .text::text',
      startNodeId: 'n1',
      targetUrl: 'https://quotes.toscrape.com/',
      timeoutMs: 12000,
      variables: {}
    });

    expect(message).toContain('默认超时 12000ms');
    expect(message).toContain('起点 n1');
  });

  it('运行配置变量应包含默认超时变量', () => {
    const variables = buildRunConfigVariables({
      flowName: '订单自动处理',
      mode: 'run',
      selector: '.quote .text::text',
      targetUrl: 'https://quotes.toscrape.com/',
      timeoutMs: 45000
    });

    expect(variables.find((item) => item.name === 'run_timeout_ms')?.value).toBe('45000');
  });

  it('最终生效配置摘要应标记节点级超时优先', () => {
    const summary = buildEffectiveRunConfigSummary({
      failureStrategy: 'retry',
      overrideCount: 2,
      scope: 'from-selection',
      screenshot: true,
      selectedNodeAction: { type: 'browser.fetch', timeoutMs: 8000 },
      selectedNodeId: 'n1',
      selectedNodeTitle: '打开网页',
      timeoutMs: 12000
    });

    expect(summary.defaultTimeoutMs).toBe(12000);
    expect(summary.effectiveTimeoutMs).toBe(8000);
    expect(summary.effectiveTimeoutSource).toBe('node');
    expect(summary.startNodeLabel).toContain('n1');
  });
});
