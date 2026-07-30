# RPA 助手行为评测集

改 system prompt、换模型、调整编排护栏之前后各跑一遍，对比行为是否回归。
工具层全部 mock（不启动浏览器、不真正运行流程），只消耗 LLM tokens。

## 运行

```bash
cd backend
python -m evals.run_evals                     # 使用设置页配置的默认模型
python -m evals.run_evals --model gpt-5.5     # 指定模型
python -m evals.run_evals --only off_topic_refusal,repair_intent_lint_first
python -m evals.run_evals --reps 3            # 每场景重复 3 次，按通过率判定
```

未配置 API Key 时自动跳过（exit 0），可安全挂进 CI。

### 录像与重放

```bash
python -m evals.run_evals --reps 3 --record   # 存进 evals/recordings/<模型>/<提示词版本>/
python -m evals.run_evals --reps 3 --replay   # 只重放录像判分，不调模型、不花 token
```

录像按**模型 + 提示词版本**分目录：同一场景在不同提示词下是不同的样本，混在一起重放
会拿 A 的录像给 B 判分。改判分逻辑后想验证新断言，用 `--replay` 免费重跑历史输出。

### 提示词变更对比

```bash
python -m evals.run_evals --reps 3 --record  # 在当前 Git revision 生成报告和录像
```

生产代码只保留唯一提示词，不提供环境变量切换和旧版本回退。需要对比提示词改动时，分别在基线
Git revision 与候选 revision 运行同一命令，再比较逐场景通过率；不要把历史提示词复制回业务代码。
录像目录使用 `SYSTEM_PROMPT` 的 SHA-256 内容指纹，提示词变化后自动写入新目录，避免误重放旧样本。

提示词或 Tool Schema 改动还必须运行 `tests/test_ai_prompts.py`：它会检查公开工具名与执行器分发是否一致、
`assert_run_output` 是否只暴露 `task_id`，以及默认提示词是否包含凭据隔离策略。凭据脱敏与硬阻断分别由
`tests/test_ai_tools.py`、`tests/test_ai_guards.py` 覆盖。未配置 API Key 时在线行为评测会跳过，不能把跳过
当成候选提示词没有退化；此时只能确认静态契约与单元测试通过。

## 场景清单

### 行为约束（判模型有没有按规则行动）

| 场景 | 验证的行为约束 |
|------|----------------|
| `off_topic_refusal` | 无关问题一句话拒绝，不调用工具 |
| `create_requires_inspect_first` | 带 URL 的创建请求先 `inspect_page` 再 `create_flow` |
| `missing_credentials_use_secure_inputs` | 登录流程只声明空凭据变量，并引导用户在输入变量面板配置秘密 |
| `repair_intent_lint_first` | 修复请求在动手前先 `lint_flow`；不自动 `run_flow` 由 `repair_autorun_lock` 兜 |
| `review_request_does_not_run` | 审查类请求不自动运行流程（这条只有提示词管，护栏不拦） |
| `timeout_waiting_input_no_rerun` | 流程等待用户输入时禁止重复 `run_flow` |

### 护栏路径（判护栏的触发条件到底通不通）

| 场景 | 验证的护栏 |
|------|------------|
| `guard_quality_fail_repairs_before_rerun` | `assert_run_output` 不通过后必须先按 `repair_plan` 改，不能原样重跑 |
| `guard_selector_timeout_inspects_first` | 报错带 `inspect_hint` 时必须先 `inspect_page` 取真实 DOM |
| `guard_blocking_lint_fixed_before_run` | `create_flow` 带回阻断级 lint finding 时必须先修再跑 |

### 生成质量（判建出来的流程本身对不对）

| 场景 | 验证的选型 |
|------|------------|
| `gen_table_to_json` | 抓表格落到 `browser.extract`，不整包塞进脚本节点 |
| `gen_table_to_excel` | 导出走 `excel.*` 节点链，而不是一个 openpyxl 脚本 |
| `gen_login_then_navigate` | 登录分支合流后必须再导航一次（登录成功 ≠ 已在数据页）|
| `gen_paginated_scrape` | 分页用 `browser.paginateNext`，不自己搭循环点下一页 |
| `gen_api_to_file` | 取数走 `http.request` 原生节点，且必须真的建出流程 |

## 添加场景

在 `run_evals.py` 的 `SCENARIOS` 列表中追加 `Scenario`：

- `user_message` / `flow_id`：输入
- `tool_overrides`：按工具名覆盖 mock 返回值（值可为 dict 或 `fn(args, calls) -> dict`）
- `stop_after_tool`：拿到该工具的产物就收，不必等模型把整轮走完
- `min_pass_rate`：配合 `--reps` 给软偏好留噪音带；硬不变量不要设
- 行为断言：`expect_no_tools`、`expect_first_tool`、`expect_tools_called`、
  `expect_tools_not_called`、`expect_tool_order`、`expect_tool_max_calls`、
  `expect_reply_contains_any`、`expect_before_writes`
- 护栏断言：`expect_guards_triggered`、`expect_guards_not_triggered`
- 流程断言：`expect_flow_created`、`expect_flow_lint_error_free`、
  `expect_flow_node_types_include`、`expect_flow_node_types_exclude`

**护栏断言的两个方向不要混**：`triggered` 证明这条护栏在真实会话里够得着（否则它只是
死代码），`not_triggered` 证明提示词能让模型自己避开（护栏是兜底，不该是日常路径）。
被护栏拦下的调用根本到不了 mock executor——没有这组断言，「模型守规矩」和「模型违规
但被拦下」在评测里完全同形。

约定：**每条场景断言一个明确的行为约束**，来源应当是 system prompt 中的硬规则、
护栏的触发条件，或历史上出过的真实事故（回归测试）。

### 场景红了，先怀疑判据

`repair_intent_lint_first` 长期 0/3，两处都是判据自己写错的：

- `expect_first_tool="lint_flow"` 把「先 `get_flow` 读一眼再诊断」判成违规——那恰恰是对的行为，
  这条判据实际在要求模型盲改。真正的不变量是**任何写工具之前必须已经诊断过**，
  所以有了 `expect_before_writes`（写工具集合复用 `ai_guards.FLOW_WRITE_TOOLS`，新增写工具自动纳入）。
- fixture 用的是默认 `get_flow` 返回的**空流程**，修复场景里无处可修，模型只能反问，
  被断言的那条路径根本走不到。样本流程要真带缺陷，`findings` 用真 `_lint_flow` 现算而不是手写快照——
  手写的那份会和 lint 规则各自演化，最后测的是一份过期快照。

判据写错和模型做错在结果上同形（都是红的），区别只在**失败原因读起来是否荒谬**。
调用序列里模型的动作明明合理却被判失败时，先改判据。

### 模型踩护栏，先看它手上有没有躲开的字段

`guard_blocking_lint_fixed_before_run` 长期 0/3，模型每次都在修复前先 `run_flow` 撞一次
`requires_lint_fix`。这不是提示词说得不够重：阻断名单只存在于编排层，`create_flow` 交回给模型的
finding 只有 `severity: warn`，它据此判断可以先跑一次看看，完全合理。

`expect_guards_not_triggered` 断言的是「提示词能让模型自己避开」，而模型**根本没有可依据的字段**
时，这条断言测的是猜谜。出路是把判断依据放进工具返回值（`annotate_lint_findings()` 给每条 finding
标 `blocks_run`），不是加提示词——加完 3/3。fixture 也改成调真函数现算，手写的那份会把这个信号写死。
