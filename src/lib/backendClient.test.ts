import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchFlowSnapshot } from './backendClient';

function respondWith(status: number, body: unknown): void {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status })));
}

afterEach(() => {
  vi.unstubAllGlobals();
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
