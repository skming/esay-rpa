import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BackendClient } from './backendClient';
import { createBrowserBridge } from './browserBridge';
import type { RunEvent, RunStartPayload } from '../types/electron';

describe('getRunDetail', () => {
  it('同时读取任务快照和持久化日志', async () => {
    const run = { taskId: 't-1', result: { count: 0, values: [] } };
    const logs = [{ id: 'log-1', message: '任务完成' }];
    const getTask = vi.fn().mockResolvedValue(run);
    const getLogs = vi.fn().mockResolvedValue(logs);
    const bridge = createBrowserBridge({ backendClient: { getTask, getLogs } as unknown as BackendClient });

    expect(await bridge.getRunDetail('t-1')).toEqual({ ok: true, data: { run, logs } });
    expect(getTask).toHaveBeenCalledWith('t-1');
    expect(getLogs).toHaveBeenCalledWith('t-1');
  });

  it('日志读取失败时明确返回错误', async () => {
    const bridge = createBrowserBridge({ backendClient: {
      getTask: vi.fn().mockResolvedValue({ taskId: 't-1' }),
      getLogs: vi.fn().mockRejectedValue(new Error('日志不可用')),
    } as unknown as BackendClient });

    expect(await bridge.getRunDetail('t-1')).toEqual({ ok: false, error: '日志不可用' });
  });
});

describe('浏览器运行反馈', () => {
  let cleanup = async (): Promise<void> => {};

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('window', globalThis);
  });

  afterEach(async () => {
    await cleanup();
    cleanup = async () => {};
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function fixture() {
    const snapshot = (taskId: string, status = 'running') => ({
      taskId, status, progress: { currentStep: 2, totalSteps: 5, percent: 40 }, variables: [], artifacts: [],
    });
    const socket = new EventTarget() as EventTarget & { close: () => void };
    socket.close = vi.fn(() => socket.dispatchEvent(new Event('close')));
    const client = {
      startTask: vi.fn(async () => snapshot('task-1')),
      getTask: vi.fn(async () => snapshot('task-1')),
      getLogs: vi.fn(async () => [] as { id: string; level: string; message: string; nodeId: string; time: string }[]),
      createLogSocket: vi.fn(() => socket),
      stopTask: vi.fn(async () => snapshot('task-1', 'stopped')),
    };
    const bridge = createBrowserBridge({ backendClient: client as unknown as BackendClient });
    const events: RunEvent[] = [];
    bridge.onRunEvent(event => events.push(event));
    cleanup = async () => {
      client.stopTask.mockResolvedValue(snapshot('cleanup', 'stopped'));
      await bridge.stopRun();
    };
    const payload: RunStartPayload = {
      mode: 'run', flowName: '流程', flowDefinition: { nodes: [{ id: 'collect', type: 'browser.extract' }] },
    };
    return { bridge, client, events, payload, socket, snapshot };
  }

  it('按真实节点与后端进度更新，不插入 n1 演示节点', async () => {
    const { bridge, client, events, payload } = fixture();
    client.getLogs.mockResolvedValue([{ id: 'log-1', level: 'running', message: '执行节点', nodeId: 'collect', time: new Date().toISOString() }]);
    await bridge.startRun(payload);
    await vi.advanceTimersByTimeAsync(250);
    expect(events).toContainEqual(expect.objectContaining({
      type: 'run:start', payload: expect.objectContaining({ totalSteps: 5 }),
    }));
    expect(events).toContainEqual(expect.objectContaining({
      type: 'run:progress', payload: expect.objectContaining({ currentStep: 2, totalSteps: 5, percent: 40 }),
    }));
    expect(events).toContainEqual({ type: 'node:update', payload: { runId: 'task-1', nodeId: 'collect', status: 'running' } });
    expect(events.some(event => event.type === 'node:update' && event.payload.nodeId === 'n1')).toBe(false);
  });

  it('WebSocket 在线时，终态仍补齐 HTTP 日志', async () => {
    const { bridge, client, events, payload, socket, snapshot } = fixture();
    client.getTask.mockResolvedValue(snapshot('task-1', 'success'));
    client.getLogs.mockResolvedValue([{ id: 'last-log', level: 'success', message: '节点完成', nodeId: 'collect', time: new Date().toISOString() }]);
    await bridge.startRun(payload);
    socket.dispatchEvent(new Event('open'));
    await vi.advanceTimersByTimeAsync(250);
    expect(events).toContainEqual(expect.objectContaining({ type: 'log:append', payload: expect.objectContaining({ id: 'last-log' }) }));
    expect(events).toContainEqual(expect.objectContaining({ type: 'run:finish', payload: expect.objectContaining({ status: 'success' }) }));
  });

  it('旧轮询失败不能给新运行追加错误事件', async () => {
    const { bridge, client, events, payload, snapshot } = fixture();
    let rejectOld!: (reason: Error) => void;
    client.getTask.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectOld = reject; }));
    await bridge.startRun(payload);
    await vi.advanceTimersByTimeAsync(250);
    client.startTask.mockResolvedValue(snapshot('task-2'));
    await bridge.startRun(payload);
    const count = events.length;
    rejectOld(new Error('old poll failed'));
    await vi.advanceTimersByTimeAsync(0);
    expect(events).toHaveLength(count);
  });

  it('停止失败时返回错误，保留当前运行观察', async () => {
    const { bridge, client, events, payload } = fixture();
    await bridge.startRun(payload);
    client.stopTask.mockRejectedValue(new Error('stop failed'));
    expect(await bridge.stopRun()).toEqual({ ok: false, error: 'stop failed' });
    expect(events.some(event => event.type === 'run:finish')).toBe(false);
    client.stopTask.mockResolvedValue({ taskId: 'task-1', status: 'stopped', progress: { currentStep: 2, totalSteps: 5, percent: 40 }, variables: [], artifacts: [] });
    expect(await bridge.stopRun()).toMatchObject({ ok: true, data: { stopped: true, runId: 'task-1' } });
  });

  it('切换执行前等待停止成功，停止失败时不启动新流程', async () => {
    const { bridge, client, events, payload } = fixture();
    await bridge.startRun(payload);
    client.stopTask.mockRejectedValue(new Error('stop failed'));
    expect(await bridge.startRun(payload)).toEqual({ ok: false, error: 'stop failed' });
    expect(client.startTask).toHaveBeenCalledTimes(1);
    expect(events.some(event => event.type === 'run:finish')).toBe(false);
  });

  it('后端开始新任务失败时不制造成功运行事件', async () => {
    const { bridge, client, events, payload } = fixture();
    client.startTask.mockRejectedValue(new Error('backend unavailable'));
    expect(await bridge.startRun(payload)).toEqual({ ok: false, error: 'backend unavailable' });
    expect(events).toEqual([]);
  });
});
