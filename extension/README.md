# Easy RPA Chrome Extension

Chrome 扩展让 Easy RPA 可以操作用户真实浏览器标签页，复用当前 Chrome 登录态。它适合企业 SSO、需要先由用户完成验证的站点，以及必须在真实浏览器中完成的页面操作。

## 客户端安装与使用

打包客户端已包含扩展文件，设置页提供安装入口和连接状态。加载客户端提供的扩展后会自动连接，无需输入扩展 ID。此前加载的旧版扩展需在 Chrome 扩展管理页重新加载。流程可在顶部运行菜单选择“当前 Chrome”。客户端重启后扩展会自动恢复连接。

## 开发启动

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

在 Chrome 扩展管理页加载 WXT 输出的开发扩展。扩展会连接：

```text
ws://127.0.0.1:8765/ws/extension/bridge
```

## 检查连接

```bash
curl http://127.0.0.1:8765/api/extension/status
```

返回结果中的 `canExecute` 为 `true` 表示扩展已启用、连接可用，可以执行流程。

## 支持能力

- 页面快照：`query`，返回 `ref/name/text/rect/visible`
- 语义查找：`find`
- 浏览器动作：`browser.open`、`click`、`fill`、`extract`、`hover`、`select`、`press`、`scroll`
- 标签页：`browser.tab.open`、`browser.tab.switch`、`browser.tab.close`
- 截图：`browser.screenshot`
- 可视化：目标元素高亮、敏感操作确认 Banner
- 可信输入：动作带 `trusted: true` 时通过 Chrome Debugger/CDP 执行点击或输入

## 使用边界

- 扩展操作的是用户真实 Chrome，不适合作为无人值守主路径。
- 助手探索传入 URL 时复用目标标签页或新建标签页；省略 URL 时绑定当前活动页。后续观察与交互保持该绑定。
- 流程运行的受控标签页与助手探索会话分别管理。
- 跨域 iframe 不能由 content script 直接读取。
- CDP 可信输入会短暂显示 Chrome 调试提示。
- 破坏性动作应启用敏感操作确认并保留审计日志。
