import { createRequire } from 'node:module';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { createRuntimeController, generateScraplingScript } = require('./runtime.cjs');
let cleanupRun = async () => {};

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('WebSocket', undefined);
});

afterEach(async () => {
  await cleanupRun();
  cleanupRun = async () => {};
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function createFakeWindow() {
  const events = [];
  return {
    events,
    isDestroyed: () => false,
    webContents: { send: (_channel, event) => events.push(event) },
  };
}

function controllerRejectingWith(error) {
  return createRuntimeController({
    backendClient: {
      runFlow: async () => {
        throw error;
      },
    },
  });
}

it('后端拒绝这次请求时不回落本地模拟', async () => {
  // 回落用的是内置演示节点 id，真实流程一个都对不上：面板既没有节点状态也没有产物，结束时却报 success。
  // 判据取「一个事件都没发出去」，只断言 rejects 的话，多发一次假的 run:start 照样能过。
  const rejected = new Error('流程缺少完整验收契约');
  rejected.status = 422;
  const controller = controllerRejectingWith(rejected);
  const win = createFakeWindow();

  await expect(controller.startRun(win, { flowId: 'flow-1' })).rejects.toThrow('流程缺少完整验收契约');

  expect(win.events).toEqual([]);
});

it('后端不可用时返回原始错误且不产生模拟运行事件', async () => {
  const controller = controllerRejectingWith(new Error('fetch failed'));
  const win = createFakeWindow();

  await expect(controller.startRun(win, { flowId: 'flow-1' })).rejects.toThrow('fetch failed');

  expect(win.events).toEqual([]);
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function task(taskId, status = 'running') {
  return { taskId, status, progress: { currentStep: 1, totalSteps: 4, percent: 25 } };
}

function watchingController(overrides = {}) {
  const backend = {
    runFlow: vi.fn(async () => task('new')),
    getTask: vi.fn(async (id) => task(id)),
    getLogs: vi.fn(async () => []),
    stopTask: vi.fn(async (id) => task(id, 'stopped')),
    ...overrides,
  };
  const controller = createRuntimeController({ backendClient: backend });
  cleanupRun = async () => {
    backend.stopTask.mockResolvedValue(task('cleanup', 'stopped'));
    await controller.stopRun();
  };
  return { backend, controller, win: createFakeWindow() };
}

it.each(['success', 'error'])('旧轮询的 %s 结果不能覆盖新运行', async (outcome) => {
  const old = deferred();
  const { backend, controller, win } = watchingController({ getTask: vi.fn(() => old.promise) });
  controller.watchBackendRun(win, task('old'));
  vi.advanceTimersByTime(250);
  expect(backend.getTask).toHaveBeenCalledWith('old');
  controller.watchBackendRun(win, task('new'));
  const eventCount = win.events.length;
  if (outcome === 'success') old.resolve(task('old', 'success'));
  else old.reject(new Error('old poll failed'));
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();

  expect(win.events).toHaveLength(eventCount);
  expect(await controller.stopRun('new')).toMatchObject({ stopped: true, runId: 'new' });
});

it('轮询期间停止后，迟到响应不再派发事件', async () => {
  const old = deferred();
  const { controller, win } = watchingController({ getTask: vi.fn(() => old.promise) });
  controller.watchBackendRun(win, task('old'));
  vi.advanceTimersByTime(250);
  await controller.stopRun('old');
  const eventCount = win.events.length;
  old.resolve(task('old', 'success'));
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();

  expect(win.events).toHaveLength(eventCount);
});

it('启动新运行前等待后端确认旧任务已停止', async () => {
  const stopped = deferred();
  const { backend, controller, win } = watchingController({ stopTask: vi.fn(() => stopped.promise) });
  controller.watchBackendRun(win, task('old'));
  const starting = controller.startRun(win, { flowId: 'flow-new' });
  expect(backend.stopTask).toHaveBeenCalledWith('old');
  expect(backend.runFlow).not.toHaveBeenCalled();
  stopped.resolve(task('old', 'stopped'));
  expect(await starting).toMatchObject({ runId: 'new' });
});

it('停止请求失败时保留运行，不报告停止成功，也不启动新任务', async () => {
  const { backend, controller, win } = watchingController({ stopTask: vi.fn().mockRejectedValue(new Error('stop failed')) });
  controller.watchBackendRun(win, task('old'));
  const eventCount = win.events.length;
  await expect(controller.startRun(win, { flowId: 'flow-new' })).rejects.toThrow('stop failed');
  expect(backend.runFlow).not.toHaveBeenCalled();
  expect(win.events).toHaveLength(eventCount);
});

it('旧停止请求的迟到结果不能停止新观察任务', async () => {
  const stopped = deferred();
  const { backend, controller, win } = watchingController({ stopTask: vi.fn(() => stopped.promise) });
  controller.watchBackendRun(win, task('old'));
  const stopping = controller.stopRun('old');
  controller.watchBackendRun(win, task('new'));
  const eventCount = win.events.length;
  stopped.resolve(task('old', 'stopped'));
  expect(await stopping).toMatchObject({ runId: 'old' });
  expect(win.events).toHaveLength(eventCount);
  backend.stopTask.mockResolvedValue(task('new', 'stopped'));
  expect(await controller.stopRun('new')).toMatchObject({ runId: 'new', stopped: true });
});

function failingBackend(message = 'fetch failed') {
  return { generateScript: async () => { throw new Error(message); } };
}

it('离线模板沿用中文流程名做文件名', async () => {
  // 只留 [a-z0-9] 会把中文名整段吃掉，两个中文流程都叫 rpa-flow.py，导出时第二个覆盖第一个。
  const script = await generateScraplingScript(
    { flowName: '门店合约抓取', flowDefinition: {} },
    failingBackend(),
  );

  expect(script.filename).toBe('门店合约抓取.py');
});

it('离线模板把自己标成降级并带上失败原因', async () => {
  // 备份脚本一个页面都不抓，静默换掉真脚本、UI 照常报「已生成」，等于让人拿着空脚本去跑。
  const script = await generateScraplingScript(
    { flowName: '门店合约抓取', flowDefinition: {} },
    failingBackend('连接被拒绝'),
  );

  expect(script.degraded).toBe(true);
  expect(script.degradedReason).toBe('连接被拒绝');
  expect(script.content).toContain('连接被拒绝');
});

it('离线模板的 run() 直接报错而不是返回流程定义', async () => {
  // 老模板 return {"flow": ...} 并退出 0：重定向到文件就是一份看起来正常的 JSON，
  // 「一条数据都没抓到」被报成了成功。
  const script = await generateScraplingScript(
    { flowName: '门店合约抓取', flowDefinition: { nodes: [] } },
    failingBackend(),
  );

  expect(script.content).toContain('raise RuntimeError');
  expect(script.content).not.toContain('return {"flow"');
});

it('后端可用时结果不带降级标记', async () => {
  const real = { filename: '门店合约抓取.py', language: 'python', dependencies: [], content: '# real' };
  const script = await generateScraplingScript(
    { flowName: '门店合约抓取', flowDefinition: {} },
    { generateScript: async () => real },
  );

  expect(script).toEqual(real);
  expect(script.degraded).toBeUndefined();
});
