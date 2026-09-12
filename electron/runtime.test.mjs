import { createRequire } from 'node:module';
import { expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { createRuntimeController, generateScraplingScript } = require('./runtime.cjs');

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
    { backend: 'mock', flowName: '门店合约抓取', flowDefinition: { nodes: [] } },
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
