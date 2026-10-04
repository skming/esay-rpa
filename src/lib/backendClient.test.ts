import { createRequire } from 'node:module';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { BackendClient, fetchFlowSnapshot } from './backendClient';

const require = createRequire(import.meta.url);
const { BackendClient: ElectronBackendClient } = require('../../electron/backendClient.cjs') as {
  BackendClient: new (baseUrl: string) => BackendClient;
};

function respondWith(status: number, body: unknown): void {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status })));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('浏览器与 Electron 的请求契约', () => {
  const flow = {
    name: ' 流程 ', version: ' v1.0.0 ', status: 'paused' as const,
    definition: { nodes: [{ id: 'n1', type: 'variable.set' }] }, inputVariables: [],
    folderPath: ' 团队/采集 ',
    acceptanceContract: { requirements: [], deliverables: [] },
    defaultBrowserExecutor: 'extension' as const,
  };
  const run = {
    flowName: ' 流程 ', mode: 'debug' as const, browserExecutor: 'extension' as const,
    timeoutMs: 65000, concurrency: 2.7, failureStrategy: 'retry' as const,
    scope: 'from-selection' as const, startNodeId: ' n1 ', screenshot: false,
    variables: { name: '张三' }, flowDefinition: flow.definition,
  };
  const cases: [string, (client: BackendClient) => Promise<unknown>][] = [
    ['创建流程', client => client.createFlow(flow)],
    ['启动任务', client => client.startTask(run)],
    ['运行已保存流程', client => client.runFlow('flow/id', run)],
    ['生成脚本', client => client.generateScript({ flowName: ' 导出 ', flowDefinition: flow.definition })],
    ['分析页面', client => client.analyzeSite({ targetUrl: ' https://example.com ', timeoutMs: 12000 })],
    ['创建计划', client => client.createSchedule({ name: ' 计划 ', enabled: true, cronExpression: '0 9 * * *', timezone: 'Asia/Shanghai', task: { ...run, flowIds: ['flow-1'] } })],
    ['任务分页', client => client.listTasks({ flowId: ' flow-1 ', limit: 2.7 })],
    ['流程运行分页', client => client.listFlowRuns('flow/id', { limit: 999 })],
    ['调试控制', client => client.debugTask('task/id', 'step-into')],
  ];

  it.each(cases)('%s 的 URL、方法和 JSON 内容一致', async (_name, send) => {
    const fetch = vi.fn(async () => new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    await send(new BackendClient('http://localhost:8765'));
    await send(new ElectronBackendClient('http://localhost:8765'));
    const calls = fetch.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0][0]).toBe(calls[1][0]);
    expect(calls[0][1].method).toBe(calls[1][1].method);
    expect(calls[0][1].body).toBe(calls[1][1].body);
  });

  it('两端保存时保留验收契约、文件夹和流程状态', async () => {
    const fetch = vi.fn(async () => new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    await new BackendClient().createFlow(flow);
    await new ElectronBackendClient('http://localhost').createFlow(flow);
    for (const [, options] of fetch.mock.calls as unknown as [string, RequestInit][]) {
      expect(JSON.parse(options.body as string)).toMatchObject({
        acceptanceContract: flow.acceptanceContract,
        folderPath: '团队/采集', status: 'paused', defaultBrowserExecutor: 'extension',
      });
    }
  });

  it('已保存流程运行透传调用方指定的超时', async () => {
    const fetch = vi.fn(async () => new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    await new BackendClient().runFlow('flow-1', run);
    const [, options] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(options.body as string).timeoutMs).toBe(65000);
  });
});

describe('HTTP 请求取消', () => {
  it('传入已取消信号时，底层请求同样处于取消状态', async () => {
    const controller = new AbortController();
    controller.abort();
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
      expect(options.signal?.aborted).toBe(true);
      throw new DOMException('Aborted', 'AbortError');
    }));
    await expect(new BackendClient().listAiModels(controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('请求完成后移除调用方的取消监听器', async () => {
    const controller = new AbortController();
    const add = vi.spyOn(controller.signal, 'addEventListener');
    const remove = vi.spyOn(controller.signal, 'removeEventListener');
    respondWith(200, {});
    await new BackendClient().listAiModels(controller.signal);
    expect(remove).toHaveBeenCalledWith('abort', add.mock.calls[0][1]);
  });
});

describe('fetchFlowSnapshot', () => {
  it('后端回 404 时判定为已删除', async () => {
    respondWith(404, { detail: 'Flow not found' });

    expect(await fetchFlowSnapshot('ce71c23a-48e9-4478-ba40-47edb461ac23')).toEqual({ kind: 'missing' });
  });

  // 后端抖动被当成"已删除"会让调用方清掉 lastOpenedFlowId，一次 500 就丢掉用户上次打开的流程
  it('后端出错或连不上时不判定为已删除', async () => {
    respondWith(500, { detail: 'boom' });
    expect(await fetchFlowSnapshot('flow_abc')).toEqual({ kind: 'unavailable' });

    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    expect(await fetchFlowSnapshot('flow_abc')).toEqual({ kind: 'unavailable' });
  });

  it('取到时带回快照', async () => {
    respondWith(200, { flowId: 'flow_abc', name: '流程' });

    const result = await fetchFlowSnapshot('flow_abc');
    expect(result.kind).toBe('ok');
    expect(result.kind === 'ok' && result.flow.flowId).toBe('flow_abc');
  });
});
