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

## 常用命令

| 命令                       | 说明                          |
| -------------------------- | ----------------------------- |
| `pnpm build`               | TypeScript 类型检查并构建前端 |
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

- [OVERVIEW.md](OVERVIEW.md)：完整能力说明。
- [PRODUCT.md](PRODUCT.md)：产品定位与设计原则。
- [PROJECT_GAP_ANALYSIS.md](PROJECT_GAP_ANALYSIS.md)：流程生成能力缺口与后续功能建议。
- [ELECTRON_PACKAGING.md](ELECTRON_PACKAGING.md)：桌面端打包说明。
- [backend/README.md](backend/README.md)：后端服务说明。

## 当前功能边界

- 主要面向 Web 自动化，不覆盖原生桌面 GUI 自动化。
- 图形验证码、滑块验证、点选验证目前以人工协同和后续扩展为主。
- 复杂前端组件如日期范围、多选下拉、级联选择、iframe、Shadow DOM 仍是后续重点增强方向。
