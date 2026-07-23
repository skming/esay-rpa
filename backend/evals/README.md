# RPA 助手行为评测集

改 system prompt、换模型、调整编排守卫之前后各跑一遍，对比行为是否回归。
工具层全部 mock（不启动浏览器、不真正运行流程），只消耗 LLM tokens。

## 运行

```bash
cd backend
python -m evals.run_evals                     # 使用设置页配置的默认模型
python -m evals.run_evals --model gpt-5.5     # 指定模型
python -m evals.run_evals --only off_topic_refusal,repair_intent_lint_first
```

未配置 API Key 时自动跳过（exit 0），可安全挂进 CI。

## 场景清单

| 场景 | 验证的行为约束 |
|------|----------------|
| `off_topic_refusal` | 无关问题一句话拒绝，不调用工具 |
| `create_requires_inspect_first` | 带 URL 的创建请求先 `inspect_page` 再 `create_flow` |
| `missing_credentials_must_ask` | 提到登录但没给账号密码 → 必须先追问 |
| `repair_intent_lint_first` | 修复请求先 `lint_flow`，且禁止自动 `run_flow` |
| `timeout_waiting_input_no_rerun` | 流程等待用户输入时禁止重复 `run_flow` |

## 添加场景

在 `run_evals.py` 的 `SCENARIOS` 列表中追加 `Scenario`：

- `user_message` / `flow_id`：输入
- `tool_overrides`：按工具名覆盖 mock 返回值（值可为 dict 或 `fn(args, calls) -> dict`）
- 断言字段：`expect_no_tools`、`expect_first_tool`、`expect_tools_called`、
  `expect_tools_not_called`、`expect_tool_order`、`expect_tool_max_calls`、
  `expect_reply_contains_any`

约定：**每条场景断言一个明确的行为约束**，来源应当是 system prompt 中的硬规则
或历史上出过的真实事故（回归测试）。
