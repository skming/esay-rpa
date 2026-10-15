# Easy RPA 开发指南

用户操作见 [使用指南](OVERVIEW.md)。本文维护环境、实现入口与验证方法。

## 开发环境

需要 Node.js、pnpm、uv 和 Python 3.12+。在仓库根目录开始：

```bash
pnpm install
cd backend
uv sync --locked
uv run playwright install chromium
cd ..
pnpm stack:dev
```

`pnpm dev` 和 `pnpm backend:dev` 可分别启动前后端。后端默认监听 `127.0.0.1:8765`；端口冲突时先确认进程归属。

## 架构与源码入口

Electron 承载 React 前端，通过桥接调用本机 FastAPI 服务。SQLite 保存流程与任务；浏览器操作由 Playwright 或 Chrome 扩展执行；`browser.fetch` 使用 Scrapling；AI 请求通过 LiteLLM 路由。

| 模块 | 主要入口 |
| --- | --- |
| 节点配置与校验 | `src/types/rpa.ts`、`src/lib/flowDefinition.ts`、`src/lib/nodeConfigDraft.ts`、`src/lib/runValidation.ts` |
| 任务执行 | `backend/app/services/task_manager.py` 与各类 `*_action_runner.py` |
| 浏览器两端 | `backend/app/services/browser_action_runner.py`、`backend/app/services/extension_executor.py`、`extension/entrypoints/` |
| 分页判定 | `backend/app/services/pagination_runtime.py` |
| AI 编排 | `backend/app/services/ai_orchestrator.py`、`ai_phases.py`、`ai_guards.py` |
| 模型通信 | `backend/app/services/ai_model_transport.py`：供应商适配、流式解析、超时与响应释放 |
| 工具注册 | `backend/app/services/ai_tool_registry.py`：公开边界、读写分类、执行入口与后处理关联 |
| 提示词与工具 | `backend/app/services/ai_prompts.py`、`ai_tools/schemas.py`、`ai_tools/catalog.py` |
| 输出验收 | `backend/app/services/acceptance_contract.py`、`ai_checks.py` |
| 脚本导出 | `backend/app/services/code_generator.py` |

节点字段平铺在流程节点上。修改字段或行为时同步配置保存与恢复、校验、执行器、节点目录和相关测试；不支持等价导出的行为应明确拒绝。

### 分页执行

配置与结束条件见 [分页采集](OVERVIEW.md#分页采集)。两端共用 `collect_paginated_pages`，以实际 URL 和全部提取行生成快照；扩展回执提供 URL 与加载状态，连续两次就绪且稳定后接受新页。

实现时还需保留以下检查：

- 点击式在第一页停止时检查是否存在更多分页证据；自定义 class 不能单独证明禁用。
- URL 回跳的实际地址必须不同于请求地址；配置的下一页控件仍可用时不接受回跳。
- 空页需等待到超时预算结束，仍连续就绪且下一页禁用才可结束。空终止页与回跳页不计入数据页数。
- 任务管理器禁止失败重试或跳过，避免在已改变的浏览器位置重新开始而漏页。

### AI 与证据约束

- 提示词维护单一规则源，工具参数以 `TOOL_SCHEMAS` 为准；必需的不变量由 Guard、阶段准入和执行器强制检查。
- 阶段工具集合与准入共用判断；状态块、按需目录与历史摘要减少重复查询。续接状态从真实历史恢复，避免压缩后丢失目标或重复执行。
- 工具返回与后台轮询共用状态提交入口，核验流程版本并去重运行结果，再更新证据、预算与检查点。工具 Schema 必须与注册表的公开集合完全匹配。
- 页面证据绑定所属会话。静态抓取证据只证明 HTTP 提取可用，不能证明浏览器交互成功。
- 验收使用绑定用户需求的契约和真实运行输出；修改最终流程后需重新取得执行证据。
- 工具读出与持久化日志需脱敏；敏感动作确认只授权一次执行。

模型目录见 `backend/config/model_catalog.json`；脚本依赖能力见 `backend/app/services/ai_tools/script_capabilities.py`。具体规则、预算与缓存时限由对应模块维护。

## 验证

首次验证先安装扩展依赖；扩展源码变化后重新构建，真实测试不会自动更新构建产物。

```bash
pnpm --dir extension install
pnpm --dir extension compile
pnpm --dir extension build
pnpm test
pnpm build
pnpm backend:lint
pnpm backend:test -ra
```

`pnpm test` 包含工具脚本与前端测试，`pnpm build` 包含类型检查、ESLint 和前端构建。真实浏览器用例需要 Chromium 和 `extension/.output/chrome-mv3`；缺件或浏览器不可用时部分用例会跳过，应检查 `-ra` 汇总。

按变更范围运行对应测试。分页回归入口包括 `test_pagination_runtime.py`、两端 runner 测试、`test_task_manager.py`、`test_real_executor_parity.py` 与 `test_real_page_flow_replay.py`。验证层级与在线模型评测见 [AI 评测](../backend/evals/README.md)。

## 数据目录

默认根目录为 `~/.easy-rpa/`，可通过 `RPA_APP_DATA_DIR` 修改。

| 目录 | 内容 |
| --- | --- |
| `db/` | 流程、调度和任务记录 |
| `ai/` | 模型配置、对话、修复台账、检查点和站点证据 |
| `runtime/` | 浏览器 Profile、Cookie 与抓取缓存 |
| `workspace/runs/` | 按流程和任务隔离的运行产物 |
| `cache/` | 内联脚本等可重建文件 |
| `logs/` | 后端、桌面端和扩展动作审计日志 |

`backend.log` 记录服务异常，`backend-process.log` 记录后端启动与浏览器安装输出，`electron.log` 记录桌面端异常，`extension_bridge_audit.jsonl` 记录动作归属、耗时与结果，不记录动作输入值或提取内容。

日志按 UTC 日期归档，默认保留 30 天；后端归档可通过 `RPA_LOG_BACKUP_COUNT`、`RPA_LOG_RETENTION_DAYS` 调整，级别使用 `RPA_LOG_LEVEL`、`RPA_LOG_LEVELS`。流程执行日志在任务记录中查看。
