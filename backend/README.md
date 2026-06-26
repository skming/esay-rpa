# Easy RPA Backend

FastAPI + Scrapling 后端服务，负责执行采集任务、生成 Python 脚本、推送运行日志。

运行时默认使用内存任务队列，并发为 2。可通过 `RPA_TASK_CONCURRENCY` 调整并发数。生产环境可切换 Redis 队列后端，不需要改动 API 合约。

## 本地启动

```bash
cd backend
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

首次安装依赖：

```bash
cd backend
uv sync
```

## Docker 部署

只构建后端镜像：

```bash
docker build -t easy-rpa-backend ./backend
```

默认基础镜像为 `pyd4vinci/scrapling:latest`，它包含 Scrapling 浏览器自动化运行所需的系统依赖。生产环境建议固定 tag：

```bash
docker build \
  --build-arg SCRAPLING_BASE_IMAGE=pyd4vinci/scrapling:latest \
  -t easy-rpa-backend ./backend
```

启动完整依赖栈：

```bash
docker compose up --build
```

默认会启动 PostgreSQL、Redis、MinIO 和 FastAPI 后端，并启用：

```bash
RPA_TASK_STORE_BACKEND=sqlalchemy
RPA_FLOW_STORE_BACKEND=sqlalchemy
RPA_SCHEDULE_STORE_BACKEND=sqlalchemy
RPA_TASK_QUEUE_BACKEND=redis
RPA_ARTIFACT_STORE_BACKEND=minio
```

无 Docker 环境下可先执行静态合约检查，确认 Compose 文件仍包含必需服务、健康检查、持久化挂载和后端环境变量：

```bash
node tools/verify_compose_config.cjs
```

有 Docker 环境时执行真实校验：

```bash
docker compose config
docker compose build backend
docker compose up -d postgres redis minio backend
curl -fsS http://127.0.0.1:8765/api/health
curl -fsS http://127.0.0.1:8765/api/queue
docker compose down -v
```

仓库已提供 GitHub Actions 工作流 `.github/workflows/ci.yml`，在 Linux runner 上执行 `docker compose config`、后端镜像构建、依赖栈启动、健康检查和 Redis 队列后端校验。

Pitfalls：

- `docker compose up` 首次启动会执行 `database/init.sql`，如果 PostgreSQL volume 已存在，初始化脚本不会重复执行。
- 示例账号和密钥只用于本地开发，不能直接用于生产环境。
- `latest` tag 可能随上游镜像更新而变化，生产镜像应固定到经过验证的 tag 或 digest。
- 本地静态合约检查只能发现配置缺漏，不能证明镜像可构建或容器可启动。

Mitigation：

- Schema 变更后本地可执行 `docker compose down -v` 重建数据卷；生产环境必须使用迁移脚本，不要直接删除 volume。
- 生产环境通过 Secret Manager 注入数据库、Redis、MinIO 凭据。
- CI 中记录 `SCRAPLING_BASE_IMAGE` 的完整 tag/digest，确保浏览器依赖和 Scrapling 版本可追溯。
- Docker 实机验收以 CI 或有 Docker 的本机输出为准，静态检查只作为前置门禁。

## 健康检查

```bash
curl http://127.0.0.1:8765/api/health
```

## 队列状态

```bash
curl http://127.0.0.1:8765/api/queue
```

## 统一验收审计

执行统一审计，汇总当前工作区已有产物，不把 smoke 或静态检查误判为正式验收通过：

```bash
cd backend
uv run python tools/audit_acceptance.py \
  --output ../output/bench/acceptance-audit.json
```

需要在 CI 或发布门禁中强制所有验收项完成时，加上 `--enforce`：

```bash
uv run python tools/audit_acceptance.py --enforce
```

状态口径：

- `passed`：当前产物能直接证明该项达标。
- `incomplete`：已有部分证据，但验收范围不足，例如只有短周期 smoke，未跑满 7 天。
- `missing`：缺少应有的机器可读产物。
- `blocked_by_external_dependency`：需要外部环境、平台或凭据，例如 Docker CLI、Apple Developer 签名凭据、Windows/Linux runner、授权 Cloudflare/DataDome 目标。

当前审计覆盖：前端 shadcn/Radix 组件约束、100 并发与 Cron 误差、Selector 回归、静态页面 smoke/7 天监控、反爬 smoke/真实目标、Compose 静态/实机运行、Electron macOS/Windows/Linux 冷启动、macOS 签名与公证。

Pitfalls：

- `acceptance-audit.json` 是证据总账，不会替你生成 7 天监控、Docker 实机、跨平台包或签名公证产物。
- smoke 通过只能证明工具链可运行，不能扩大解释为真实站点、长周期或跨平台验收通过。

Mitigation：

- 每个正式验收项都保留 JSON 产物到 `backend/storage/**` 或 `output/bench/**`，再运行统一审计。
- 外部依赖项在具备授权目标、Docker、Apple 凭据或对应 OS runner 后补跑，避免在本地用静态检查替代真实运行。

## 压测与验收指标

后端提供进程内压测工具，用合成 runner 绕过外网和浏览器波动，专门验证 100 并发入队、队列 active/queued 指标和 Cron 调度误差。

```bash
cd backend
uv run python tools/load_test.py \
  --tasks 100 \
  --create-concurrency 100 \
  --worker-concurrency 16 \
  --cron-samples 5 \
  --json-output storage/bench/load-test.json
```

默认启用验收退出码：

- 100 个任务必须全部创建成功。
- 100 个任务必须全部完成。
- Cron 最大误差必须不超过 `5000ms`。

只采集指标、不让验收失败影响退出码：

```bash
uv run python tools/load_test.py --no-enforce
```

Pitfalls：

- 该脚本验证平台队列和调度链路，不代表真实 Scrapling 抓取吞吐。
- `--worker-concurrency` 过高会放大本机 CPU 和事件循环调度抖动，Cron 误差不能直接等同生产环境表现。

Mitigation：

- CI 中固定 `--worker-concurrency`、`--runner-sleep-ms` 和机器规格，保留 `storage/bench/load-test.json` 作为验收附件。
- 真实站点吞吐应结合下面的静态页面监控脚本单独跑。

## 静态页面成功率监控

用于验证 `books.toscrape.com` 静态页面采集成功率。默认输出 JSONL，便于连续 7 天监控后汇总。

短周期冒烟：

```bash
cd backend
uv run python tools/monitor_static_success.py \
  --cycles 3 \
  --interval-seconds 0 \
  --reset-output \
  --output storage/monitor/static-success-smoke.jsonl
```

7 天监控示例：

```bash
cd backend
uv run python tools/monitor_static_success.py \
  --cycles 10080 \
  --interval-seconds 60 \
  --success-threshold 0.99 \
  --output storage/monitor/static-success-7d.jsonl
```

如果后端已通过 Docker 或本地 uvicorn 启动，可以指定真实服务地址：

```bash
uv run python tools/monitor_static_success.py \
  --base-url http://127.0.0.1:8765 \
  --cycles 10080 \
  --interval-seconds 60 \
  --success-threshold 0.99
```

汇总已有 JSONL 并生成验收报告：

```bash
uv run python tools/summarize_monitor.py \
  --input storage/monitor/static-success-7d.jsonl \
  --success-threshold 0.99 \
  --expected-min-records 10080 \
  --expected-min-hours 168 \
  --json-output storage/monitor/static-success-7d-summary.json
```

Pitfalls：

- 7 天监控会受本机睡眠、网络出口、目标站可用性影响。
- 默认 selector 面向 `books.toscrape.com` 当前页面结构，目标站更新时可能需要重新确认 selector。
- 短周期冒烟只能证明工具链可运行，不能证明连续 7 天 `>=99%`。

Mitigation：

- 监控机器关闭休眠，并使用进程守护工具或 CI 定时任务保活。
- 每轮记录 `status`、`latencyMs`、`resultCount` 和 `error`，失败时优先检查 JSONL 中的错误详情。
- 7 天验收以 `summarize_monitor.py` 输出的 `records`、`durationHours`、`successRate` 和 `passed` 为准。

## 反爬页面成功率评测

执行方案要求 Cloudflare / DataDome 等反爬页面采集成功率 `>=90%`。真实目标不能硬编码进仓库，应由测试方提供合法授权目标清单。

目标清单模板：

```bash
cp config/anti_bot_targets.example.json config/anti_bot_targets.local.json
```

示例结构：

```json
[
  {
    "name": "cloudflare-real-site",
    "category": "cloudflare",
    "target_url": "https://example-protected-site.invalid/",
    "selector": "main",
    "fetcher": "stealthy",
    "extract_mode": "text",
    "min_count": 1,
    "timeout_ms": 90000
  }
]
```

工具链 smoke，不依赖真实反爬站：

```bash
uv run python tools/anti_bot_benchmark.py \
  --attempts 1 \
  --success-threshold 0.90 \
  --output storage/bench/anti-bot-smoke.json
```

真实反爬目标评测：

```bash
uv run python tools/anti_bot_benchmark.py \
  --targets-file config/anti_bot_targets.local.json \
  --attempts 10 \
  --attempt-interval-seconds 30 \
  --success-threshold 0.90 \
  --output storage/bench/anti-bot-benchmark.json
```

Pitfalls：

- Cloudflare / DataDome 站点策略会随时间、地区、账号状态和 IP 信誉变化，单次通过不能代表长期成功率。
- 未授权测试第三方反爬站可能违反对方服务条款。
- `fetcher=stealthy` 会启动浏览器，资源消耗和执行时间明显高于静态抓取。
- 单目标单次测试只适合 smoke，不能证明 `>=90%` 的统计稳定性。

Mitigation：

- 仅使用授权测试目标或自有防护环境。
- 每个目标至少采样 10 次，记录 `byCategory`、`byTarget`、结果数量、失败错误和时间戳，保留 `anti-bot-benchmark.json` 作为验收附件。
- 失败时先用 `site:analyze` 和目标站稳定 selector 复核，再调整 fetcher 或超时时间。

## Selector 回归验证

用于模拟 CSS-in-JS 构建哈希变更后的 selector 存活率，并验证平台推荐 selector 的自动重定位能力。

```bash
cd backend
uv run python tools/selector_regression.py \
  --css-in-js-survival-target 0.85 \
  --adaptive-relocation-target 0.80 \
  --json-output storage/bench/selector-regression.json
```

验收口径：

- `recommendedSelectorSurvivalRate >= 0.85`：分析器推荐出的稳定 selector 在哈希变更后仍能命中目标。
- `relocationRate >= 0.80`：当原始哈希 selector 失效时，推荐 selector 能重定位到同一业务元素。
- `brittleSelectorFailureRate` 用于证明测试场景确实发生了哈希变更，不作为通过条件。

Pitfalls：

- 该工具使用本地 HTML 变体回归，验证 selector 策略，不等同于 Scrapling adaptive 在真实动态站点的完整行为。
- 如果业务页面没有 `data-testid`、`name`、`aria-label` 等稳定属性，推荐结果可能只能退化到标签或可读 class。

Mitigation：

- 企业内网页面应把自动化稳定属性纳入前端组件规范。
- 真实站改版后先运行 `/api/site/analyze`，确认候选 selector 的 `stabilityScore` 和命中数量，再写入流程定义。

## Redis 队列

默认配置：

```bash
RPA_TASK_QUEUE_BACKEND=memory
RPA_TASK_CONCURRENCY=2
```

启用 Redis：

```bash
export RPA_TASK_QUEUE_BACKEND=redis
export RPA_REDIS_URL=redis://127.0.0.1:6379/0
export RPA_TASK_QUEUE_NAME=rpa:tasks
```

Pitfalls：

- Redis 队列后端只负责跨进程排队状态，任务执行仍由当前 FastAPI Worker 消费。
- 多实例部署时必须保证每个实例可访问同一 Redis，并避免在滚动发布时直接清空队列 key。

Mitigation：

- 上线前先用 `/api/queue` 验证 `backend=redis`。
- 发布脚本只迁移服务，不执行 `FLUSHDB` 或删除 `rpa:tasks*`。

## 创建任务

```bash
curl -X POST http://127.0.0.1:8765/api/tasks \
  -H 'content-type: application/json' \
  -d '{
    "flowName": "订单自动处理",
    "flowId": "00000000-0000-0000-0000-000000000101",
    "targetUrl": "https://quotes.toscrape.com/",
    "selector": ".quote .text::text",
    "fetcher": "static",
    "extractMode": "text"
  }'
```

## 流程定义管理

流程定义可保存到 `rpa_flows`，用于版本管理、调度绑定和桌面端同步。

```bash
curl -X POST http://127.0.0.1:8765/api/flows \
  -H 'content-type: application/json' \
  -d '{
    "name": "订单自动处理",
    "version": "v3.0.2",
    "description": "订单流程编排定义",
    "status": "active",
    "definition": {
      "nodes": [
        {"id": "start", "type": "start"},
        {"id": "n1", "type": "browser.fetch"}
      ],
      "edges": [
        {"source": "start", "target": "n1"}
      ]
    }
  }'
```

读取流程列表：

```bash
curl http://127.0.0.1:8765/api/flows
```

按流程定义启动运行：

```bash
curl -X POST http://127.0.0.1:8765/api/flows/{flow_id}/run \
  -H 'content-type: application/json' \
  -d '{"mode": "run"}'
```

当前运行器会读取流程定义里的第一个 `browser.fetch` 节点，并将其中的 `targetUrl`、`selector`、`fetcher`、`extractMode` 等参数转换为 Scrapling 采集任务。

## 生成脚本

```bash
curl -X POST http://127.0.0.1:8765/api/code/generate \
  -H 'content-type: application/json' \
  -d '{
    "flowName": "订单自动处理",
    "targetUrl": "https://quotes.toscrape.com/",
    "selector": ".quote .text::text"
}'
```

## 站点与选择器分析

用于在运行前检查 selector 是否命中、是否依赖疑似 CSS-in-JS 哈希 class，并返回更稳定的候选 selector。

```bash
curl -X POST http://127.0.0.1:8765/api/site/analyze \
  -H 'content-type: application/json' \
  -d '{
    "targetUrl": "https://quotes.toscrape.com/",
    "selector": ".quote .text::text",
    "fetcher": "static",
    "maxCandidates": 8
  }'
```

运行任务和脚本生成支持 Scrapling adaptive 定位：

```json
{
  "adaptive": true,
  "autoSave": true
}
```

Pitfalls：

- `fetcher=dynamic` 或 `fetcher=stealthy` 会启动浏览器，耗时和资源占用高于静态抓取。
- CSS-in-JS 检测基于 class 形态和占比判断，是风险提示，不等同于绝对结论。

Mitigation：

- 对企业内网页面优先添加 `data-testid`、`name`、`aria-label` 等稳定属性。
- 对外部页面先调用 `/api/site/analyze`，再把推荐 selector 写入任务或调度配置。

## 数据库初始化

```bash
psql "$DATABASE_URL" -f ../database/init.sql
```

当前后端默认使用内存调度仓储。启用 SQLAlchemy 异步仓储后，调度配置会写入 PostgreSQL 的 `rpa_schedules` 表。

```bash
export RPA_TASK_STORE_BACKEND=sqlalchemy
export RPA_FLOW_STORE_BACKEND=sqlalchemy
export RPA_SCHEDULE_STORE_BACKEND=sqlalchemy
export DATABASE_URL=postgresql+asyncpg://rpa:rpa@127.0.0.1:5432/rpa
```

Pitfalls：

- `DATABASE_URL` 必须使用 SQLAlchemy async driver，例如 `postgresql+asyncpg://...`。
- API 创建的调度不一定绑定已有流程，因此 `rpa_schedules.flow_id` 允许为空。
- `RPA_FLOW_STORE_BACKEND=memory` 时流程定义只保存在当前进程内，服务重启后不可恢复。
- `RPA_TASK_STORE_BACKEND=memory` 时任务、结果和日志只保存在当前进程内，服务重启后不可恢复。
- 任务持久化只保存 artifact 元数据；artifact 文件内容仍由本地文件系统或 MinIO 后端负责。

Mitigation：

- 生产启动前先执行 `database/init.sql`，再启动后端。
- 启动后用 `GET /api/schedules` 验证调度可读取，创建一条调度后重启服务验证仍存在。
- 生产环境同时启用 `RPA_FLOW_STORE_BACKEND=sqlalchemy`、`RPA_TASK_STORE_BACKEND=sqlalchemy` 和持久化 artifact store，避免流程、任务记录与结果文件生命周期不一致。

## 创建调度

```bash
curl -X POST http://127.0.0.1:8765/api/schedules \
  -H 'content-type: application/json' \
  -d '{
    "name": "每日订单采集",
    "cronExpression": "0 9 * * *",
    "timezone": "Asia/Shanghai",
    "enabled": true,
    "task": {
      "flowName": "订单自动处理",
      "flowId": "00000000-0000-0000-0000-000000000101",
      "targetUrl": "https://quotes.toscrape.com/",
      "selector": ".quote .text::text",
      "timeoutMs": 30000
    }
  }'
```

## 手动触发调度

```bash
curl -X POST http://127.0.0.1:8765/api/schedules/{schedule_id}/trigger
```

后端启动后会自动扫描到期调度。也可以手动执行一次扫描：

```bash
curl -X POST http://127.0.0.1:8765/api/schedules:tick
```

## 查询采集结果文件

任务成功后会在 artifact store 中保存 `scrape-result.json`。默认使用本地文件系统：

```bash
curl http://127.0.0.1:8765/api/tasks/{task_id}/artifacts
```

默认本地路径：

```text
backend/storage/artifacts/{task_id}/scrape-result.json
```

## MinIO Artifact Store

启用 MinIO：

```bash
export RPA_ARTIFACT_STORE_BACKEND=minio
export RPA_MINIO_ENDPOINT=127.0.0.1:9000
export RPA_MINIO_ACCESS_KEY=minioadmin
export RPA_MINIO_SECRET_KEY=minioadmin
export RPA_MINIO_BUCKET=rpa-artifacts
export RPA_MINIO_SECURE=false
```

Pitfalls：

- MinIO SDK 是同步客户端，后端通过线程池调用，避免阻塞事件循环。
- Artifact 内容读取只允许通过平台已登记的 `artifactId`，不接受任意对象路径。
- `RPA_MINIO_SECURE=true` 时 endpoint 需要配置 HTTPS 访问。

Mitigation：

- 上线前确认 bucket 自动创建权限，或提前创建 `RPA_MINIO_BUCKET`。
- 生产环境不要使用默认 `minioadmin/minioadmin`，应通过密钥管理系统注入。
