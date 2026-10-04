import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useBottomPanelStore } from '../stores/useBottomPanelStore';
import type { RpaBridge, RunDetail, TaskSnapshot } from '../types/electron';
import { useRecentRun } from './useRecentRun';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function detail(status: TaskSnapshot['status'] = 'error'): RunDetail {
  return {
    run: {
      taskId: 'task-1', flowId: 'flow-1', flowName: '测试流程', status, mode: 'run',
      createdAt: '2026-10-04T01:00:00Z', updatedAt: '2026-10-04T01:01:00Z',
      runConfig: { concurrency: 1, failureStrategy: 'stop', scope: 'full', screenshot: true },
      progress: { currentStep: 2, elapsedMs: 800, percent: 50, totalSteps: 4 },
      error: status === 'error' ? '等待元素超时' : null,
      variables: [{ name: 'rows', scope: '全局', type: 'List', value: '[]' }],
      artifacts: [{ artifactId: 'screenshot-1', artifactType: 'screenshot', filename: 'failure.png',
        taskId: 'task-1', createdAt: '2026-10-04T01:01:00Z', metadata: {},
        storageUrl: 'file:///tmp/failure.png', contentType: 'image/png', sizeBytes: 10 }],
    },
    logs: status === 'error'
      ? [{ id: 'log-1', time: '01:01:00', level: 'error', nodeId: 'n2', message: '节点执行失败', detail: '等待元素超时' }]
      : [{ id: 'log-1', time: '01:01:00', level: 'success', nodeId: 'n2', message: '执行完成' }],
  };
}

function renderRecentRun(result = detail()) {
  const list = vi.fn(async () => ({ ok: true, data: [result.run] }));
  const read = vi.fn<RpaBridge['getRunDetail']>(async () => ({ ok: true, data: result }));
  const api = { listFlowRuns: list, getRunDetail: read } as unknown as RpaBridge;
  const params: Parameters<typeof useRecentRun>[0] = {
    activeRunIdRef: { current: null }, lastRunIdRef: { current: null },
    callBridge: async (action) => (await action(api)).data ?? null,
    setLastRunId: vi.fn(), setLogs: vi.fn(), setNodeStates: vi.fn(), setProgress: vi.fn(),
    setRuntimeStatus: vi.fn(), setVariables: vi.fn(), setArtifacts: vi.fn(),
  };
  let history!: ReturnType<typeof useRecentRun>;
  function Probe() { history = useRecentRun(params); return null; }
  renderToStaticMarkup(createElement(Probe));
  return { history, params, list, read };
}

describe('恢复流程最近运行', () => {
  beforeEach(() => { useBottomPanelStore.getState().setOpen(false); useBottomPanelStore.getState().setActiveTab('logs'); });

  it('带入失败日志、任务 ID、节点状态、变量与产物，打开错误面板', async () => {
    const result = detail();
    const { history, params, list, read } = renderRecentRun(result);
    await history.restoreRecentRun('flow-1');
    expect(list).toHaveBeenCalledWith('flow-1', { limit: 1 });
    expect(read).toHaveBeenCalledWith('task-1');
    expect(params.lastRunIdRef.current).toBe('task-1');
    expect(params.setLastRunId).toHaveBeenCalledWith('task-1');
    expect(params.setLogs).toHaveBeenCalledWith([expect.objectContaining({ level: 'error', nodeId: 'n2', detail: '等待元素超时' })]);
    expect(params.setNodeStates).toHaveBeenCalledWith({ n2: { status: 'error' } });
    expect(params.setProgress).toHaveBeenCalledWith(result.run.progress);
    expect(params.setVariables).toHaveBeenCalledWith(result.run.variables);
    expect(params.setArtifacts).toHaveBeenCalledWith(result.run.artifacts);
    expect(params.setRuntimeStatus).toHaveBeenCalledWith('error');
    expect(useBottomPanelStore.getState()).toMatchObject({ open: true, activeTab: 'errors' });
  });

  it('成功运行恢复自身日志，不沿用更早的失败状态', async () => {
    const { history, params } = renderRecentRun(detail('success'));
    await history.restoreRecentRun('flow-1');
    expect(params.setRuntimeStatus).toHaveBeenCalledWith('success');
    expect(params.setLogs).toHaveBeenCalledWith([expect.objectContaining({ level: 'success' })]);
    expect(useBottomPanelStore.getState().open).toBe(false);
  });

  it('没有错误日志时使用任务已保存的错误，不丢失 AI 排查入口', async () => {
    const result = detail();
    result.logs = [];
    const { history, params } = renderRecentRun(result);
    await history.restoreRecentRun('flow-1');
    expect(params.setLogs).toHaveBeenCalledWith([expect.objectContaining({ level: 'error', message: result.run.error })]);
    expect(params.setLastRunId).toHaveBeenCalledWith(result.run.taskId);
  });

  it.each(['queued', 'running', 'awaiting_confirmation'] as const)('不把 %s 当作历史终态或弹出旧确认', async (status) => {
    const { history, params, read } = renderRecentRun(detail(status));
    await history.restoreRecentRun('flow-1');
    expect(read).not.toHaveBeenCalled();
    expect(params.setLastRunId).not.toHaveBeenCalled();
  });

  it('没有运行记录时保持空面板', async () => {
    const { history, params, list, read } = renderRecentRun();
    list.mockResolvedValue({ ok: true, data: [] });
    await history.restoreRecentRun('flow-1');
    expect(read).not.toHaveBeenCalled();
    expect(params.setLogs).not.toHaveBeenCalled();
  });

  it('读取失败时不生成假运行记录', async () => {
    const { history, params, read } = renderRecentRun();
    read.mockResolvedValue({ ok: false, error: '读取失败' });
    await history.restoreRecentRun('flow-1');
    expect(params.setLastRunId).not.toHaveBeenCalled();
  });

  it('不接受属于其他流程的运行详情', async () => {
    const { history, params } = renderRecentRun(detail());
    await history.restoreRecentRun('other-flow');
    expect(params.setLogs).not.toHaveBeenCalled();
  });

  it.each(['activeRunIdRef', 'lastRunIdRef'] as const)('已有 %s 时不读取历史', async (ref) => {
    const { history, params, list } = renderRecentRun();
    params[ref].current = 'new-run';
    await history.restoreRecentRun('flow-1');
    expect(list).not.toHaveBeenCalled();
  });

  it('详情仍在读取时切换流程或重新运行，丢弃旧结果', async () => {
    const pending = deferred<{ ok: boolean; data: RunDetail }>();
    const { history, params, read } = renderRecentRun();
    read.mockImplementation(() => pending.promise);
    const loading = history.restoreRecentRun('flow-1');
    await vi.waitFor(() => expect(read).toHaveBeenCalledOnce());
    history.cancelRestore();
    pending.resolve({ ok: true, data: detail() });
    await loading;
    expect(params.setLogs).not.toHaveBeenCalled();
    expect(params.setLastRunId).not.toHaveBeenCalled();
    expect(useBottomPanelStore.getState().open).toBe(false);
  });

  it('列表仍在读取时清空面板，不再读取旧任务详情', async () => {
    const pending = deferred<{ ok: boolean; data: TaskSnapshot[] }>();
    const { history, params, list, read } = renderRecentRun();
    list.mockImplementation(() => pending.promise);
    const loading = history.restoreRecentRun('flow-1');
    history.cancelRestore();
    pending.resolve({ ok: true, data: [detail().run] });
    await loading;
    expect(read).not.toHaveBeenCalled();
    expect(params.setLogs).not.toHaveBeenCalled();
  });

  it('读取途中新运行已开始并结束，也不能覆盖新结果', async () => {
    const pending = deferred<{ ok: boolean; data: RunDetail }>();
    const { history, params, read } = renderRecentRun();
    read.mockImplementation(() => pending.promise);
    const loading = history.restoreRecentRun('flow-1');
    await vi.waitFor(() => expect(read).toHaveBeenCalledOnce());
    params.lastRunIdRef.current = 'new-run';
    pending.resolve({ ok: true, data: detail() });
    await loading;
    expect(params.setLogs).not.toHaveBeenCalled();
    expect(params.lastRunIdRef.current).toBe('new-run');
  });
});
