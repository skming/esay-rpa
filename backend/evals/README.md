# AI 助手评测

## 验证层级

| 入口 | 验证范围 | 是否调用模型 |
| --- | --- | --- |
| `pytest` 单元测试 | 工具契约、Guard、阶段准入与执行器边界 | 否 |
| `tests/test_real_*.py` | 真实 DOM、交互与既定流程重放 | 否 |
| `evals.run_evals` | 模型使用 mock 工具时的行为 | 在线运行调用，录像重放不调用 |
| `evals.run_e2e` | 模型在本地真实页面创建、执行并验收流程 | 是，`--self-check` 除外 |

环境与常规检查见 [开发指南](../../docs/TECHNICAL.md#验证)。以下命令在 `backend/` 执行，在线评测读取应用模型配置并消耗 tokens；未配置密钥时可能以退出码 0 跳过，报告必须标为未运行。

## 行为评测

```bash
uv run python -m evals.run_evals
uv run python -m evals.run_evals --only off_topic_refusal,review_request_does_not_run
uv run python -m evals.run_evals --reps 3 --record
uv run python -m evals.run_evals --reps 3 --replay
```

可用 `--model` 指定模型。修改提示词或编排前后，在各自 Git revision 使用相同场景、模型和重复次数比较。

录像按模型和提示词指纹隔离，指纹由 `SYSTEM_PROMPT` 与 `PAGE_DISCOVERY_PROMPT` 共同生成；重放只读取匹配版本，旧录像不能证明新提示词的效果。每个场景开始前清理其流程的修复、检查点和验证状态，不使用业务流程 ID。

场景和断言定义见 [run_evals.py](run_evals.py)。新增场景应满足：

- 验证一个明确行为，使用足以进入目标阶段的 fixture。
- Guard 的 `triggered` 断言验证护栏可达，`not_triggered` 验证模型自行遵守规则。
- 生成流程需检查真实 lint 与节点类型；修复场景必须包含真实缺陷。
- 区分模型、fixture 和断言错误；同时比较轮数、工具调用、重复调用与 token 成本。

提示词、工具或策略改动还需运行 `test_ai_prompts.py`、`test_ai_tools.py`、`test_ai_guards.py` 和 `test_ai_phases.py` 等相关测试。

## 真实页面 E2E

```bash
uv run python -m evals.run_e2e
uv run python -m evals.run_e2e --only pagination_sweep
uv run python -m evals.run_e2e --self-check
```

`run_e2e` 在隔离目录中启动临时本机 HTTP 服务，模型生成和外部重放使用同一页面服务。`--root` 可指定隔离目录，`--model` 可覆盖默认模型。`--self-check` 只运行内置手写案例，不接受 `--only`，不计入模型成功率。

评分分开记录：

| 字段 | 通过条件 |
| --- | --- |
| `replay_passed` | 保存的流程在全部独立输入变体上通过真实重放 |
| `model_execution.passed` | 模型最后一次运行最终流程成功、平台验收通过，实际变量符合首组独立预期 |
| `passed` | 两项均通过，且模型轮次没有错误事件 |

首次运行变量在评测 prompt 中提供；比较历史分数前需确认输入条件相同。最终流程的证据要求见 [AI 与证据约束](../../docs/TECHNICAL.md#ai-与证据约束)。

`pagination_sweep` 使用 3 页数据，`max_pages=20` 与 `max_pages=3` 均应完整采集。执行器边界由 [分页相关测试](../../docs/TECHNICAL.md#验证) 覆盖。

工具证据记录状态和异常，不保存完整参数、DOM 或截图；平台读取和外部重放不计入模型调用数。被取消的尝试保留调用记录，但进程取消不保证产出完整案例报告。
