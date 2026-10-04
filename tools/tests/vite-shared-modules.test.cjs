const assert = require('node:assert/strict');
const { mkdtemp, rm } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { join, resolve } = require('node:path');
const { test } = require('node:test');
const { pathToFileURL } = require('node:url');

test('Vite 开发服务将共享 CommonJS 模块转换为可加载的浏览器入口', async () => {
  const { createServer } = await import('vite');
  const root = resolve(__dirname, '../..');
  const cacheDir = await mkdtemp(join(tmpdir(), 'rpa-vite-test-'));
  let server;
  try {
    server = await createServer({
      root,
      configFile: join(root, 'vite.config.ts'),
      cacheDir,
      logLevel: 'error',
      optimizeDeps: { noDiscovery: true, entries: [] },
      server: { host: '127.0.0.1', port: 0, strictPort: false, hmr: false, open: false },
    });
    await server.listen();
    const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
    for (const [consumer, moduleName, exportName, argument, expected] of [
      ['backendClient', 'backendPayloads', 'normalizeLimit', 250, 200],
      ['browserBridge', 'runtimeEvents', 'readBackendTotalSteps', { totalSteps: 7 }, 7],
    ]) {
      const response = await fetch(`${origin}/src/lib/${consumer}.ts`);
      assert.equal(response.status, 200);
      const source = await response.text();
      const importMatch = source.match(new RegExp(`import \\w+ from "([^"]+/deps/[^"/]*${moduleName}[^"/]+)"`));
      assert.ok(importMatch, `${consumer} must import the ESM prebundle instead of raw CommonJS`);
      const moduleResponse = await fetch(new URL(importMatch[1], origin));
      assert.equal(moduleResponse.status, 200);
      const moduleSource = await moduleResponse.text();
      assert.match(moduleSource, /\bexport\s+(?:default|\{)/);
      const modulePath = decodeURIComponent(new URL(importMatch[1], origin).pathname).slice('/@fs'.length);
      const esm = await import(pathToFileURL(modulePath).href);
      assert.equal(esm.default[exportName](argument), expected);
    }
  } finally {
    await server?.close();
    await rm(cacheDir, { recursive: true, force: true });
  }
});
