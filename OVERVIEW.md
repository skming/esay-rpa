# Easy RPA 说明文档

---

## 一、产品定位

Easy RPA 是一款运行在本地的桌面端 RPA（机器人流程自动化）工具。用户通过可视化画布搭建自动化流程，由内置的 RPA 助手辅助创建和调试。所有数据与执行均在本机完成，不依赖云服务。

**主要用途**：
- 网页数据采集（登录、填表、翻页、表格提取）
- 文件批量处理（读写 Excel/CSV/JSON、复制移动压缩）
- 定时自动化任务（按 Cron 表达式调度）
- 需要人机协同的半自动流程（运行时等待用户输入验证码等）

**不适用场景**：
- 桌面原生 GUI 自动化（只能操作网页，不能操作 QQ、微信、本机 Excel 桌面客户端）
- 需要第三方 Python 包（仅内置 `json`、`os`、`re`、`csv`、`datetime`、`math`、`pathlib`、`urllib`、`hashlib`、`openpyxl`）
- 需要强持久登录态且有强反爬的平台（如银行 U 盾验证、微信公众号后台）
- 实时音视频处理、WebRTC

---

## 二、技术架构

| 层           | 技术                                                             |
| ------------ | ---------------------------------------------------------------- |
| 桌面外壳     | Electron                                                         |
| 前端         | React 18 + TypeScript + Tailwind CSS + @xyflow/react（流程画布） |
| 后端         | Python 3 + FastAPI（本机 HTTP 服务）                             |
| 浏览器自动化 | Playwright（持久化 Browser Profile，Cookie 跨运行保留）          |
| 轻量抓取     | `browser.fetch` 节点走 httpx 直接请求（无浏览器会话）            |
| AI 对话      | LiteLLM 多供应商路由 + Server-Sent Events 流式输出               |
| 数据存储     | SQLite（流程/调度/任务记录）+ 本地文件（产物/日志）              |

后端作为本机进程运行，前端通过 `electronBridge` 调用后端 REST API。

---

## 三、节点能力

流程由节点（Node）和连线（Edge）组成。以下为所有已实现的节点类型。

### 3.1 浏览器操作（21 种）

| 节点                    | 说明                                                                                                                                                                                               |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `browser.open`          | 打开/跳转页面（URL 支持 `${var.xxx}` 变量）                                                                                                                                                        |
| `browser.ensureLogin`   | 登录态优先探测：打开目标页后按"已登录特征"（`selector`）/"未登录特征"（`targetSelector`）判断持久会话是否有效，输出 `logged_in` / `login_required` 供条件分支跳过登录链路；含登录的流程应以此节点开头 |
| `browser.fetch`         | 轻量抓取：直接请求 URL 提取内容，不启动持久浏览器会话                                                                                                                                              |
| `browser.click`         | 点击元素（支持 `continueOnError` 容错）                                                                                                                                                            |
| `browser.hover`         | 鼠标悬停元素，用于触发悬停展开的下拉/二级菜单（如 Element UI NavMenu）                                                                                                                             |
| `browser.fill`          | 填写输入框（支持 `fillMode: 'js'` 以 JS 赋值绕过键盘模拟）                                                                                                                                         |
| `browser.press`         | 模拟按键（Enter / Tab / Escape 等）                                                                                                                                                                |
| `browser.select`        | 下拉框选项选择                                                                                                                                                                                     |
| `browser.check`         | 复选框勾选/取消                                                                                                                                                                                    |
| `browser.drag`          | 拖拽元素到目标位置                                                                                                                                                                                 |
| `browser.extract`       | 提取内容到变量；模式：`text`/`html`/`attribute`/`table`/`similar`/`by_text`（`table` 自动识别表头输出 `list[dict]`；`similar` 以首个元素为种子扩展结构相似兄弟元素；`by_text` 按可见文字定位元素）；可选 `outputSchema` 声明输出字段契约 |
| `browser.wait`          | 等待元素出现（支持 `continueOnError` 超时不中断）                                                                                                                                                  |
| `browser.waitFor`       | 等待条件：轮询元素可见/隐藏/文本包含等状态，扩展执行器下复用 `browser.elementState` 探测                                                                                                           |
| `browser.scroll`        | 页面滚动（`up`/`down`/`left`/`right`）                                                                                                                                                             |
| `browser.screenshot`    | 截取当前页面，结果以 base64 存入变量                                                                                                                                                               |
| `browser.dismiss`       | 关闭浏览器原生弹窗（alert/confirm/prompt）                                                                                                                                                         |
| `browser.paginateNext`  | 点击下一页按钮，提取当前页内容并累计                                                                                                                                                               |
| `browser.clickLoadMore` | 反复点击"加载更多"到底，累计提取所有内容                                                                                                                                                           |
| `browser.tab.open`      | 新开标签页                                                                                                                                                                                         |
| `browser.tab.switch`    | 按序号切换标签页                                                                                                                                                                                   |
| `browser.tab.close`     | 关闭当前标签页                                                                                                                                                                                     |

> 注：另有 8 种 `ui.*` 节点（`ui.click`、`ui.fill`、`ui.extract` 等），底层与 `browser.*` 共用 Playwright 引擎，UI 组件库尚未将其列入节点面板。

**选择器语法**：所有基于选择器的节点统一支持——① CSS（自动穿透 open Shadow DOM，无需特殊写法）；② 语义引擎 `text="文案"` / `role=button[name="文案"]`（按元素含义定位，抗页面改版）；③ iframe 穿透语法 `iframe选择器 >>> 内部选择器`（可多层链式，如 `iframe[name="main"] >>> tbody tr`）。

**Selector 自愈**：click / fill / press / wait / extract / check / hover / select 等基于选择器的节点支持两个可选韧性字段——`fallbackSelectors`（备选选择器列表，换行分隔）和 `anchorText`（元素可见文字锚点）。主 selector 未命中时，运行时会依次探测备选和由锚点文字派生的候选（如 `role=button[name="登录"]`、`button:has-text("登录")`），仍未命中还会自动到页面各 iframe 内探测（自动补全 `>>>` 前缀），命中则重试一次，自愈结果记入节点执行详情。

**Schema 驱动抓取**：提取类节点（extract / clickLoadMore / paginateNext）可声明 `outputSchema`（JSON 数组，如 `[{"name":"品名","aliases":["名称"]},{"name":"价格"}]`）。运行时按表头 精确→别名→包含 匹配把行改名对齐成声明的字段（无表头表格按列序命名，纯文本行单字段包装）；必需字段未命中直接报错并列出实际可用列——输出从"抓到什么算什么"变成可校验的契约。

### 3.2 流程控制（10 种）

| 节点                       | 说明                                                                   |
| -------------------------- | ---------------------------------------------------------------------- |
| `control.foreach`          | 遍历列表变量；两条出边必须分别标记 `body`（循环体）和 `exit`（循环后） |
| `control.repeat_until`     | 重复循环体直到 `condition` 成立（次数由运行时状态决定：翻到目标月份、点到「加载更多」消失）；出边规则同 foreach；跑满 `maxIterations` 仍未满足条件即运行失败 |
| `control.condition`        | 条件分支（if/else），`inputValue` 填布尔表达式                         |
| `control.delay`            | 延时等待（毫秒）                                                       |
| `control.retry`            | 自动重试，可配置次数和间隔                                             |
| `control.try`              | 异常捕获块，`errorVariable` 存放错误信息                               |
| `control.break`            | 跳出循环                                                               |
| `control.noop`             | 空操作（占位 / 分支汇聚点）                                            |
| `control.subprocess`       | 调用另一个已发布的流程                                                 |
| `control.human_takeover`   | 暂停流程等待人工接管；前端显示全局 Banner，操作员选择"继续"或"重试当前节点"后恢复；`timeoutMs` 默认 600000（10 分钟），超时按"任务停止"而非"失败"处理 |

### 3.3 变量与交互（6 种）

| 节点                 | 说明                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------ |
| `variable.set`       | 赋值（支持 `${var.xxx}` 引用其他变量）                                               |
| `variable.get`       | 读取变量存入另一个变量                                                               |
| `variable.input`     | 运行时弹窗等待用户输入（适用于验证码等动态值；在全自动流程中误用会导致任务阻塞超时） |
| `variable.log`       | 输出日志（可设置级别：info/success/running/warn/error）                              |
| `variable.notify`    | 向指定通道发送通知                                                                   |
| `variable.clipboard` | 读写剪贴板                                                                           |

### 3.4 数据处理（7 种）

| 节点                    | 说明                                                       |
| ----------------------- | ---------------------------------------------------------- |
| `data.json.parse`       | 解析 JSON 字符串                                           |
| `data.string.transform` | 字符串操作（trim/upper/lower/replace/split 等）            |
| `data.regex.match`      | 正则匹配                                                   |
| `data.list.map`         | 列表处理（compact/unique/join 等）                         |
| `data.math.compute`     | 数学运算（+/-/×/÷/mod）                                    |
| `data.convert`          | 类型转换（to_int/to_float/to_bool/to_str/to_list/to_json） |
| `data.encrypt`          | 哈希与加解密（md5/sha256/base64/AES）                      |

### 3.5 HTTP 请求（1 种）

| 节点           | 说明                                             |
| -------------- | ------------------------------------------------ |
| `http.request` | HTTP 请求（GET/POST/PUT/DELETE），响应体存入变量 |

### 3.6 脚本（4 种）

| 节点                | 说明                                                                                                                                           |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `script.python`     | 内联 Python 代码或本地文件；运行器自动在脚本顶部注入 `_vars` 加载器，从 `RPA_VARIABLES_FILE` 读取完整变量快照（无大小限制截断），无需手动读取 |
| `script.javascript` | 同上，Node.js 环境；变量通过 `RPA_VARIABLES_JSON` 环境变量传入                                                                                 |
| `script.shell`      | Shell 命令，支持 `${var.xxx}` 插值；每个变量值在插入命令前会自动经过 `shlex.quote()` 转义，防止 Shell 注入——变量内容无法再携带 `&&`、`;`、`\|` 等元字符来拼接子命令；若需执行动态组合命令，改用 `script.python` 构造并调用 `subprocess`；变量同样通过 `RPA_VARIABLES_JSON` / `RPA_VARIABLES_FILE` 可读 |
| `script.websocket`  | 建立 WebSocket 连接，发送并接收一条消息                                                                                                        |

> Python 脚本中可直接用 `_vars['xxx']` 访问所有流程变量，无需额外导入。可用内置包仅限：`json`、`os`、`re`、`csv`、`datetime`、`math`、`pathlib`、`urllib`、`hashlib`、`openpyxl`。

**脚本的能力边界**（`backend/app/services/ai_tools/script_capabilities.py` 单点声明，写进节点说明的同时也是 lint 的放行范围）：

| 能力         | 当前状态                                                                                                                                                          |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 产出格式     | 标准库格式（`.csv/.json/.md/.txt/.html/.xml/.zip`）随便写；第三方库按环境实际探测（`find_spec`），当前 `.xlsx` 可用（openpyxl），`.pdf/.docx/.pptx` 缺库不可用。缺库时不允许自己拼字节流——那样照样跑成 `success`，坏在用户打开文件那一刻，所以 lint 以 `unavailable_artifact_format`（error）在运行前拦下，出路是换格式或让用户装库。往 `pyproject` 加一个库，节点说明与放行范围同时生效，无需改代码 |
| 语义加工     | **没有**会调模型的节点，脚本只能做规则处理（原文摘录、按句截取、词频统计、正则抽取）。声称「总结/摘要/改写/翻译」会被 `claimed_semantic_capability_unavailable`（error）拦下，出路是告诉用户平台只能给规则产物、并把节点与文档里的说法改成实际做的事 |

### 3.7 文件操作（9 种）

`file.read` / `file.write` / `file.copy` / `file.move` / `file.delete` / `file.list` / `file.compress` / `file.rename` / `file.watch`

### 3.8 Excel 操作（6 种）

`excel.read` / `excel.write` / `excel.addrow` / `excel.deleterow` / `excel.save` / `excel.filter`

---

## 四、内置变量

每次运行时系统自动注入以下变量，无需在流程中声明，可直接用 `${var.xxx}` 引用（脚本节点通过 `_vars['xxx']` 访问）：

| 变量名          | 值                                                | 示例                                                           |
| --------------- | ------------------------------------------------- | -------------------------------------------------------------- |
| `run_timestamp` | 运行开始时间                                      | `20260622_143022`                                              |
| `flow_slug`     | 已保存流程为 `flow_id`，临时流程回退到流程名 slug | `2b36c13c-d502-4937-b97a-1e8c513f1c3f`                         |
| `output_dir`    | 本次运行的标准输出目录，按任务隔离                | `runs/2b36c13c-d502-4937-b97a-1e8c513f1c3f/<task_id>/`          |
| `output_prefix` | 带时间戳的路径前缀                                | `runs/2b36c13c-d502-4937-b97a-1e8c513f1c3f/<task_id>/20260622_143022` |

---

## 五、执行模型

- **并发**：内存队列，默认并发数 2（每次 `run_flow` 可配置）。超出并发上限的任务排队等待。
- **调试模式**：支持逐节点步进（step-once）、断点（breakpoint）、恢复运行（resume-until-breakpoint）。
- **用户输入暂停**：`variable.input` 节点执行时流程挂起，直到前端 `POST /api/tasks/{task_id}/input` 提交用户输入后继续。
- **人工接管**：`control.human_takeover` 节点将任务状态置为 `paused_for_human`，前端在所有页面顶部显示全局 Banner。操作员完成人工操作后调用 `POST /api/tasks/{task_id}/resume`（`resume_mode`: `next_node` 或 `current_node`）恢复执行。接管超时（默认 10 分钟）按"任务停止"处理，不计为失败。
- **验证码运行时兜底**：浏览器节点失败时自动探测页面上的滑块/点选验证组件（极验、顶象、数美、腾讯、阿里、字节等特征）。有头模式下命中即自动转入 `paused_for_human` 等待人工完成验证，完成后自动重试该节点（无需流程中预置接管节点）；无头模式下无法人工处理，会在错误信息中注明检测到的验证类型并建议加入 `control.human_takeover` 节点。
- **失败现场取证**：浏览器节点最终失败时自动截取当时页面（JPEG）并连同页面 URL 存为运行产物，供事后排查和 RPA 助手诊断使用。
- **子流程**：`control.subprocess` 节点可调用其他已发布（`active`）状态的流程，支持传参和获取返回值。
- **超时**：`run_flow` API 最长等待 90 秒返回结果；单个节点超时由 `timeoutMs` 字段控制。

---

## 六、定时调度

流程可设置 Cron 表达式（5 段或 6 段）进行定时触发。调度记录持久化到 SQLite，支持：
- 启用/禁用单个调度
- 手动触发（`POST /api/schedules/{id}/trigger`）
- 查看执行历史

调度器在后端进程启动时随 lifespan 自动运行。

---

## 七、RPA 助手

### 支持的模型供应商

| 供应商    | 已配置模型                                                          |
| --------- | -------------------------------------------------------------------- |
| Anthropic | Claude Opus 4.8、Fable 5、Sonnet 5（默认）、Haiku 4.5                |
| OpenAI    | GPT-5.5、GPT-5.4-mini                                                |
| xAI       | Grok 4.5                                                            |
| Google    | Gemini 3.5 Flash                                                     |
| DeepSeek  | V4 Pro、V4 Flash                                                     |
| 阿里云 Qwen | Qwen3.7 Max、Qwen3.6 Flash                                         |
| 智谱 GLM  | GLM-4.6                                                              |

模型目录以 `backend/config/model_catalog.json` 为准，此表仅供概览；新增/调整模型不需要改动本文档。

支持自定义 `base_url` 接入私有中转（relay）端点。密钥存储在本机 `~/.easy-rpa/ai/config.json`；设置页面展示时以 `前4位****后4位` 格式掩码，发送回服务端的掩码值不会覆盖已保存的真实密钥（清空需显式删除）。

### RPA 助手能做什么

RPA 助手（对话面板）通过工具调用（Function Calling）直接操作流程，无需用户手动点击画布。它可以：

- 根据自然语言描述**创建新流程**（`create_flow`）
- **修改现有流程**：增删节点、调整连线、批量修改字段（`update_flow`、`apply_node_fix`）
- **运行流程**并等待结果（`run_flow`，最长等待 90 秒）
- **查看运行结果**：输出变量、产物文件（`get_run_output`）
- **诊断错误**：读取失败节点配置和日志，并附带失败瞬间的页面截图供视觉模型直接查看（`get_run_error`、`get_run_logs`）
- **检查页面 DOM**：读取当前页面的输入框、按钮、链接、表格、可见选项和页面布局（`inspect_page`）
- **识别日期控件并给出交互配方**：`inspect_page` 会返回 `date_controls[].interaction_recipe`——命中 Element UI / Ant Design 时用内置配方；其他组件库（Arco/Vant/iView/自研）按输入框的日期特征推断出 `library:"generic"` 的通用配方，selector 同样取自真实 DOM。两者都以「键入日期文本 + Enter + 回读硬门控」为主路线
- **校验变量引用**完整性（`validate_flow`）
- **审计运行质量**：运行成功后检查输出是否可验证、是否混入 UI 行、是否满足日期/枚举等需求约束（`assert_run_output`）。文档型交付（md/html/pdf…）额外比对本次抓取到的数据是否真的出现在正文里（`document_missing_run_data`）——正文整篇由脚本写出，只比需求关键词等于让模型拿自己写的标题自证，这条不接受自证
- **发布流程**（`publish_flow`，将状态改为 `active`，使其可被调度和子流程调用）

对话面板会实时显示本轮的轮次、token 消耗（含缓存命中比例）、模型耗时与被护栏拦下的次数，
接近轮次上限时变色提示——多轮自愈是最贵的路径，值不值得再试一次由用户判断。

RPA 助手**无法做到**：

- 直接“看懂”截图中的视觉细节；非视觉模型应优先使用 `inspect_page` 读取 DOM，而不是依赖截图节点
- 保证首次生成的选择器（CSS selector）一定匹配目标页面；选择器基于 DOM 检查和运行反馈持续修复
- 记住跨会话的对话内容本身（新对话从空上下文开始；跨会话延续的只有下方「站点经验档案」「修复台账」「会话检查点」这三份**由真实运行结果沉淀**的结构化记录）
- 操作需要强验证或反爬保护的网站
- **做语义加工**：平台没有会调模型的节点，所以「总结/摘要/润色/翻译」只能得到规则产物（原文摘录、按句截取、词频统计）。助手会先说明这件事，由用户决定接受还是改需求；把规则产物命名成「总结」会被 lint 在运行前拦下
- 生成当前环境缺库的产物格式（见 3.6「脚本的能力边界」）：会停下来问用户装库还是换格式，不会自己拼字节流糊过去

### RPA 助手质量闸门

闸门的判据一律挂在**节点实际做的事**上，不挂节点类型白名单：同一个缺陷换一种脚本语言写、
换一个等价节点做，必须一样拦得住——否则闸门的宽严取决于模型的写法偏好，而抹平模型差异正是它的目的。

RPA 助手的关键安全规则不只依赖 system prompt，而是在编排层和工具层硬执行。这些护栏登记在
一张策略表里（`backend/app/services/ai_guards.py`），提示词中的「系统硬约束」段落由该表自动
生成，因此不存在「规则改了但提示词还写着旧的」这种两份维护。被拦截时返回里带 `guard_id`，
指明踩到的是哪一条：

| 闸门              | 行为                                                                                                                                                                      |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Selector 失败熔断 | `get_run_error` 返回 `inspect_hint` 后，编排层会阻止继续 `run_flow` 或盲目修节点，直到调用 `inspect_page` 获取真实 DOM                                                    |
| 质量审计熔断      | `assert_run_output.passed=false` 后，未按 `repair_plan` 修复前会阻止再次 `run_flow`                                                                                       |
| 阻断级 lint       | `critical_action_continue_on_error`、`script_uses_browser_dom`、`table_extract_selector_targets_container`、`date_filter_missing_verification`、`submit_key_on_body`、`client_side_filter_masks_page_filter`、`login_without_navigation_to_data_page`、`probe_extract_without_continue_on_error`、`unavailable_artifact_format`、`claimed_semantic_capability_unavailable` 等发现会阻止不可信运行 |
| 修复不顺手重跑    | 用户只说「修一下 / 报错了」时，助手改完必须交回用户，不会自己 `run_flow`——运行会真的打开浏览器操作目标站点，这个决定归用户。用户下一句表示要跑时限制自动解除；本轮自己跑出来的错另算，那次运行是用户点的 |
| 重复修复去重      | 同一对话中重复提交完全相同的节点 patch 会被拒绝，防止弱模型反复无效尝试                                                                                                   |
| 节点级修复熔断    | 同一节点的 selector 累计修改 2 次后，第 3 次修改会被阻断，必须先获取新页面证据（`inspect_page` / `inspect_screenshot` / `get_run_error` 失败截图）；疑似验证码时会被引导改插 `control.human_takeover` 而非继续改 selector |
| 跨会话修复台账    | 上述计数落盘在 `~/.easy-rpa/ai/repairs/flow_<id>.json`，新会话读回后继续累计；开场即把「这些节点改过几次、试过哪些 selector」告知模型，避免用户回一句「还是不行」就重试同一批失败方案。`assert_run_output` 通过即清账 |
| 会话检查点        | 失败预算与未了结的义务（必须先 `inspect_page`、lint 未修完、审计未通过）落盘在 `~/.easy-rpa/ai/checkpoints/flow_<id>.json`。用户点停止、断流、关窗后重开，这些额度不会归零；拿到最终回复即清账，新任务不背旧熔断。2 小时未更新自动作废 |
| 站点经验档案      | 按域名沉淀在 `~/.easy-rpa/ai/site_knowledge.json`，对话提到同域名时注入。**双向记录**：跑通过的 selector / UI 框架 / 登录探针供直接复用；真实运行中未命中的 selector 列为「已证伪」，避免换个流程抓同一站点时重踩同一个坑。同一 selector 后来跑通即撤销，失败记录 14 天过期 |

`run_flow` 返回 `success` 只代表节点没有抛出运行异常，不代表业务结果可信。抓取、筛选、导出类流程必须继续执行 `get_run_output` 和 `assert_run_output`。

### 已知行为限制

| 问题           | 说明                                                                                                                   |
| -------------- | ---------------------------------------------------------------------------------------------------------------------- |
| 选择器盲猜     | 目标网站 DOM 结构未知时，AI 可能按框架惯例推测选择器；失败后会被要求先 `inspect_page` 再修复                           |
| 复杂筛选 UI    | 日期选择器、多选下拉等复杂过滤 UI 需要用真实 DOM 和运行输出校验，不能只看节点运行成功                                  |
| 输出污染       | 抽取范围过宽时可能混入日历、分页、按钮等非业务 UI 行；`assert_run_output` 会返回 `mixed_ui_rows` 并要求收窄范围        |
| 弱模型质量差异 | 能力较弱的模型仍可能效率低、修复路径绕远；硬熔断会减少盲跑，但生产建议使用 Claude Sonnet 5/Opus 4.8、GPT-5.5 等强模型 |

---

## 八、数据存储

所有数据存储在本机 `~/.easy-rpa/`（可通过环境变量 `RPA_APP_DATA_DIR` 修改）：

```
~/.easy-rpa/
  db/
    rpa.sqlite3            ← 流程/调度/任务记录（SQLite）
  ai/
    config.json            ← AI 供应商密钥和默认模型
    chats/                 ← AI 对话会话
    repairs/               ← 按流程的修复台账（跑通即清）
    checkpoints/           ← 按流程的会话检查点（收尾即清，2 小时作废）
    site_knowledge.json    ← 按域名的站点经验（已验证 / 已证伪的 selector）
  runtime/
    browser/
      profile/             ← Playwright 浏览器 Profile（Cookie 跨运行持久化）
      cookies.json         ← 最近一次浏览器任务导出的 Cookie
    scrapling/
      scrapling_storage.db ← Scrapling 自适应指纹存储（adaptive=True 时持久化）
  workspace/
    runs/<flow_slug>/<task_id>/ ← 运行产物（每个流程保留最近 10 次）
  cache/
    scripts/               ← 内联脚本临时文件（7 天 / 超 300 个自动清理）
  logs/
    backend.log            ← 当天实时日志（每日零点切换新文件）
    backend-YYYY-MM-DD.log ← 按日滚动归档（默认保留 30 天，可通过 RPA_LOG_BACKUP_COUNT 调整）
```

**产物保留策略**：每个流程最多保留最近 10 次运行的输出文件；超出时自动删除最旧的一批。

---

## 九、流程状态

| 状态       | 说明                                                               |
| ---------- | ------------------------------------------------------------------ |
| `draft`    | 草稿，可编辑，不可被调度器触发或子流程调用                         |
| `active`   | 已发布，可被定时调度和子流程调用                                   |
| `paused`   | 已暂停，保留调度绑定但当前周期不触发；可在流程列表随时恢复为 active |
| `disabled` | 已禁用，保留配置但不触发；需手动重新发布才能恢复                   |
| `archived` | 已归档，退出活跃列表                                               |

---

## 十、系统要求

- **操作系统**：macOS / Windows（Electron 桌面应用）
- **网络**：本机执行，AI 对话需访问对应供应商 API
- **Python**：后端需 Python 3.10+（含 Playwright 浏览器二进制）
- **浏览器**：Playwright 自管理的 Chromium（无需单独安装）
