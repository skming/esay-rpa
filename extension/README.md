# Chrome 扩展开发

客户端安装和使用见 [使用指南](../docs/OVERVIEW.md#创建与运行)。

## 启动与连接

先在仓库根目录启动 `pnpm stack:dev`，再运行：

```bash
cd extension
pnpm install
pnpm dev
```

在 Chrome 扩展管理页加载 WXT 输出的开发扩展。默认桥接地址为 `ws://127.0.0.1:8765/ws/extension/bridge`。

```bash
curl http://127.0.0.1:8765/api/extension/status
```

`canExecute=true` 表示扩展已启用且连接可执行。

## 执行边界

- 助手探索与流程运行分别管理受控标签页；探索传入 URL 时复用目标页或新建页，省略 URL 时绑定当前活动页。
- content script 提供页面观察与 DOM 动作；可信点击和输入通过 Chrome Debugger/CDP 执行，会短暂显示调试提示。
- content script 不能直接读取跨域 iframe。敏感动作通过确认 Banner 执行，并记录审计日志。
- 分页协议与回执要求见 [分页执行](../docs/TECHNICAL.md#分页执行)。

## 回归验证

修改协议后同时使用对应版本的后端与扩展，按 [开发验证](../docs/TECHNICAL.md#验证) 准备并构建，在仓库根目录运行两端一致性测试：

```bash
pnpm backend:test tests/test_real_executor_parity.py -ra
```

测试使用临时 Profile 和本地页面，覆盖异步分页、安全上限和 URL 回跳。
