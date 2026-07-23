# Easy RPA

Easy RPA 是一个本地桌面端 RPA（机器人流程自动化）工具，用于通过可视化流程画布创建、运行和调试 Web 自动化任务。项目面向开发者和技术用户，重点覆盖网页采集、表单填写、分页抓取、文件/Excel 处理、定时调度和 AI 辅助流程生成。

## 核心能力

- 可视化流程编排：基于节点和连线组织浏览器操作、控制流、变量、脚本和文件处理。
- Web 自动化：打开页面、点击、填表、滚动、等待、截图、表格/列表提取。
- 数据处理：JSON、正则、字符串、列表、数学、类型转换、加解密等轻量处理。
- 文件与 Excel：读写文件、CSV/JSON/Excel 导出、Excel 行操作。
- 定时调度：基于 Cron 表达式触发流程，查看运行历史。
- AI 助手：通过工具调用创建流程、修改节点、运行流程、读取错误、检查 DOM、审计输出。

## 技术栈

| 层级   | 技术                                                     |
| ------ | -------------------------------------------------------- |
| 桌面端 | Electron                                                 |
| 前端   | React、TypeScript、Tailwind CSS、Radix UI、@xyflow/react |
| 后端   | Python 3.12+、FastAPI、SQLite                            |
| 自动化 | Playwright、Scrapling                                    |
| AI     | LiteLLM 多模型供应商路由                                 |

## 快速启动

安装前端依赖：

```bash
pnpm install
```

启动完整开发栈：

```bash
pnpm stack:dev
```

仅启动前端：

```bash
pnpm dev
```

仅启动后端：

```bash
pnpm backend:dev
```

端口占用：

```bash
lsof -ti :8765 | xargs kill
```

## 执行模式

| 模式 | 适用场景 | 说明 |
| --- | --- | --- |
| Playwright | 默认后台自动化、定时任务、无人值守流程 | 使用 Easy RPA 管理的浏览器 Profile，稳定可复现 |
| Chrome 扩展 | 复用用户真实 Chrome 登录态、企业 SSO、人机协同 | 需要扩展在线并控制当前标签页，不建议作为无人值守主路径 |

扩展开发与调试见 [extension/README.md](extension/README.md)。

## 常用命令

| 命令                       | 说明                          |
| -------------------------- | ----------------------------- |
| `pnpm build`               | 类型检查 + ESLint + 构建前端  |
| `pnpm lint`                | 前端 ESLint                   |
| `pnpm backend:lint`        | 后端 ruff                     |
| `pnpm test`                | 运行前端测试                  |
| `pnpm backend:test`        | 运行后端测试                  |
| `pnpm icons:generate`      | 重新生成应用图标              |
| `pnpm electron:pack`       | 生成 macOS 目录包             |
| `pnpm electron:dist`       | 构建 macOS 发布包             |
| `pnpm electron:dist:win`   | 构建 Windows 发布包           |
| `pnpm electron:dist:linux` | 构建 Linux 发布包             |

## 数据目录

默认本地数据目录：

```text
~/.easy-rpa/
```

主要包含：

- `db/`：流程、调度、任务记录。
- `ai/`：AI 配置和对话记录。
- `runtime/browser/`：浏览器 Profile 与 Cookie。
- `workspace/runs/`：流程运行产物。
- `logs/`：后端日志。

## 项目文档

- [OVERVIEW.md](OVERVIEW.md)：完整能力说明（节点清单、执行模型、AI 助手、数据存储）。
- [PRODUCT.md](PRODUCT.md)：产品定位与设计原则简报。
- [DESIGN.md](DESIGN.md)：视觉设计系统规范（色板、字体、组件、Do's and Don'ts）。
- [ELECTRON_PACKAGING.md](ELECTRON_PACKAGING.md)：桌面端打包、签名与发布说明。
- [extension/README.md](extension/README.md)：Chrome 扩展开发与调试。
- [backend/evals/README.md](backend/evals/README.md)：RPA 助手行为回归评测集。

## 当前功能边界

- 主要面向 Web 自动化，不覆盖原生桌面 GUI 自动化。
- 图形验证码、滑块验证、点选验证不做自动破解：运行时检测到验证组件会自动暂停转人工完成后重试（有头模式），或在错误信息中提示（无头模式）。
- iframe（`>>>` 穿透语法）与 open Shadow DOM（CSS 自动穿透）已支持；closed Shadow DOM 不可及。
- 复杂前端组件如日期范围、多选下拉、级联选择仍是后续重点增强方向。
