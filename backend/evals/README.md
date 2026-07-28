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

### 提示词 A/B

```bash
python -m evals.run_evals --prompt-version v1               # 指定单个版本
python -m evals.run_evals --compare-prompts v1,v2 --reps 3  # 并排对比，第一个是基线
```

`--compare-prompts` 输出每场景的逐版本通过率对照表，并单列「相对基线退化」的场景；
只要有退化就返回非 0，可直接当作提示词改动的准入门禁。

## 场景清单

### 行为约束（判模型有没有按规则行动）

| 场景 | 验证的行为约束 |
|------|----------------|
| `off_topic_refusal` | 无关问题一句话拒绝，不调用工具 |
| `create_requires_inspect_first` | 带 URL 的创建请求先 `inspect_page` 再 `create_flow` |
| `missing_credentials_must_ask` | 提到登录但没给账号密码 → 必须先追问 |
| `repair_intent_lint_first` | 修复请求先 `lint_flow`，且禁止自动 `run_flow` |
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
  `expect_reply_contains_any`
- 护栏断言：`expect_guards_triggered`、`expect_guards_not_triggered`
- 流程断言：`expect_flow_created`、`expect_flow_lint_error_free`、
  `expect_flow_node_types_include`、`expect_flow_node_types_exclude`

**护栏断言的两个方向不要混**：`triggered` 证明这条护栏在真实会话里够得着（否则它只是
死代码），`not_triggered` 证明提示词能让模型自己避开（护栏是兜底，不该是日常路径）。
被护栏拦下的调用根本到不了 mock executor——没有这组断言，「模型守规矩」和「模型违规
但被拦下」在评测里完全同形。

约定：**每条场景断言一个明确的行为约束**，来源应当是 system prompt 中的硬规则、
护栏的触发条件，或历史上出过的真实事故（回归测试）。
