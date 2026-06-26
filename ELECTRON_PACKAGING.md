# Electron 打包说明

Easy RPA 桌面端使用 Electron + Vite 构建，打包工具为 `electron-builder`。当前桌面包会同时携带前端 UI、Electron Main Process 和 Python FastAPI 后端运行时，应用启动后由 Electron 主进程自动托管后端服务。

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

`backend:bundle:prepare` 会把 uv 管理的 Python 3.12 复制到 `backend/.bundle-python`；
`backend:bundle:verify` 会确认 `backend/app/main.py`、`backend/config/model_catalog.json`、
`backend/.bundle-python/bin/python3.12`、`backend/.venv/lib/python3.12/site-packages`
和关键依赖存在。正式包启动后端时优先使用 `resources/backend/python/bin/python3.12`，
并通过 `PYTHONPATH` 加载 `resources/backend/.venv` 中的依赖，避免依赖构建机本地 Miniconda/Homebrew 路径。

## 生成安装包

```bash
pnpm electron:dist
```

`electron:dist` 只构建**当前操作系统**对应产物：

- macOS：`.dmg`、`.zip`
- Windows：NSIS `.exe`
- Linux：`.AppImage`

Windows 安装包需要在 Windows 环境执行：

```bash
pnpm electron:dist:win
```

Linux 安装包需要在 Linux 环境执行：

```bash
pnpm electron:dist:linux
```

不要在 macOS 上强行交叉构建 Windows/Linux 包。桌面包内包含平台相关的 Python/Playwright 原生运行时，跨平台构建会产出前端可打开但后端不可运行的坏包。

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

仓库提供 `.github/workflows/release.yml`，在 tag `v*` 或手动触发时构建：

- macOS：签名 + 公证 `.dmg` / `.zip`
- Windows：NSIS `.exe`
- Linux：`.AppImage`

GitHub Secrets 名称与上面的环境变量一致。

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

AI 模型目录（`backend/config/model_catalog.json`）在打包时自动随包携带，无需额外配置。若需新增或修改模型，直接编辑该 JSON 文件，重新打包即可生效。

运行期可写目录不再落到应用包内，而是由 Electron 注入到用户数据目录：

```text
<userData>/backend-runtime/workspace
<userData>/backend-runtime/artifacts
```

开发态如需改连外部后端，仍可在启动桌面端前设置：

```bash
export RPA_BACKEND_URL=http://127.0.0.1:8765
```

## 冷启动测量

执行方案要求 Electron 冷启动时间不超过 3 秒。先生成目录包，再用测量脚本连续启动 5 次并计算均值：

```bash
pnpm electron:pack
node tools/measure_electron_startup.cjs \
  --runs 5 \
  --max-average-ms 3000 \
  --output output/bench/electron-startup.json
```

测量脚本会设置 `RPA_STARTUP_PROBE=1`，Electron 主进程在主窗口 `ready-to-show` 时输出结构化事件；脚本以 `spawn` 到该事件的耗时作为冷启动样本。默认连接不可达的本地后端端口，避免后端网络请求影响首屏窗口可用时间。

产物：

```text
output/bench/electron-startup.json
```

Pitfalls：

- 该测量只能证明当前操作系统和当前硬件上的目录包冷启动，不代表 Windows/Linux 或签名安装包。
- macOS 首次运行未签名 app 可能触发 Gatekeeper 或系统缓存抖动，首次样本可能高于后续样本。
- `ready-to-show` 表示窗口可展示，不等同于用户完成一次业务操作所需的端到端时间。

Mitigation：

- macOS 和 Windows 各自运行 5 次，保留 JSON 产物和机器规格。
- 正式发布包需要在签名、公证、安装后再重复测量。
- 若均值接近 3 秒，继续拆分 renderer chunk、延迟加载 React Flow 低频面板，并复测。

## Pitfalls

- Electron 生产模式使用 `loadFile` 加载 `dist/index.html`，Vite `base` 必须保持 `./`，否则静态资源会按文件系统根路径解析导致白屏。
- 不要在打包态直接使用 `.venv/bin/python`。该文件在很多机器上是指向 Homebrew/Miniconda 的绝对软链接，正式包会依赖构建机本地路径。当前 Electron 主进程打包态优先使用 `resources/backend/python/bin/python3.12`。
- `pnpm add` 如遇到 store 路径不一致，需要使用项目内 store：`pnpm --store-dir .pnpm-store install`。
- `electron-builder` 的 Windows 安装器依赖可能触发 pnpm build-script 审批提示，跨平台制品应在对应平台 CI 中单独验证。
- Windows `.exe` 不会在 macOS 打包命令中出现；必须在 Windows 本机或 Windows CI 构建。
- macOS 未签名产物会被 Gatekeeper（macOS 应用安全校验）拦截，不应直接发给终端用户。
- `electron:dist:signed` 缺少 Apple Developer 或证书变量时会失败，这是预期的发布门禁。

## Mitigation

- 每次打包前执行 `pnpm build` 和 Electron 主进程语法检查。
- 每次打包前执行 `pnpm backend:bundle:prepare` 和 `pnpm backend:bundle:verify`，确保随包 Python 与后端依赖可用。
- 生产发布时将桌面端与后端服务版本一起记录，避免 API 合约漂移。
- 打包验证时检查 `release/**/resources/backend/.venv` 是否存在，否则桌面端虽然能启动窗口，但后端会进入 `missing` 状态。
- 检查 `release/**/resources/backend/config/model_catalog.json` 是否存在，缺失时 AI 模型列表为空，无法选择模型。
- 验证产物内无 `__pycache__/` 目录、`.pyc` 文件、测试目录或缓存文件（由 `electron-builder.config.cjs` 的 `filter` 规则自动排除）。
- 对外发布使用 release workflow 开启签名和公证，本地 `electron:pack` 保持无签名验证链路。
- 发布前执行 `node tools/verify_release_env.cjs darwin` 或 `win32`，避免缺失凭据时产出未签名制品。
- `release/` 是构建产物目录，已加入 `.gitignore`，不要纳入源码管理。
