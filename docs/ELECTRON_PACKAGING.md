# Electron 打包说明

Easy RPA 桌面端使用 Electron + Vite 构建，打包工具为 `electron-builder`。当前桌面包会同时携带前端 UI、Electron Main Process 和 Python FastAPI 后端运行时，应用启动后由 Electron 主进程自动托管后端服务。以下命令在仓库根目录执行。

## 本地验证目录包

```bash
pnpm electron:pack
```

产物目录：

```text
release/mac-arm64/Easy RPA.app
```

打包前会执行后端运行时门禁：

```bash
pnpm backend:bundle:prepare
pnpm backend:bundle:verify
```

`backend:bundle:prepare` 会把 uv 管理的 Python 3.12 复制到 `backend/.bundle-python`，并把锁定的生产依赖安装到 `backend/.bundle-venv`；
`backend:bundle:verify` 会确认 `backend/app/main.py`、`backend/config/model_catalog.json`、
`backend/.bundle-python/bin/python3.12`、`backend/.bundle-venv/lib/python3.12/site-packages`
和关键依赖存在。正式包启动后端时优先使用 `resources/backend/python/bin/python3.12`，
并通过 `PYTHONPATH` 加载 `resources/backend/.venv` 中的依赖，避免依赖构建机本地 Miniconda/Homebrew 路径。

macOS 的 arm64 与 x64 包必须分别在对应架构的 runner 上构建。打包完成后会校验 Electron、Python、原生依赖架构，并启动随包后端检查 `/api/health`。

## 生成安装包

| 平台 | 命令 | 产物 |
| --- | --- | --- |
| macOS | `pnpm electron:dist` | `.dmg`、`.zip` |
| Windows | `pnpm electron:dist:win` | NSIS `.exe` |
| Linux | `pnpm electron:dist:linux` | `.AppImage` |

各平台必须在对应系统构建。包内包含平台相关的 Python/Playwright 原生运行时，不能在 macOS 上交叉生成可用的 Windows/Linux 包。

默认 `electron:pack` / `electron:dist` 会跳过代码签名，用于本地和普通 CI 验证可重复打包。正式发布使用：

```bash
pnpm electron:dist:signed
```

未签名 macOS 包仅用于内部测试。打包时会执行一次 ad-hoc 重签，保证 `.app`
包结构在 macOS 上是有效的；接收机器仍需移除 quarantine 后才能打开：

```bash
xattr -dr com.apple.quarantine "/Applications/Easy RPA.app"
```

macOS 签名和 notarization（苹果公证）由 `RPA_RELEASE_SIGN=1` 开启，配置位于 `electron-builder.config.cjs`。发布前先校验凭据：

```bash
node tools/verify_release_env.cjs darwin
```

必需环境变量：

```bash
CSC_LINK=...
CSC_KEY_PASSWORD=...
APPLE_ID=...
APPLE_APP_SPECIFIC_PASSWORD=...
APPLE_TEAM_ID=...
```

Windows 签名校验：

```bash
node tools/verify_release_env.cjs win32
```

必需环境变量：

```bash
CSC_LINK=...
CSC_KEY_PASSWORD=...
```

仓库提供 `.github/workflows/release.yml`，在 tag `v*` 或手动触发时构建上述平台产物；macOS 执行签名与公证。GitHub Secrets 名称与上面的环境变量一致。

## 后端运行模型

桌面端默认在本机启动并连接：

```text
http://127.0.0.1:8765
```

打包产物内的 backend 资源位于 `resources/backend`，包含：

- `backend/app`（已过滤 `__pycache__/` 和 `.pyc`/`.pyo` 文件）
- `backend/config`（含 `model_catalog.json` AI 模型目录）
- `backend/python`（可随包携带的 Python 3.12 运行时）
- `backend/.venv`（打包时过滤 `__pycache__/`、`.pyc`、测试目录和缓存文件）
- `backend/pyproject.toml`
- `backend/uv.lock`

运行期数据不写入应用包，位置与布局见 [数据目录](TECHNICAL.md#数据目录)。

开发态如需改连外部后端，仍可在启动桌面端前设置：

```bash
export RPA_BACKEND_URL=http://127.0.0.1:8765
```

## 冷启动测量

以下示例以平均 3 秒作为测量阈值，不代表已达到该性能。先生成目录包，再连续测量 5 次：

```bash
pnpm electron:pack
node tools/measure_electron_startup.cjs \
  --runs 5 \
  --max-average-ms 3000 \
  --output output/bench/electron-startup.json
```

测量脚本会设置 `RPA_STARTUP_PROBE=1`，Electron 主进程在主窗口 `ready-to-show` 时输出结构化事件；脚本以 `spawn` 到该事件的耗时作为冷启动样本。默认连接不可达的本地后端端口，避免后端网络请求影响首屏窗口可用时间。

测量边界：

- 该测量只能证明当前操作系统和当前硬件上的目录包冷启动，不代表 Windows/Linux 或签名安装包。
- macOS 首次运行未签名 app 可能触发 Gatekeeper 或系统缓存抖动，首次样本可能高于后续样本。
- `ready-to-show` 表示窗口可展示，不等同于用户完成一次业务操作所需的端到端时间。

复测要求：

- macOS 和 Windows 各自运行 5 次，保留 JSON 产物和机器规格。
- 正式发布包需要在签名、公证、安装后再重复测量。
- 若均值接近 3 秒，继续拆分 renderer chunk、延迟加载 React Flow 低频面板，并复测。

## 常见问题

- Electron 生产模式使用 `loadFile` 加载 `dist/index.html`，Vite `base` 必须保持 `./`，否则静态资源会按文件系统根路径解析导致白屏。
- `electron-builder` 的 Windows 安装器依赖可能触发 pnpm build-script 审批提示，跨平台制品应在对应平台 CI 中单独验证。

## 发布验证

- 每次打包前执行 `pnpm build` 和 Electron 主进程语法检查。
- 按 [本地验证目录包](#本地验证目录包) 完成后端运行时门禁；对外发布前按 [生成安装包](#生成安装包) 完成凭据校验与签名。
- 生产发布时将桌面端与后端服务版本一起记录，避免 API 合约漂移。
- 验证产物内无 `__pycache__/` 目录、`.pyc` 文件、测试目录或缓存文件（由 `electron-builder.config.cjs` 的 `filter` 规则自动排除）。
- `release/` 是构建产物目录，已加入 `.gitignore`，不要纳入源码管理。
