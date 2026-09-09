import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import type { ArtifactSnapshot, RpaBridge, RunEvent } from '../types/electron';
import type { RuntimeVariable } from '../types/rpa';
import { useRunEventHandler } from './useRunEventHandler';

vi.mock('../stores/useNotificationStore', () => ({ useNotificationStore: { getState: () => ({ push: vi.fn() }) } }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function renderHandler() {
  const variables = deferred<RuntimeVariable[]>();
  const artifacts = deferred<ArtifactSnapshot[]>();
  const api = {
    listTaskVariables: async () => ({ ok: true, data: await variables.promise }),
    listArtifacts: async () => ({ ok: true, data: await artifacts.promise }),
    listFlows: async () => ({ ok: true, data: [] }),
  } as unknown as RpaBridge;
  const params: Parameters<typeof useRunEventHandler>[0] = {
    activeRunIdRef: { current: 'old-run' }, lastRunIdRef: { current: 'old-run' }, activeFlowNameRef: { current: '流程' },
    callBridge: async (action) => (await action(api)).data ?? null,
    pushToast: vi.fn(), onArtifactsReady: vi.fn(), setActiveRunId: vi.fn(), setActiveRunFlowId: vi.fn(),
    setLastRunId: vi.fn(), setArtifactContent: vi.fn(), setArtifacts: vi.fn(), setFlows: vi.fn(),
    setGeneratedScript: vi.fn(), setInputPrompt: vi.fn(), setHumanTakeoverMessage: vi.fn(), setPausedPageUrl: vi.fn(),
    setLogs: vi.fn(), setNodeStates: vi.fn(), setProgress: vi.fn(), setRuntimeStatus: vi.fn(), setVariables: vi.fn(),
  };
  let handle!: (event: RunEvent) => void;
  function Probe() {
    handle = useRunEventHandler(params);
    return null;
  }
  renderToStaticMarkup(createElement(Probe));
  return { handle, params, variables, artifacts };
}

function start(runId: string): RunEvent {
  return { type: 'run:start', payload: { runId, flowName: '流程', startedAt: '', totalSteps: 2, status: 'running' } };
}

function finish(runId: string): RunEvent {
  return { type: 'run:finish', payload: { runId, status: 'success', finishedAt: '', message: '' } };
}

describe('运行事件归属', () => {
  it('开始后同一批到达的节点事件立即生效', () => {
    const { handle, params } = renderHandler();
    handle(start('new-run'));
    vi.mocked(params.setNodeStates).mockClear();
    handle({ type: 'node:update', payload: { runId: 'new-run', nodeId: 'node-1', status: 'running' } });
    expect(params.setNodeStates).toHaveBeenCalledOnce();
  });

  it('新任务开始后，旧任务的结束事件不能停止新任务', () => {
    const { handle, params } = renderHandler();
    handle(start('new-run'));
    vi.mocked(params.setActiveRunId).mockClear();
    handle(finish('old-run'));
    expect(params.setActiveRunId).not.toHaveBeenCalled();
    expect(params.pushToast).not.toHaveBeenCalled();
  });

  it('有活动任务时不再接收上一次任务的事件', () => {
    const { handle, params } = renderHandler();
    params.activeRunIdRef.current = 'new-run';
    handle({ type: 'variable:set', payload: { runId: 'old-run', name: 'old', scope: '全局', type: 'String', value: 'old' } });
    expect(params.setVariables).not.toHaveBeenCalled();
  });

  it.each(['new-run', null])('任务切换或清空后丢弃旧请求结果：%s', async (runId) => {
    const { handle, params, variables, artifacts } = renderHandler();
    handle(finish('old-run'));
    params.activeRunIdRef.current = runId;
    params.lastRunIdRef.current = runId;
    variables.resolve([{ name: 'old', scope: '全局', type: 'String', value: 'old' }]);
    artifacts.resolve([{ artifactId: 'old-artifact' } as ArtifactSnapshot]);
    await vi.waitFor(() => expect(params.setFlows).toHaveBeenCalledOnce());
    expect(params.setVariables).not.toHaveBeenCalled();
    expect(params.setArtifacts).not.toHaveBeenCalled();
    expect(params.onArtifactsReady).not.toHaveBeenCalled();
  });

  it('仍在查看已结束任务时回填其变量和产物', async () => {
    const { handle, params, variables, artifacts } = renderHandler();
    handle(finish('old-run'));
    const result: RuntimeVariable[] = [{ name: 'current', scope: '全局', type: 'String', value: 'value' }];
    variables.resolve(result);
    artifacts.resolve([{ artifactId: 'current-artifact' } as ArtifactSnapshot]);
    await vi.waitFor(() => expect(params.setVariables).toHaveBeenCalledWith(result));
    expect(params.setArtifacts).toHaveBeenCalledOnce();
    expect(params.onArtifactsReady).toHaveBeenCalledOnce();
  });
});
