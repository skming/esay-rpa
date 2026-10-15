# 本地测试页面

| 入口 | 用途 |
| --- | --- |
| `tests/test_real_page_probe.py` | DOM 探测与真实交互 |
| `tests/test_real_page_flow_replay.py` | 既定流程重放与输出校验 |
| `python -m evals.run_e2e` | 模型创建、执行与验收流程 |

`test_real_executor_parity.py` 在测试内另行构造页面。环境与命令见 [开发验证](../../../docs/TECHNICAL.md#验证) 和 [AI 评测](../../evals/README.md)。

`holdout/` 是留出集：调配方或组件技能时不得查看。已用于调优的页面不能再作为跨页面泛化证据。
