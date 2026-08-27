# Easy RPA Chrome Extension

Chrome 扩展让 Easy RPA 可以操作用户真实浏览器标签页，复用当前 Chrome 登录态。它是 Playwright 后台执行器的补充，适合企业 SSO、验证码前后的人机协同和必须在真实浏览器中完成的页面操作。

## 启动

先启动桌面端/后端：

```bash
pnpm run stack:dev
```

再启动扩展开发服务：

```bash
cd extension
pnpm install
pnpm dev
```

在 Chrome 扩展管理页加载 WXT 输出的开发扩展。扩展启动后会连接：

```text
ws://127.0.0.1:8765/ws/extension/bridge
```

## 检查连接

```bash
curl http://127.0.0.1:8765/api/extension/status
```

返回 `{"connected": true}` 表示扩展已连接后端。

## 支持能力

- 页面快照：`query`，返回 `ref/name/text/rect/visible`
- 语义查找：`find`
- 浏览器动作：`browser.open`、`click`、`fill`、`extract`、`hover`、`select`、`press`、`scroll`
- 标签页：`browser.tab.open`、`browser.tab.switch`、`browser.tab.close`
- 截图：`browser.screenshot`
- 可视化：目标元素高亮、人工接管 Banner
- 可信输入：动作带 `trusted: true` 时通过 Chrome Debugger/CDP 执行点击或输入

## 手工测试

查询当前受控标签页元素：

```bash
curl -X POST http://127.0.0.1:8765/api/extension/execute \
  -H 'content-type: application/json' \
  -d '{"action":{"type":"query"}}'
```

点击元素：

```bash
curl -X POST http://127.0.0.1:8765/api/extension/execute \
  -H 'content-type: application/json' \
  -d '{"action":{"type":"browser.click","selector":"button"}}'
```

可信点击：

```bash
curl -X POST http://127.0.0.1:8765/api/extension/execute \
  -H 'content-type: application/json' \
  -d '{"action":{"type":"browser.click","selector":"button","trusted":true}}'
```

## 使用边界

- 扩展操作的是用户真实 Chrome，不适合作为无人值守主路径。
- 当前动作默认绑定受控标签页；首次动作会使用当前激活标签页。
- 跨域 iframe 不能由 content script 直接读取。
- CDP 可信输入会短暂显示 Chrome 调试提示。
- 破坏性动作应配合人工确认和审计日志使用。
