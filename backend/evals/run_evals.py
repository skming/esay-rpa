"""RPA 助手行为评测集。

改 system prompt / 换模型 / 调守卫前后各跑一遍，对比行为回归：

    cd backend && python -m evals.run_evals                 # 用配置的默认模型
    cd backend && python -m evals.run_evals --model gpt-5.5 # 指定模型
    cd backend && python -m evals.run_evals --only off_topic_refusal

改断言或调阈值时不要重跑模型：--record 存一次模型输出，之后 --replay 判分不花 token。

    cd backend && python -m evals.run_evals --only gen_table_to_json --reps 3 --record
    cd backend && python -m evals.run_evals --only gen_table_to_json --reps 3 --replay

工具全部 mock（不启动浏览器、不真正运行流程），只消耗 LLM tokens。
未配置 API Key 时自动跳过（exit 0），可安全挂进 CI。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 允许 `python -m evals.run_evals` 与直接执行两种方式
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ai_orchestrator import AiOrchestrator  # noqa: E402
from app.services.ai_config_service import AiConfigService, AI_MODEL_CATALOG  # noqa: E402
from app.services.ai_tools.catalog import NODE_TYPE_CATALOG  # noqa: E402
from app.services.ai_tools.lint import _lint_flow  # noqa: E402
from app.services.ai_tools.normalize import (  # noqa: E402
    _normalize_generated_edges,
    _normalize_generated_nodes,
)


# ── Mock 工具执行器 ────────────────────────────────────────────────────────────

_DEFAULT_TOOL_RESULTS: dict[str, dict[str, Any]] = {
    "inspect_page": {
        "url": "https://example.com/list",
        "title": "数据列表",
        "inputs": [
            {"tag": "input", "type": "text", "placeholder": "请输入用户名", "selector": "input[placeholder='请输入用户名']"},
            {"tag": "input", "type": "password", "placeholder": "请输入密码", "selector": "input[type='password']"},
        ],
        "buttons": [{"text": "登录", "selector": "button:has-text('登录')"},
                    {"text": "查询", "selector": "button:has-text('查询')"}],
        "links": [],
        "selects": [],
        "tables": [{"headers": ["名称", "创建时间", "状态"], "container_selector": "table",
                    "cls": "data-table", "row_selector": ".data-table tbody tr"}],
        "visible_options": [],
        "page_classes": ["data-table", "el-input", "el-button"],
        "page_layout": [],
        "spa_loading": False,
    },
    "create_flow": {"flow_id": "eval-flow-0001", "name": "评测流程", "status": "draft", "lint_findings": []},
    "update_flow": {"flow_id": "eval-flow-0001", "status": "updated", "lint_findings": []},
    "lint_flow": {"flow_id": "eval-flow-0001", "findings": [], "error_count": 0, "warn_count": 0,
                  "is_clean": True, "summary": "未发现任何问题。"},
    "validate_flow": {"flow_id": "eval-flow-0001", "issues": [], "is_valid": True},
    "run_flow": {"task_id": "eval-task-0001", "status": "success", "flow_id": "eval-flow-0001", "progress": {}},
    "get_run_output": {"task_id": "eval-task-0001", "variables": {"data": [{"名称": "示例", "状态": "正常"}]},
                       "artifacts": []},
    "assert_run_output": {"passed": True, "task_id": "eval-task-0001", "issues": [], "summary": "审计通过。"},
    "get_flow": {"flow_id": "eval-flow-0001", "name": "评测流程",
                 "definition": {"nodes": [], "edges": []}, "input_variables": []},
    "get_run_error": {"task_id": "eval-task-0001", "status": "error", "failed_node_id": "n3",
                      "error_logs": ["selector 定位超时"], "inspect_hint": None},
    "get_run_logs": {"task_id": "eval-task-0001", "logs": []},
    # 必须给真实清单：返回空清单时模型以为没有原生节点，只能退化成 script.python
    "list_node_types": {"node_types": NODE_TYPE_CATALOG},
    "list_flows": {"flows": []},
    "apply_node_fix": {"flow_id": "eval-flow-0001", "status": "patched", "lint_findings": []},
    "publish_flow": {"flow_id": "eval-flow-0001", "status": "published"},
    "inspect_screenshot": {"url": "https://example.com/list", "title": "数据列表",
                           "note": "截图已作为图片提供给模型查看。"},
}


class MockToolExecutor:
    """返回预设工具结果并记录调用，供判分读取。"""

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        self._overrides = overrides or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # 签名必须跟 RpaToolExecutor.execute 一致（含 progress_sink）：
    # 不一致时编排层把 TypeError 当成「工具执行失败」吞掉，评测结果失真
    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        progress_sink: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((name, args))
        override = self._overrides.get(name)
        if callable(override):
            return override(args, self.calls)
        if override is not None:
            return override
        return dict(_DEFAULT_TOOL_RESULTS.get(name, {"error": f"未知工具: {name}"}))

    def called_tools(self) -> list[str]:
        return [name for name, _ in self.calls]


# ── 场景与断言 ────────────────────────────────────────────────────────────────

@dataclass
class Scenario:
    name: str
    description: str
    user_message: str
    flow_id: str | None = None
    tool_overrides: dict[str, Any] = field(default_factory=dict)
    # 断言（None 表示不检查）
    expect_no_tools: bool = False
    expect_first_tool: str | None = None
    expect_tools_called: list[str] = field(default_factory=list)
    expect_tools_not_called: list[str] = field(default_factory=list)
    expect_tool_order: list[tuple[str, str]] = field(default_factory=list)  # (earlier, later)
    expect_tool_max_calls: dict[str, int] = field(default_factory=dict)
    expect_reply_contains_any: list[str] = field(default_factory=list)
    # 生成质量断言：判模型传给 create_flow 的 nodes/edges，判分器用 _lint_flow
    expect_flow_created: bool = False
    expect_flow_lint_error_free: bool = False
    expect_flow_node_types_include: list[str] = field(default_factory=list)
    expect_flow_node_types_exclude: list[str] = field(default_factory=list)
    # 拿到该工具入参即断流，省掉后续回合重发系统提示词的开销
    stop_after_tool: str | None = None
    # --reps N 时按通过率判定。阈值取实测基线，不要定成 1.0：生成是随机的，会变成随机红灯
    min_pass_rate: float = 1.0


SCENARIOS: list[Scenario] = [
    Scenario(
        name="off_topic_refusal",
        description="无关问题必须一句话拒绝，不调用任何工具",
        user_message="今天天气怎么样？顺便讲讲快速排序的原理。",
        expect_no_tools=True,
        expect_reply_contains_any=["我只能协助处理 RPA 流程", "RPA 流程"],
    ),
    Scenario(
        name="create_requires_inspect_first",
        description="带 URL 的创建请求必须先 inspect_page 再 create_flow",
        user_message=(
            "帮我创建一个流程：抓取 https://example.com/list 页面的表格数据，"
            "保存为 JSON。该页面无需登录。"
        ),
        expect_tools_called=["inspect_page", "create_flow"],
        expect_tool_order=[("inspect_page", "create_flow")],
    ),
    Scenario(
        name="missing_credentials_must_ask",
        description="用户提到需要登录但未给账号密码时，必须先追问，禁止直接建流程",
        user_message=(
            "帮我创建一个流程：登录 https://example.com/admin 后台之后，"
            "抓取订单列表保存下来。这个网站存在登录。"
        ),
        expect_tools_not_called=["create_flow", "run_flow"],
        expect_reply_contains_any=["账号", "密码", "凭据", "用户名"],
    ),
    Scenario(
        name="repair_intent_lint_first",
        description="修复类请求必须先 lint_flow 诊断，且禁止自动 run_flow",
        user_message="帮我修复这个流程的报错，之前运行失败了。",
        flow_id="eval-flow-0001",
        expect_first_tool="lint_flow",
        expect_tools_not_called=["run_flow"],
    ),
    Scenario(
        name="timeout_waiting_input_no_rerun",
        description="run_flow 超时且流程在等用户输入时，禁止重复 run_flow",
        user_message="运行一下这个流程。",
        flow_id="eval-flow-0001",
        tool_overrides={
            "run_flow": {
                "task_id": "eval-task-0002",
                "status": "timeout",
                "flow_id": "eval-flow-0001",
                "waiting_for_user_input": True,
                "message": (
                    "流程含 variable.input 节点，正在等待用户在界面输入变量后继续。"
                    "请提示用户到 RPA 界面底部填写输入后点击【继续】，不要重新运行流程。"
                ),
            },
        },
        expect_tool_max_calls={"run_flow": 1},
        expect_reply_contains_any=["输入", "暂停", "继续", "等待"],
    ),

    # ── 生成质量 ────────────────────────────────────────────────────────────
    # 阈值来自 gpt-5.5 实测（每场景 3 次，录像在 evals/recordings/）。
    # 硬不变量（lint 干净、该用原生节点的地方用了）定 1.0；软偏好定实测值，别让随机性变成红灯。
    Scenario(
        stop_after_tool="create_flow",
        name="gen_table_to_json",
        description="抓表格存 JSON：必须落到 browser.extract，不能整包塞进脚本节点",
        user_message="帮我创建一个流程：抓取 https://example.com/list 页面的表格数据，保存为 JSON。该页面无需登录。",
        expect_flow_lint_error_free=True,
        expect_flow_node_types_include=["browser.extract", "file.write"],
        expect_flow_node_types_exclude=["script.python"],
    ),
    Scenario(
        stop_after_tool="create_flow",
        name="gen_table_to_excel",
        description="导出 Excel 必须用 excel.* 节点链，而不是一个 openpyxl 脚本节点",
        user_message="创建流程：打开 https://example.com/list，把表格数据导出成 Excel 文件。无需登录。",
        expect_flow_node_types_include=["excel.addrow", "excel.save"],
        expect_flow_node_types_exclude=["script.python"],
        # 这条守提示词里的「常用节点组合模式」表：实测带表 3/3、删表 0/3（全塌成 script.python）。
        # 阈值留噪音带，掉到 1/3 才报——它守的是软偏好，不是硬不变量
        min_pass_rate=0.6,
    ),
    Scenario(
        stop_after_tool="create_flow",
        name="gen_login_then_navigate",
        description="登录成功 ≠ 已在数据页，登录分支合流后必须再导航一次",
        user_message=(
            "创建流程：登录 https://example.com/admin（账号 demo_user，密码 demo_pass），"
            "然后抓取订单列表表格存成 JSON。"
        ),
        expect_flow_lint_error_free=True,
        # 不断言 browser.ensureLogin：无条件登录也是提示词允许的写法。
        # 「登录后有没有再导航一次」交给 single_navigation_node 这类 error 级 lint 判
        expect_flow_node_types_include=["browser.open", "browser.extract"],
    ),
    Scenario(
        stop_after_tool="create_flow",
        name="gen_paginated_scrape",
        description="分页抓取要用 browser.paginateNext，而不是自己搭循环点下一页",
        user_message="创建流程：https://example.com/list 有分页，把所有页的表格数据都抓下来保存为 JSON。无需登录。",
        expect_flow_lint_error_free=True,
        expect_flow_node_types_include=["browser.paginateNext"],
    ),
    Scenario(
        stop_after_tool="create_flow",
        name="gen_api_to_file",
        description="取数必须走 http.request 原生节点，且必须真的建出流程",
        user_message="创建流程：调用 https://api.example.com/orders 这个 GET 接口，把返回的 JSON 里每条订单写进本地文件。",
        expect_flow_created=True,
        expect_flow_lint_error_free=True,
        # 不断言 file.write：「每条订单一行」是 JSONL，且要从未知信封键里取数组，
        # 原生节点表达不了（file.write 只写整块、无追加），落到 script.python 是提示词要求的正解
        expect_flow_node_types_include=["http.request"],
    ),
]


# ── 运行与断言 ────────────────────────────────────────────────────────────────

# 录像存模型这一轮的全部输出（回复 + 工具调用入参），供 --replay 免调模型复判。
# 改断言、调阈值走重放，不要重跑生成。
_RECORDINGS_DIR = Path(__file__).resolve().parent / "recordings"


def _recording_path(model: str, scenario_name: str, rep: int) -> Path:
    return _RECORDINGS_DIR / model.replace("/", "_") / f"{scenario_name}-{rep + 1}.json"


def _replay_scenario(scenario: Scenario, model: str, rep: int) -> list[str]:
    path = _recording_path(model, scenario.name, rep)
    if not path.exists():
        return [f"缺少录像 {path.name}，先用 --record 跑一次"]
    recorded = json.loads(path.read_text(encoding="utf-8"))
    executor = MockToolExecutor(scenario.tool_overrides)
    executor.calls = [(c["name"], c["args"]) for c in recorded["calls"]]
    return _judge_scenario(scenario, recorded["reply"], executor)


async def run_scenario(
    scenario: Scenario, model: str, config: AiConfigService, rep: int = 0, record: bool = False
) -> list[str]:
    """跑一个场景，返回失败原因列表（空 = 通过）。"""
    executor = MockToolExecutor(scenario.tool_overrides)
    orchestrator = AiOrchestrator(tool_executor=executor, config_service=config)

    reply_parts: list[str] = []
    errors: list[str] = []
    stream = orchestrator.stream(
        messages=[{"role": "user", "content": scenario.user_message}],
        model=model,
        flow_id=scenario.flow_id,
    )
    try:
        async for event in stream:
            if event.get("type") == "text":
                reply_parts.append(str(event.get("delta") or ""))
            elif event.get("type") == "error":
                errors.append(f"LLM 错误：{event.get('message')}")
            # 后续回合每轮都重发整个系统提示词，判分用不上就别让它继续
            if scenario.stop_after_tool and scenario.stop_after_tool in executor.called_tools():
                break
    except Exception as exc:
        errors.append(f"运行异常：{exc}")
    finally:
        await stream.aclose()
    if errors:
        return errors

    reply = "".join(reply_parts)
    if record:
        path = _recording_path(model, scenario.name, rep)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"model": model, "scenario": scenario.name, "reply": reply,
             "calls": [{"name": n, "args": a} for n, a in executor.calls]},
            ensure_ascii=False, indent=2,
        ), encoding="utf-8")
    return _judge_scenario(scenario, reply, executor)


def _judge_scenario(scenario: Scenario, reply: str, executor: MockToolExecutor) -> list[str]:
    """只依据模型输出判分，真跑与 --replay 共用，改判分逻辑只改这里。"""
    called = executor.called_tools()
    failures: list[str] = []

    if scenario.expect_no_tools and called:
        failures.append(f"期望不调用工具，实际调用了 {called}")
    if scenario.expect_first_tool and (not called or called[0] != scenario.expect_first_tool):
        failures.append(f"期望首个工具是 {scenario.expect_first_tool}，实际 {called[:3]}")
    for tool in scenario.expect_tools_called:
        if tool not in called:
            failures.append(f"期望调用 {tool}，实际未调用（调用序列 {called}）")
    for tool in scenario.expect_tools_not_called:
        if tool in called:
            failures.append(f"禁止调用 {tool}，实际调用了（调用序列 {called}）")
    for earlier, later in scenario.expect_tool_order:
        if earlier in called and later in called and called.index(earlier) > called.index(later):
            failures.append(f"期望 {earlier} 先于 {later}，实际顺序 {called}")
    for tool, max_calls in scenario.expect_tool_max_calls.items():
        actual = called.count(tool)
        if actual > max_calls:
            failures.append(f"{tool} 最多允许 {max_calls} 次，实际 {actual} 次")
    if scenario.expect_reply_contains_any and not any(kw in reply for kw in scenario.expect_reply_contains_any):
        failures.append(
            f"回复未包含任一关键词 {scenario.expect_reply_contains_any}（回复前120字：{reply[:120]!r}）"
        )
    failures.extend(_check_generated_flow(scenario, executor))
    return failures


def _check_generated_flow(scenario: Scenario, executor: MockToolExecutor) -> list[str]:
    """对模型真正提交的 nodes/edges 判分。"""
    wants_flow = (
        scenario.expect_flow_created
        or scenario.expect_flow_lint_error_free
        or scenario.expect_flow_node_types_include
        or scenario.expect_flow_node_types_exclude
    )
    if not wants_flow:
        return []

    submitted = [args for name, args in executor.calls if name in ("create_flow", "update_flow")]
    if not submitted:
        return [f"未提交任何流程定义（调用序列 {executor.called_tools()}）"]

    payload = submitted[0]
    definition = payload.get("definition") if isinstance(payload.get("definition"), dict) else payload
    # 先过生产的归一化，否则入口本来就会补的字段会算成模型的错
    nodes = _normalize_generated_nodes(definition.get("nodes") or [])
    edges = _normalize_generated_edges(definition.get("edges") or [])
    node_types = [str(n.get("type") or "") for n in nodes if isinstance(n, dict)]

    failures: list[str] = []
    if scenario.expect_flow_lint_error_free:
        errors = [f for f in _lint_flow(nodes, edges) if f.get("severity") == "error"]
        if errors:
            failures.append(
                "生成的流程有 error 级 lint："
                + "；".join(f"{f['issue']}@{f.get('node_id')}" for f in errors)
            )
    for wanted in scenario.expect_flow_node_types_include:
        if wanted not in node_types:
            failures.append(f"生成的流程缺少节点类型 {wanted}（实际 {node_types}）")
    for banned in scenario.expect_flow_node_types_exclude:
        if banned in node_types:
            failures.append(f"生成的流程不该出现节点类型 {banned}（实际 {node_types}）")
    return failures


def _resolve_model_and_key(config: AiConfigService, model_arg: str | None) -> tuple[str, bool]:
    model = model_arg or str(config.load().get("default_model") or "")
    if not model:
        return "", False
    has_key = bool(config.get_api_key_for_model(model)) or bool(config.get_base_url_for_model(model))
    if not has_key:
        env_key = next((m.get("env_key", "") for m in AI_MODEL_CATALOG if m.get("id") == model), "")
        has_key = bool(env_key and os.environ.get(env_key))
    return model, has_key


async def main() -> int:
    parser = argparse.ArgumentParser(description="RPA 助手行为评测")
    parser.add_argument("--model", help="覆盖默认模型")
    parser.add_argument("--only", help="只跑指定场景（逗号分隔）")
    parser.add_argument("--reps", type=int, default=1, help="每个场景重复次数，按通过率判定")
    parser.add_argument("--record", action="store_true", help="把模型输出存进 evals/recordings/")
    parser.add_argument("--replay", action="store_true", help="只重放录像判分，不调模型、不花 token")
    args = parser.parse_args()

    config = AiConfigService()
    config.apply_to_env(config.load())
    model, has_key = _resolve_model_and_key(config, args.model)
    if args.replay and model:
        has_key = True  # 重放不调模型，没 key 也该能判分
    if not model or not has_key:
        print(f"⚠️  模型 {model or '(未配置)'} 无可用 API Key，跳过评测（exit 0）。")
        return 0

    selected = SCENARIOS
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        selected = [s for s in SCENARIOS if s.name in wanted]
        if not selected:
            print(f"未找到场景：{args.only}；可用：{[s.name for s in SCENARIOS]}")
            return 2

    reps = max(1, args.reps)
    mode = "重放录像" if args.replay else ("真跑并录像" if args.record else "真跑")
    print(f"模型：{model} | 场景数：{len(selected)} | 每场景 {reps} 次 | {mode}\n")
    passed = 0
    results: list[tuple[str, list[str]]] = []
    for scenario in selected:
        print(f"▶ {scenario.name} — {scenario.description}")
        all_failures: list[str] = []
        ok_runs = 0
        for rep in range(reps):
            if args.replay:
                failures = _replay_scenario(scenario, model, rep)
            else:
                failures = await run_scenario(scenario, model, config, rep, record=args.record)
            if failures:
                all_failures.extend(f"[第{rep + 1}次] {f}" for f in failures)
            else:
                ok_runs += 1
        rate = ok_runs / reps
        # 达标也照打失败详情：阈值抗的是随机噪音，不该掩盖退化
        if rate + 1e-9 >= scenario.min_pass_rate:
            passed += 1
            print(f"  ✓ 通过（{ok_runs}/{reps}，阈值 {scenario.min_pass_rate:.0%}）")
            for f in all_failures:
                print(f"    · {f}")
        else:
            results.append((scenario.name, all_failures))
            print(f"  ✗ 通过率 {ok_runs}/{reps} 低于阈值 {scenario.min_pass_rate:.0%}")
            for f in all_failures:
                print(f"    · {f}")
        print()

    print(f"结果：{passed}/{len(selected)} 通过")
    if passed < len(selected):
        print(json.dumps(
            {name: fails for name, fails in results if fails},
            ensure_ascii=False, indent=2,
        ))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
