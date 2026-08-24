# RPA 助手行为评测集

改 system prompt、换模型、调整编排护栏之前后各跑一遍，对比行为是否回归。
工具层全部 mock（不启动浏览器、不真正运行流程），只消耗 LLM tokens。

## 运行

```bash
cd backend
python -m evals.run_evals                     # 使用设置页配置的默认模型
python -m evals.run_evals --model gpt-5.5     # 指定模型
python -m evals.run_evals --only off_topic_refusal,review_request_does_not_run
python -m evals.run_evals --reps 3            # 每场景重复 3 次，按通过率判定
```

未配置 API Key 时自动跳过（exit 0），可安全挂进 CI。

### 录像与重放

```bash
python -m evals.run_evals --reps 3 --record   # 存进 evals/recordings/<模型>/prompt-<指纹>/
python -m evals.run_evals --reps 3 --replay   # 只重放录像判分，不调模型、不花 token
```

录像按**模型 + 提示词版本**分目录：同一场景在不同提示词下是不同的样本，混在一起重放
会拿 A 的录像给 B 判分。改判分逻辑后想验证新断言，用 `--replay` 免费重跑历史输出。

指纹是 `SYSTEM_PROMPT` 与 `PAGE_DISCOVERY_PROMPT` 拼起来的 SHA-256 前 12 位（两段都算，
因为首轮探测阶段用的是后者，只算前者会让探测规则的改动共用旧录像）。指纹变了就是新目录，
旧目录随即再也读不到——它不是历史存档，是判分对不上的样本，该删。

### 每次重跑都从「第一次见到这个流程」开始

修复台账、会话检查点、验证证据按 `flow_id` 落在真实 `~/.easy-rpa/ai/` 下，而所有场景共用
`eval-flow-0001`。不清的话第 2 次重跑读到的是第 1 次的失败记录：台账摘要会作为 system 消息
注入，selector 修复计数还会触发 `lint_diff` 的预算护栏——每个场景的输入都被上一轮污染，
通过率既不可比也不可复现。`run_scenario` 开头的 `_reset_session_state()` 负责清这三份。

不整体隔离 `RPA_APP_DATA_DIR`（测试套件那样做）：API Key 与中转地址就在那个目录里，
隔离掉评测就没法调模型了。

### 提示词变更对比

```bash
python -m evals.run_evals --reps 3 --record  # 在当前 Git revision 生成报告和录像
```

生产代码只保留唯一提示词，不提供环境变量切换和旧版本回退。需要对比提示词改动时，分别在基线
Git revision 与候选 revision 运行同一命令，再比较逐场景通过率；不要把历史提示词复制回业务代码。

提示词或 Tool Schema 改动还必须运行 `tests/test_ai_prompts.py`：它会检查公开工具名与执行器分发是否一致、
`run_flow` 的参数是否只有 `flow_id`/`variables`/`browser_executor`（验收不接受模型提供判据），
以及默认提示词是否包含凭据隔离策略。凭据脱敏与硬阻断分别由
`tests/test_ai_tools.py`、`tests/test_ai_guards.py` 覆盖，阶段准入与收敛预算的每条触发路径由
`tests/test_ai_phases.py` 覆盖——护栏「够不够得着」在那里证，评测里的 `expect_guards_not_triggered`
只证「模型有没有自己避开」。未配置 API Key 时在线行为评测会跳过，不能把跳过
当成候选提示词没有退化；此时只能确认静态契约与单元测试通过。

## 场景清单

### 行为约束（判模型有没有按规则行动）

| 场景 | 验证的行为约束 |
|------|----------------|
| `off_topic_refusal` | 无关问题一句话拒绝，不调用工具 |
| `create_requires_inspect_first` | 带 URL 的创建请求先 `inspect_page` 再 `create_flow` |
| `missing_credentials_use_secure_inputs` | 登录流程只声明空凭据变量，并引导用户在输入变量面板配置秘密 |
| `page_access_denied_stops_tool_loop` | 页面返回 403 后由服务端立即收尾，不进入第二轮 LLM，也不查询节点目录或空流程 |
| `continue_creation_recovers_task_state` | “继续创建”从历史工具证据恢复目标和阶段，重新检查页面而不是复述旧错误 |
| `repair_inspects_before_touching_selectors` | 改 selector 之前必须已取过真实 DOM，不能照着状态块盲改 |
| `repair_spends_no_round_on_reading_state` | 状态块已给出定义与诊断，不该再花调用去「确认一遍」（剩下唯一能空转的是节点目录） |
| `explicit_acceptance_gets_run_evidence` | 「改完跑一遍验收」同句出现时必须真跑，不能只交静态检查结论 |
| `review_request_does_not_run` | 审查类请求模型自己就不该跑流程，而不是撞到 `run_not_authorized` 上 |
| `timeout_waiting_input_no_rerun` | 流程等待用户输入时禁止重复 `run_flow` |

### 护栏路径（判护栏的触发条件到底通不通）

| 场景 | 验证的护栏 |
|------|------------|
| `guard_quality_fail_repairs_before_rerun` | `run_flow` 带回的 `acceptance_audit` 不通过后必须先按 `repair_plan` 改，不能原样重跑 |
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

### 全套不变量：不挂在任何场景上

`_check_fabricated_write` 对每一局都判一次：回复宣称流程已落盘，但一次写入工具都没成功调过，
即为假交付。它不挂场景，因为跟场景想测什么无关——而现有判据一条都拦不住：`expect_tool_order`
对没发生的调用恒真，`expect_reply_contains_any` 还会因为「已创建流程」这类措辞判过。
实测在 `guard_blocking_lint_fixed_before_run` 的录像里出现过整局零调用的假绿灯。

短语表与编排层的撤回判据同一份（`ai_orchestrator._FLOW_SAVED_CLAIM_PHRASES`）：各写一份的话，
编排层补了新说法而评测测不到，等于放掉一条已经修好的缺陷的回归。

### 行为指标：断言之外的那半

断言只回答「这一次对不对」，答不了「大量重复审查创建修复」——那是一个分布。一个场景可以
每条断言都通过，却用了 18 轮、把同一个工具调了 5 次。`evals/metrics.py` 按轮数、工具调用数、
重复调用数（与护栏共用 `call_fingerprint`，两边算法不同就会调到错的地方去）、token 与护栏
触发分布记账，跑完打一张表。这里只记账不判定；阈值等实测基线出来再定。

### 断言 run_flow 之前，先确认 fixture 推得到 VERIFY

`run_flow` 只在 VERIFY 阶段才拿得到，而阶段由 fixture 决定的事实（有没有节点、有没有阻断诊断）
推出来。fixture 推不到 VERIFY 时断言 `run_flow` 等于断言一件不可能的事，而失败信息指向模型，
人会去改提示词。`tests/test_evals_harness.py::test_scenarios_expecting_run_flow_can_reach_verify`
把这条钉住：fixture 要么现在放行，要么落一次 `apply_node_fix` 后放行。实际踩过——fixture 流程
没带 `acceptance_contract`，状态块判出 error 级 `acceptance_contract_incomplete`，四个场景整局
钉在 FIX 阶段。它同时守住 fixture 的动态性：写死返回值的 fixture 在第二次判定里仍然停在 FIX。

### 场景红了，先怀疑判据

`repair_inspects_before_touching_selectors` 的前身长期 0/3，两处都是判据自己写错的：

- `expect_first_tool` 钉死第一个工具，把「先读一眼再动手」判成违规——那恰恰是对的行为，
  这条判据实际在要求模型盲改。真正的不变量是**任何写工具之前必须已经拿到证据**，
  所以有了 `expect_before_writes`（写工具集合复用 `ai_guards.FLOW_WRITE_TOOLS`，新增写工具自动纳入）。
- fixture 用的是默认返回的**空流程**，修复场景里无处可修，模型只能反问，被断言的那条路径
  根本走不到。样本流程要真带缺陷，`findings` 用真 `_lint_flow` 现算而不是手写快照——
  手写的那份会和 lint 规则各自演化，最后测的是一份过期快照。

判据写错和模型做错在结果上同形（都是红的），区别只在**失败原因读起来是否荒谬**。
调用序列里模型的动作明明合理却被判失败时，先改判据。

「读一眼」这件事后来连工具都不需要了：`get_flow` / `lint_flow` / `validate_flow` 已从 schema
撤下，状态块每轮重算一份塞在消息尾部（见 `ai_flow_state.py`）。所以现在的判据换成了
`repair_spends_no_round_on_reading_state`——不是禁止复检，是复检已经无处可调。

### 模型踩护栏，先看它手上有没有躲开的字段

`guard_blocking_lint_fixed_before_run` 长期 0/3，模型每次都在修复前先 `run_flow` 撞一次
`blocking_diagnostics_must_be_fixed`。这不是提示词说得不够重：阻断名单只存在于编排层，`create_flow` 交回给模型的
finding 只有 `severity: warn`，它据此判断可以先跑一次看看，完全合理。

`expect_guards_not_triggered` 断言的是「提示词能让模型自己避开」，而模型**根本没有可依据的字段**
时，这条断言测的是猜谜。出路是把判断依据放进工具返回值（`annotate_lint_findings()` 给每条 finding
标 `blocks_run`），不是加提示词——加完 3/3。fixture 也改成调真函数现算，手写的那份会把这个信号写死。
