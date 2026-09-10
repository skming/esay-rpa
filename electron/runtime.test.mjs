import { createRequire } from 'node:module';
import { expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { createRuntimeController } = require('./runtime.cjs');

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

it('后端不可用时仍回落本地模拟', async () => {
  const controller = controllerRejectingWith(new Error('fetch failed'));
  const win = createFakeWindow();

  const started = await controller.startRun(win, { flowId: 'flow-1' });

  expect(started.status).toBe('running');
  expect(win.events.some((event) => event.type === 'run:start')).toBe(true);

  // activeRun 是模块级状态，模拟运行留下的定时器会跨用例存活。
  await controller.stopRun();
});
