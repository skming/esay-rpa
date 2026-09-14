from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import json

from app.models.schemas import (
    FlowSnapshot,
    RuntimeProgress,
    TaskLogEntry,
    TaskSnapshot,
)
from app.services.ai_orchestrator import (
    _is_explicit_channel_switch_request,
    _orchestrator_guard_after_tool,
    _orchestrator_guard_before_tool,
    _parse_tool_arguments,
    _session_requirement_text,
    _overstated_result_claim,
    _unmet_verification_request,
)
from app.services.ai_tools.executor import (
    _profile_busy_block,
    _splice_branch_placeholder_noops,
)
from app.services.ai_tools.page_observation import annotate_observation, classify_page_outcome
from app.services.ai_tools import RpaToolExecutor
from app.services.ai_tools.diagnostics import (
    _check_structured_rows,
    _find_incomplete_sweeps,
    _find_ineffective_transforms,
    build_navigation_trace,
    build_navigation_verdict,
)
from app.services.ai_tools.schemas import validate_tool_arguments
from app.services.ai_tools.lint import _lint_flow, is_blocking_finding
from app.services.ai_tools.catalog import NODE_TYPE_CATALOG, select_node_types
from app.services.ai_tools.lint_scenarios import (
    _lint_claimed_semantic_capability,
    _lint_script_hardcoded_content,
    _lint_unavailable_artifact_format,
)
from app.services.ai_tools.script_capabilities import (
    SEMANTIC_NODE_PREFIXES,
    describe_script_capabilities,
    semantic_rewrite_node_types,
)
from app.services.ai_tools.normalize import _normalize_generated_edges, _normalize_generated_nodes
from app.services.ai_guard_state import GuardState


def _ready_state(**overrides: Any) -> GuardState:
    """一个可以直接跑流程的会话：流程已存在、证据到手、诊断干净、用户授权。

    阶段机的事实全部 fail-closed，空 GuardState 会被判成「流程还不存在」，
    断言到的就不是这次改动而是缺省值。
    """
    state = GuardState(
        flow_has_nodes=True,
        page_evidence_required=None,
        page_evidence_done=True,
        run_authorized=True,
    )
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


def _valid_contract(variable: str) -> dict[str, Any]:
    return {
        "requirements": [{
            "id": "test-requirement",
            "description": "测试交付要求",
            "source_kind": "product_default",
            "confidence": 1,
            "confirmed": True,
        }],
        "deliverables": [{
            "id": "test-deliverable",
            "variable": variable,
            "kind": "scalar",
            "requirement_ids": ["test-requirement"],
        }],
    }


def test_lint_flow_reports_visual_overlap_for_crowded_branch_columns() -> None:
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 560, "y": 20}},
        {"id": "n_cond", "type": "control.condition", "title": "判断", "position": {"x": 560, "y": 420}},
        {"id": "n_login", "type": "browser.fill", "title": "填写账号", "selector": "input", "position": {"x": 360, "y": 620}},
        {"id": "n_nav", "type": "browser.click", "title": "点击菜单", "selector": "text=Reports", "position": {"x": 560, "y": 620}},
        {"id": "end", "type": "end", "position": {"x": 560, "y": 740}},
    ]
    edges = [
        {"source": "start", "target": "n_cond"},
        {"source": "n_cond", "target": "n_login", "label": "true"},
        {"source": "n_cond", "target": "n_nav", "label": "false"},
        {"source": "n_nav", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges)

    assert any(finding["issue"] == "node_visual_overlap" for finding in findings)


def test_normalize_layout_spreads_columns_and_removes_visual_overlap() -> None:
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 560, "y": 20}},
        {"id": "n_cond", "type": "control.condition", "title": "判断", "position": {"x": 560, "y": 420}},
        {"id": "n_login", "type": "browser.fill", "title": "填写账号", "position": {"x": 360, "y": 620}},
        {"id": "n_nav", "type": "browser.click", "title": "点击菜单", "position": {"x": 560, "y": 620}},
        {"id": "end", "type": "end", "position": {"x": 560, "y": 740}},
    ]
    edges = [
        {"source": "start", "target": "n_cond"},
        {"source": "n_cond", "target": "n_login", "label": "true"},
        {"source": "n_cond", "target": "n_nav", "label": "false"},
        {"source": "n_login", "target": "n_nav"},
        {"source": "n_nav", "target": "end"},
    ]

    RpaToolExecutor._normalize_layout(nodes, edges)
    findings = _lint_flow(nodes, edges)

    assert not any(finding["issue"] == "node_visual_overlap" for finding in findings)
    x_positions = {node["position"]["x"] for node in nodes if node["id"] in {"n_login", "n_nav"}}
    assert max(x_positions) - min(x_positions) >= 280


def test_normalize_layout_keeps_realistic_login_and_navigation_flow_readable() -> None:
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 560, "y": 20}},
        {"id": "n_init", "type": "variable.set", "position": {"x": 560, "y": 120}},
        {"id": "n1", "type": "browser.open", "position": {"x": 560, "y": 220}},
        {"id": "n_check", "type": "browser.extract", "selector": "input[type=password]", "countVariable": "login_count", "position": {"x": 560, "y": 320}},
        {"id": "n_cond", "type": "control.condition", "position": {"x": 560, "y": 420}},
        {"id": "n6", "type": "browser.fill", "selector": "input", "position": {"x": 360, "y": 620}},
        {"id": "n7", "type": "browser.fill", "selector": "input[type=password]", "position": {"x": 360, "y": 720}},
        {"id": "n8", "type": "browser.click", "selector": "button", "position": {"x": 360, "y": 820}},
        {"id": "n9", "type": "browser.wait", "selector": "nav", "position": {"x": 360, "y": 920}},
        {"id": "n_nav_menu", "type": "browser.click", "selector": "text=Reports", "position": {"x": 560, "y": 520}},
        {"id": "n_nav_sub", "type": "browser.click", "selector": "text=Daily", "position": {"x": 560, "y": 620}},
        {"id": "n13", "type": "browser.wait", "selector": "table", "position": {"x": 560, "y": 720}},
        {"id": "end", "type": "end", "position": {"x": 560, "y": 840}},
    ]
    edges = [
        {"source": "start", "target": "n_init"},
        {"source": "n_init", "target": "n1"},
        {"source": "n1", "target": "n_check"},
        {"source": "n_check", "target": "n_cond"},
        {"source": "n_cond", "target": "n6", "label": "true"},
        {"source": "n6", "target": "n7"},
        {"source": "n7", "target": "n8"},
        {"source": "n8", "target": "n9"},
        {"source": "n9", "target": "n_nav_menu"},
        {"source": "n_cond", "target": "n_nav_menu", "label": "false"},
        {"source": "n_nav_menu", "target": "n_nav_sub"},
        {"source": "n_nav_sub", "target": "n13"},
        {"source": "n13", "target": "end"},
    ]

    RpaToolExecutor._normalize_layout(nodes, edges)
    findings = _lint_flow(nodes, edges)

    assert not any(finding["issue"] == "node_visual_overlap" for finding in findings)
    by_id = {node["id"]: node for node in nodes}
    assert by_id["n6"]["position"]["x"] < by_id["n_nav_menu"]["position"]["x"]
    assert by_id["n_nav_sub"]["position"]["y"] > by_id["n_nav_menu"]["position"]["y"]


async def test_create_flow_rejects_missing_or_unbound_acceptance_contract_before_persisting() -> None:
    executor = RpaToolExecutor(  # type: ignore[arg-type]
        flow_service=SimpleNamespace(),
        task_manager=SimpleNamespace(),
    )
    nodes = [{
        "id": "extract",
        "type": "browser.extract",
        "selector": ".rows",
        "outputVariable": "rows",
        "position": {"x": 0, "y": 100},
    }]

    missing = await executor.execute("create_flow", {"name": "订单", "nodes": nodes})
    unbound = await executor.execute("create_flow", {
        "name": "订单",
        "nodes": nodes,
        "acceptance_contract": _valid_contract("unknown"),
    })

    assert missing["error"] == "invalid_arguments"
    assert unbound["error"] == "acceptance_contract_invalid"
    assert any("unknown" in issue for issue in unbound["contract_errors"])


async def test_create_flow_refuses_to_persist_credential_values() -> None:
    """写盘这一层才真正拥有「秘密值不落盘」这条不变量。

    编排层同名护栏在调用前就拦了，这里再判一次是因为落盘的代价不可逆：值会进流程定义、
    进快照、进导出。护栏表重排、换调用方、或有人绕过编排直接调执行器都不该动摇它。
    `defaultValue` 一起判——下面 input_variables 就是把它当 `value` 的别名收下的。
    """
    executor = RpaToolExecutor(  # type: ignore[arg-type]
        flow_service=SimpleNamespace(),
        task_manager=SimpleNamespace(),
    )
    nodes = [{"id": "extract", "type": "browser.extract", "selector": ".rows", "outputVariable": "rows"}]

    for variable in (
        {"name": "password", "category": "credential", "value": "s3cret"},
        {"name": "api_token", "defaultValue": "t-123"},
    ):
        blocked = await executor.execute("create_flow", {
            "name": "订单", "nodes": nodes, "input_variables": [variable],
            "acceptance_contract": _valid_contract("rows"),
        })
        assert blocked["status"] == "blocked_credential_values"
        assert blocked["exposed_variables"] == [variable["name"]]
        # 值本身不能回到模型上下文里
        assert "s3cret" not in json.dumps(blocked, ensure_ascii=False)
        assert "t-123" not in json.dumps(blocked, ensure_ascii=False)


def test_normalize_layout_ignores_ai_dirty_positions_and_places_join_after_branch() -> None:
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 9999, "y": 9999}},
        {"id": "check", "type": "control.condition", "position": {"x": 0, "y": 0}},
        {"id": "login_user", "type": "browser.fill", "position": {"x": 10, "y": 10}},
        {"id": "login_pwd", "type": "browser.fill", "position": {"x": 10, "y": 10}},
        {"id": "login_submit", "type": "browser.click", "position": {"x": 10, "y": 10}},
        {"id": "nav", "type": "browser.click", "position": {"x": 10, "y": 10}},
        {"id": "table", "type": "browser.extract", "position": {"x": 10, "y": 10}},
        {"id": "end", "type": "end", "position": {"x": -999, "y": -999}},
    ]
    edges = [
        {"source": "start", "target": "check"},
        {"source": "check", "target": "login_user", "label": "true"},
        {"source": "login_user", "target": "login_pwd"},
        {"source": "login_pwd", "target": "login_submit"},
        {"source": "login_submit", "target": "nav"},
        {"source": "check", "target": "nav", "label": "false"},
        {"source": "nav", "target": "table"},
        {"source": "table", "target": "end"},
    ]

    RpaToolExecutor._normalize_layout(nodes, edges)

    by_id = {node["id"]: node for node in nodes}
    assert by_id["login_user"]["position"]["x"] < by_id["check"]["position"]["x"]
    assert by_id["nav"]["position"]["x"] == by_id["check"]["position"]["x"]
    assert by_id["nav"]["position"]["y"] > by_id["login_submit"]["position"]["y"]
    assert by_id["table"]["position"]["y"] > by_id["nav"]["position"]["y"]
    assert by_id["end"]["position"]["y"] > by_id["table"]["position"]["y"]
    assert not any(finding["issue"] == "node_visual_overlap" for finding in _lint_flow(nodes, edges))


def test_normalize_layout_splits_condition_siblings_into_stable_lanes() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {"id": "condition", "type": "control.condition"},
        {"id": "when_true", "type": "browser.wait", "position": {"x": 560, "y": 140}},
        {"id": "when_false", "type": "browser.wait", "position": {"x": 560, "y": 140}},
        {"id": "join", "type": "browser.extract", "position": {"x": 560, "y": 140}},
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "condition"},
        {"source": "condition", "target": "when_true", "label": "true"},
        {"source": "condition", "target": "when_false", "label": "false"},
        {"source": "when_true", "target": "join"},
        {"source": "when_false", "target": "join"},
        {"source": "join", "target": "end"},
    ]

    RpaToolExecutor._normalize_layout(nodes, edges)

    by_id = {node["id"]: node for node in nodes}
    assert by_id["when_true"]["position"]["x"] < by_id["condition"]["position"]["x"]
    assert by_id["when_false"]["position"]["x"] > by_id["condition"]["position"]["x"]
    assert by_id["join"]["position"]["x"] == by_id["condition"]["position"]["x"]
    assert by_id["join"]["position"]["y"] > max(
        by_id["when_true"]["position"]["y"],
        by_id["when_false"]["position"]["y"],
    )
    assert not any(finding["issue"] == "node_visual_overlap" for finding in _lint_flow(nodes, edges))


def test_lint_flow_reports_diagnostic_bloat_without_node_count_budget() -> None:
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 560, "y": 20}},
        *[
            {
                "id": f"n{index}",
                "type": "browser.wait",
                "title": f"等待节点 {index}",
                "selector": "table",
                "position": {"x": 560, "y": 120 + index * 120},
            }
            for index in range(25)
        ],
        {"id": "n_diag_1", "type": "browser.screenshot", "title": "诊断截图", "position": {"x": 560, "y": 3200}},
        {"id": "n_diag_2", "type": "browser.extract", "title": "诊断链接", "selector": "a", "outputVariable": "links", "position": {"x": 560, "y": 3320}},
        {"id": "n_diag_3", "type": "variable.log", "title": "diag log", "message": "debug", "position": {"x": 560, "y": 3440}},
        {"id": "end", "type": "end", "position": {"x": 560, "y": 3560}},
    ]
    edges = [
        {"source": nodes[index]["id"], "target": nodes[index + 1]["id"]}
        for index in range(len(nodes) - 1)
    ]

    findings = _lint_flow(nodes, edges)

    assert any(finding["issue"] == "diagnostic_node_bloat" for finding in findings)
    assert not any(finding["issue"] == "flow_node_budget_exceeded" for finding in findings)


def test_lint_flow_reports_long_wait_and_login_detection_risk() -> None:
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 560, "y": 20}},
        {
            "id": "n_init",
            "type": "variable.set",
            "variableName": "login_count",
            "value": "0",
            "title": "初始化登录计数",
            "position": {"x": 560, "y": 120},
        },
        {
            "id": "n_check",
            "type": "browser.extract",
            "selector": "input[type='password']",
            "extractMode": "count",
            "countVariable": "login_count",
            "continueOnError": True,
            "timeoutMs": 3000,
            "title": "检测登录表单",
            "position": {"x": 560, "y": 240},
        },
        {
            "id": "n_delay",
            "type": "control.delay",
            "delayMs": 15000,
            "title": "等待 SPA",
            "position": {"x": 560, "y": 360},
        },
        {"id": "end", "type": "end", "position": {"x": 560, "y": 480}},
    ]
    edges = [
        {"source": "start", "target": "n_init"},
        {"source": "n_init", "target": "n_check"},
        {"source": "n_check", "target": "n_delay"},
        {"source": "n_delay", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges)

    assert any(finding["issue"] == "long_fixed_wait" for finding in findings)
    assert any(finding["issue"] == "login_detection_timeout_may_skip_login" for finding in findings)


def test_lint_flow_rejects_node_fields_nobody_reads() -> None:
    """写了没人读的键必须拦住：这是 pagination_sweep 真实的失败形态。

    模型把翻页上限写成 maxPages，执行层只读 maxIterations，上限静默变成默认 20，
    流程 success、验收通过、数据多了一页。只有第二组变体的外部重放才暴露出来。
    """
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 560, "y": 20}},
        {
            "id": "n_paginate",
            "type": "browser.paginateNext",
            "title": "累计分页行",
            "selector": "button.next-page",
            "targetSelector": "#grid tbody tr",
            "outputVariable": "rows",
            "maxPages": "${var.max_pages}",
            "position": {"x": 560, "y": 140},
        },
        {"id": "end", "type": "end", "position": {"x": 560, "y": 260}},
    ]
    edges = [
        {"source": "start", "target": "n_paginate"},
        {"source": "n_paginate", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges)
    unread = [f for f in findings if f["issue"] == "unread_node_field"]

    assert [f["node_id"] for f in unread] == ["n_paginate"]
    assert "maxPages" in unread[0]["message"]
    # 只提示「有个键没人读」不够，模型得知道改成哪个字段
    assert "maxIterations" in unread[0]["fix"]
    # warn 级但必须挡住 run_flow：跑完才发现等于白跑一趟真实站点
    assert unread[0]["severity"] == "warn"
    assert is_blocking_finding(unread[0])


def test_lint_flow_accepts_the_page_limit_field_the_executor_actually_reads() -> None:
    """同一个流程改用 maxIterations 就不该再报——判据挂「谁会读这个键」，不是挂字段数量。"""
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 560, "y": 20}},
        {
            "id": "n_paginate",
            "type": "browser.paginateNext",
            "title": "累计分页行",
            "selector": "button.next-page",
            "targetSelector": "#grid tbody tr",
            "outputVariable": "rows",
            "maxIterations": "${var.max_pages}",
            "position": {"x": 560, "y": 140},
        },
        {"id": "end", "type": "end", "position": {"x": 560, "y": 260}},
    ]
    edges = [
        {"source": "start", "target": "n_paginate"},
        {"source": "n_paginate", "target": "end"},
    ]

    assert not [f for f in _lint_flow(nodes, edges) if f["issue"] == "unread_node_field"]


def test_lint_flow_rejects_temporary_element_refs_written_into_the_flow() -> None:
    """interact_page 的 ref 只在那一次观察里有效，存进流程等于每次运行都找不到元素。

    而它长得像 selector，运行时报的是普通的元素超时，排查会一路往「selector 写错了」走，
    没人会想到这串字符本来就不是选择器。
    """
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 560, "y": 20}},
        {
            "id": "n_click",
            "type": "browser.click",
            "title": "点开日期面板",
            "selector": "e12",
            "position": {"x": 560, "y": 140},
        },
        {
            "id": "n_fill",
            "type": "browser.fill",
            "title": "填开始日期",
            "selector": "#start-date",
            "fallbackSelectors": "input[placeholder='开始日期']\ne7",
            "inputValue": "2026-06-01",
            "position": {"x": 560, "y": 260},
        },
        {"id": "end", "type": "end", "position": {"x": 560, "y": 380}},
    ]
    edges = [
        {"source": "start", "target": "n_click"},
        {"source": "n_click", "target": "n_fill"},
        {"source": "n_fill", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges)
    refs = [f for f in findings if f["issue"] == "temp_element_ref_in_flow"]

    assert {f["node_id"] for f in refs} == {"n_click", "n_fill"}
    assert all(f["severity"] == "error" for f in refs)
    # 同一节点里合法的那条 fallback 不该被牵连报出来
    assert all("placeholder" not in f["message"] for f in refs)


def test_lint_flow_keeps_quiet_on_selectors_that_merely_look_like_refs() -> None:
    """真实 selector 里也会出现 e 开头的类名/元素名，判据必须是整串就是 ref。"""
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 560, "y": 20}},
        {
            "id": "n_click",
            "type": "browser.click",
            "title": "查询",
            "selector": ".el-button--primary",
            "fallbackSelectors": "#e2e-search\nform e12 button",
            "position": {"x": 560, "y": 140},
        },
        {"id": "end", "type": "end", "position": {"x": 560, "y": 260}},
    ]
    edges = [
        {"source": "start", "target": "n_click"},
        {"source": "n_click", "target": "end"},
    ]

    assert not any(f["issue"] == "temp_element_ref_in_flow" for f in _lint_flow(nodes, edges))


def test_lint_flow_reports_foreach_ambiguous_edges_and_missing_excel_row_data() -> None:
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 560, "y": 20}},
        {
            "id": "loop",
            "type": "control.foreach",
            "title": "遍历主题",
            "itemsVariable": "topics",
            "itemVariable": "topic",
            "position": {"x": 560, "y": 140},
        },
        {
            "id": "write",
            "type": "excel.addrow",
            "title": "写入主题行",
            "path": "${var.output_prefix}.csv",
            "position": {"x": 560, "y": 260},
        },
        {"id": "save", "type": "excel.save", "path": "${var.output_prefix}.csv", "position": {"x": 560, "y": 380}},
        {"id": "end", "type": "end", "position": {"x": 560, "y": 500}},
    ]
    edges = [
        {"source": "start", "target": "loop"},
        {"source": "loop", "target": "write"},
        {"source": "loop", "target": "save"},
        {"source": "write", "target": "loop"},
        {"source": "save", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges)

    assert any(finding["issue"] == "foreach_missing_body_edge" for finding in findings)
    assert any(finding["issue"] == "foreach_missing_exit_edge" for finding in findings)
    assert any(finding["issue"] == "foreach_ambiguous_unlabeled_edges" for finding in findings)
    assert any(finding["issue"] == "excel_addrow_missing_row_data" for finding in findings)


def test_lint_flow_reports_noncanonical_path_field() -> None:
    nodes = [
        {"id": "start", "type": "start", "position": {"x": 560, "y": 20}},
        {
            "id": "write",
            "type": "file.write",
            "title": "保存到 CSV 文件",
            "filePath": "${var.output_prefix}.csv",
            "content": "${var.topics}",
            "position": {"x": 560, "y": 140},
        },
        {"id": "end", "type": "end", "position": {"x": 560, "y": 260}},
    ]
    edges = [{"source": "start", "target": "write"}, {"source": "write", "target": "end"}]

    findings = _lint_flow(nodes, edges)

    assert any(finding["issue"] == "noncanonical_path_field" for finding in findings)


def test_normalize_generated_nodes_flattens_model_specific_shapes() -> None:
    nodes = _normalize_generated_nodes([
        {
            "id": "n1",
            "action": {"type": "browser.open", "url": "https://example.com"},
            "config": {"timeoutMs": "30000"},
        },
        {
            "id": "n2",
            "type": "browser.fill",
            "config": {"selector": "input", "value": "admin", "delayMs": "500"},
        },
        {
            "id": "n3",
            "type": "file.write",
            "config": {"filePath": "runs/out_${var.run_timestamp}.csv", "value": "hello"},
        },
        {
            "id": "n4",
            "type": "excel.addrow",
            "config": {"filePath": "runs/out_${var.run_timestamp}.csv", "row": "${var.topic}"},
        },
    ])
    edges = _normalize_generated_edges([
        {"source": "n1", "target": "n2", "label": "Yes"},
        {"source": "n2", "target": "end", "label": "循环体"},
        {"source": "n4", "target": "end", "label": "complete"},
    ])

    assert nodes[0]["type"] == "browser.open"
    assert nodes[0]["targetUrl"] == "https://example.com"
    assert nodes[0]["timeoutMs"] == 30000
    assert nodes[0]["kind"] == "browser"
    assert nodes[1]["inputValue"] == "admin"
    assert nodes[1]["delayMs"] == 500
    assert nodes[2]["path"] == "runs/out_${var.run_timestamp}.csv"
    assert nodes[2]["content"] == "hello"
    assert nodes[3]["path"] == "runs/out_${var.run_timestamp}.csv"
    assert nodes[3]["rowData"] == "${var.topic}"
    assert edges[0]["label"] == "true"
    assert edges[1]["label"] == "body"
    assert edges[2]["label"] == "exit"


class FakeFlowService:
    async def get_flow(self, flow_id: str) -> FlowSnapshot:
        now = datetime.now(UTC)
        return FlowSnapshot(
            flowId=flow_id,
            name="失败预算测试",
            version="v1.0.0",
            status="active",
            inputVariables=[],
            acceptanceContract=_valid_contract("login_count"),
            definition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "n_check", "type": "browser.extract", "selector": "input[type='password']", "countVariable": "login_count", "continueOnError": True},
                    {"id": "n13", "type": "browser.wait", "selector": "table"},
                ],
                "edges": [{"source": "start", "target": "n13"}],
            },
            createdAt=now,
            updatedAt=now,
        )


class FakeTaskManager:
    def __init__(
        self,
        *,
        with_failing_tasks: bool = True,
        extension_connected: bool = False,
        extension_enabled: bool = True,
        extension_holder: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self.started = False
        self.started_request = None
        self.extension_connected = extension_connected
        self.extension_enabled = extension_enabled
        self.extension_holder = extension_holder
        self.tasks = (
            [
                TaskSnapshot(
                    taskId=f"task-{index}",
                    flowId="flow-1",
                    flowName="失败预算测试",
                    mode="run",
                    status="error",
                    progress=RuntimeProgress(currentStep=1, totalSteps=2, percent=50, elapsedMs=1000),
                    error="Timeout waiting for selector table",
                    createdAt=now - timedelta(minutes=index),
                    updatedAt=now - timedelta(minutes=index),
                )
                for index in range(3)
            ]
            if with_failing_tasks
            else []
        )

    async def list_tasks(self, *, flow_id: str | None = None, schedule_id: str | None = None, limit: int = 50):
        return self.tasks[:limit]

    async def get_task(self, task_id: str):
        return next((task for task in self.tasks if task.task_id == task_id), None)

    async def get_logs(self, task_id: str):
        return [
            TaskLogEntry(
                taskId=task_id,
                level="error",
                message="浏览器动作失败，继续执行 · 检测登录表单",
                detail="Timeout waiting for selector input[type='password']",
                nodeId="n_check",
            ),
            TaskLogEntry(
                taskId=task_id,
                level="error",
                message="任务失败",
                detail="Timeout waiting for selector table",
                nodeId="n13",
            )
        ]

    async def start_task(self, request):  # pragma: no cover - 应被熔断阻止
        self.started = True
        self.started_request = request
        return self.tasks[0] if self.tasks else TaskSnapshot(
            taskId="task-new",
            flowId="flow-1",
            flowName="失败预算测试",
            mode="run",
            status="running",
            progress=RuntimeProgress(currentStep=0, totalSteps=2, percent=0, elapsedMs=0),
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
        )

    def is_extension_connected(self) -> bool:
        return self.extension_connected

    def is_extension_enabled(self) -> bool:
        return self.extension_enabled

    def extension_run_holder(self) -> str | None:
        return self.extension_holder


async def test_run_flow_blocks_after_repeated_similar_failures() -> None:
    task_manager = FakeTaskManager()
    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._run_flow("flow-1")

    assert result["status"] == "blocked_by_failure_budget"
    assert result["recent_failed_nodes"] == ["n13", "n13", "n13"]
    assert task_manager.started is False


async def test_check_extension_connection_reports_connected_state() -> None:
    task_manager = FakeTaskManager(extension_connected=True)
    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._check_extension_connection()

    assert result["connected"] is True


async def test_check_extension_connection_reports_disconnected_state() -> None:
    task_manager = FakeTaskManager(extension_connected=False)
    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._check_extension_connection()

    assert result["connected"] is False
    assert "未连接" in result["message"]


async def test_run_flow_blocks_when_extension_requested_but_not_connected() -> None:
    task_manager = FakeTaskManager(with_failing_tasks=False, extension_connected=False)
    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._run_flow("flow-1", browser_executor="extension")

    assert result["status"] == "extension_not_connected"
    assert task_manager.started is False


async def test_run_flow_blocks_when_extension_disabled_in_settings() -> None:
    """开关关掉时不能只报"未连接"：那会让模型一路催用户去开浏览器，而用户浏览器早就开着了。"""
    task_manager = FakeTaskManager(with_failing_tasks=False, extension_connected=True, extension_enabled=False)
    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._run_flow("flow-1", browser_executor="extension")

    assert result["status"] == "extension_disabled"
    assert "设置" in result["message"]
    assert task_manager.started is False


async def test_check_extension_connection_reports_disabled_switch() -> None:
    task_manager = FakeTaskManager(extension_connected=True, extension_enabled=False)
    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._check_extension_connection()

    assert result["enabled"] is False
    assert "设置" in result["message"]


class _SimpleFlowService:
    """Minimal flow with every node reachable, so lint_flow doesn't block the run."""

    async def get_flow(self, flow_id: str) -> FlowSnapshot:
        now = datetime.now(UTC)
        return FlowSnapshot(
            flowId=flow_id,
            name="扩展执行器测试",
            version="v1.0.0",
            status="active",
            inputVariables=[],
            acceptanceContract=_valid_contract("page_opened"),
            definition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "n1", "type": "browser.open", "targetUrl": "https://example.com", "outputVariable": "page_opened"},
                ],
                "edges": [{"source": "start", "target": "n1"}],
            },
            createdAt=now,
            updatedAt=now,
        )


async def test_run_flow_threads_browser_executor_into_request_when_extension_connected() -> None:
    task_manager = FakeTaskManager(with_failing_tasks=False, extension_connected=True)
    executor = RpaToolExecutor(flow_service=_SimpleFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._run_flow("flow-1", browser_executor="extension")

    assert task_manager.started is True
    assert task_manager.started_request.browser_executor == "extension"
    assert result.get("status") != "extension_not_connected"


async def test_run_failure_includes_diagnostics_and_execution_signature(monkeypatch) -> None:
    from unittest.mock import AsyncMock
    from app.services.ai_tools.lint_diff import execution_signature

    task_manager = FakeTaskManager()
    task_manager.tasks = task_manager.tasks[:1]
    executor = RpaToolExecutor(flow_service=_SimpleFlowService(), task_manager=task_manager)
    diagnostics = AsyncMock(return_value={
        "task_id": "task-0", "status": "error", "failed_node_id": "n1",
        "inspect_hint": "inspect", "image_base64": "binary", "failure_screenshot_note": "attached",
    })
    monkeypatch.setattr(executor, "_get_run_error", diagnostics)
    result = await executor._run_flow("flow-1")
    assert result["error_summary"] == task_manager.tasks[0].error
    assert len(result["execution_signature"]) == 64
    assert result["failure_diagnostics"] == {"failed_node_id": "n1", "inspect_hint": "inspect"}
    diagnostics.assert_awaited_once_with("task-0")
    original = {"nodes": [{"id": "n1", "type": "browser.click", "selector": "button"}]}
    layout = {"nodes": [{**original["nodes"][0], "position": {"x": 20}, "status": "success"}]}
    changed = {"nodes": [{**original["nodes"][0], "selector": "a"}]}
    assert execution_signature(original) == execution_signature(layout)
    assert execution_signature(original) != execution_signature(changed)


class _CredentialFlowService:
    """凭据就绪判定的测试流程：变量是否被节点引用由 definition 决定。"""

    def __init__(self, variables: list[dict[str, Any]], reference: str | None = "${var.password}") -> None:
        self._variables = variables
        self._reference = reference

    async def get_flow(self, flow_id: str) -> FlowSnapshot:
        now = datetime.now(UTC)
        return FlowSnapshot(
            flowId=flow_id,
            name="凭据测试",
            version="v1.0.0",
            status="active",
            inputVariables=self._variables,
            acceptanceContract=_valid_contract("filled"),
            definition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "n1", "type": "browser.fill", "selector": "#pwd", "inputValue": self._reference or "x", "outputVariable": "filled"},
                ],
                "edges": [{"source": "start", "target": "n1"}],
            },
            createdAt=now,
            updatedAt=now,
        )


def _cred(name: str, value: str = "", category: str = "credential") -> dict[str, Any]:
    return {"name": name, "type": "String", "value": value, "category": category}


async def test_get_flow_computes_credential_readiness_instead_of_leaving_it_to_the_model() -> None:
    """判空条件有三个（是不是凭据/值空不空/有没有被引用），交给模型目测必然时对时错。"""
    executor = RpaToolExecutor(  # type: ignore[arg-type]
        flow_service=_CredentialFlowService([_cred("password")]),
        task_manager=FakeTaskManager(with_failing_tasks=False),
    )

    data = await executor._get_flow("flow-1")

    assert data["run_readiness"]["ready"] is False
    assert data["run_readiness"]["empty_credential_fields"] == ["password"]


async def test_get_flow_redacts_credential_values_before_returning_to_the_model() -> None:
    executor = RpaToolExecutor(  # type: ignore[arg-type]
        flow_service=_CredentialFlowService([
            _cred("password", "hunter2"),
            _cred("date_start", "2026-01-01", category="flow"),
        ]),
        task_manager=FakeTaskManager(with_failing_tasks=False),
    )

    data = await executor._get_flow("flow-1")
    variables = {item["name"]: item for item in data["input_variables"]}

    assert variables["password"]["value"] == ""
    assert variables["password"]["has_value"] is True
    assert variables["date_start"]["value"] == "2026-01-01"
    assert data["run_readiness"]["ready"] is True


async def test_credential_readiness_ignores_filled_and_unreferenced_fields() -> None:
    filled = RpaToolExecutor._credential_readiness(
        await _CredentialFlowService([_cred("password", "hunter2")]).get_flow("f")
    )
    assert filled["ready"] is True

    # 声明了却没人引用的空凭据不影响运行，报出来只会引出一次无谓的追问
    unreferenced = RpaToolExecutor._credential_readiness(
        await _CredentialFlowService([_cred("password")], reference=None).get_flow("f")
    )
    assert unreferenced["ready"] is True


async def test_run_flow_tells_the_model_to_ask_the_user_not_to_invent_credentials() -> None:
    """普通变量缺失可以让模型自己补，凭据不行——编一个密码只会换来一轮查选择器。"""
    task_manager = FakeTaskManager(with_failing_tasks=False)
    executor = RpaToolExecutor(  # type: ignore[arg-type]
        flow_service=_CredentialFlowService([_cred("password")]),
        task_manager=task_manager,
    )

    result = await executor._run_flow("flow-1")

    assert result["status"] == "empty_credential_variables"
    assert result["empty_credential_fields"] == ["password"]
    assert "不要自行编造" in result["message"]
    assert task_manager.started is False

    # 调用方真的传了值就照常跑：判据看的是 variables 参数，不是被默认值填满的 merged_variables
    ok = await executor._run_flow("flow-1", variables={"password": "hunter2"})
    assert ok["status"] != "empty_credential_variables"


async def _fake_sleep(_seconds: float) -> None:
    """轮询等待在测试里没有意义，真睡 90s 会把整个套件拖死。"""
    return None


async def test_run_flow_rejects_call_parameters_smuggled_into_variables() -> None:
    """browser_executor 写进 variables 会被当普通变量吞掉：不报错、不生效、照常跑完。"""
    task_manager = FakeTaskManager(with_failing_tasks=False, extension_connected=True)
    executor = RpaToolExecutor(flow_service=_SimpleFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._run_flow("flow-1", variables={"browser_executor": "extension"})

    assert result["status"] == "misplaced_call_parameters"
    assert result["misplaced_variables"] == ["browser_executor"]
    assert task_manager.started is False


class _TakeoverFlowService:
    """含人工接管节点的流程：运行会停在非终态等人，而不是跑得慢。"""

    def __init__(self, node_type: str) -> None:
        self._node_type = node_type

    async def get_flow(self, flow_id: str) -> FlowSnapshot:
        now = datetime.now(UTC)
        return FlowSnapshot(
            flowId=flow_id,
            name="等待用户测试",
            version="v1.0.0",
            status="active",
            inputVariables=[],
            acceptanceContract=_valid_contract("takeover_result"),
            definition={
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "n1", "type": self._node_type, "title": "等用户", "outputVariable": "takeover_result"},
                ],
                "edges": [{"source": "start", "target": "n1"}],
            },
            createdAt=now,
            updatedAt=now,
        )


async def test_run_flow_reports_paused_for_human_instead_of_timeout(monkeypatch) -> None:
    """判成 timeout 会让助手重跑，旧任务留在后台继续等——用户面前多一个孤儿任务。"""
    import asyncio as _asyncio

    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)
    task_manager = FakeTaskManager(with_failing_tasks=False, extension_connected=True)
    executor = RpaToolExecutor(flow_service=_TakeoverFlowService("control.human_takeover"), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._run_flow("flow-1")

    assert result["status"] == "paused_for_human"
    assert result["waiting_for_user_action"] is True
    assert "不要重新运行流程" in result["message"]


async def test_run_flow_reports_waiting_for_user_input_instead_of_timeout(monkeypatch) -> None:
    import asyncio as _asyncio

    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)
    task_manager = FakeTaskManager(with_failing_tasks=False, extension_connected=True)
    executor = RpaToolExecutor(flow_service=_TakeoverFlowService("variable.input"), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._run_flow("flow-1")

    assert result["status"] == "waiting_for_user_input"
    assert result["waiting_for_user_input"] is True


async def test_run_flow_blocks_when_another_run_holds_the_browser_profile() -> None:
    """浏览器被占用时若照常起跑，失败现场是一屏 Chrome 启动参数，模型会当成 selector 问题去改流程。"""
    from app.core import storage
    from app.services import browser_profile_lock

    profile = str(storage.resolve_browser_profile_dir())
    browser_profile_lock.acquire(profile, "抓取 NodeSeek 帖子内容 · 运行 t_1")
    try:
        task_manager = FakeTaskManager(with_failing_tasks=False, extension_connected=True)
        executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

        result = await executor._run_flow("flow-1")

        assert result["status"] == "blocked_browser_profile_busy"
        assert result["holder"] == "抓取 NodeSeek 帖子内容 · 运行 t_1"
        assert "不要改流程" in result["message"]

        # 插件执行器借用用户自己的浏览器，不受应用 profile 占用影响，不能顺手拦掉
        extension_result = await executor._run_flow("flow-1", browser_executor="extension")
        assert extension_result.get("status") != "blocked_browser_profile_busy"
    finally:
        browser_profile_lock.release(profile, "抓取 NodeSeek 帖子内容 · 运行 t_1")


async def test_inspect_page_is_not_blocked_by_its_own_exploration_session() -> None:
    """探索会话自己也在占用登记表里，「占用方是不是别人」必须区分出自己。

    不区分的后果：第一次 inspect_page(url=...) 打开会话之后，任何不带 url 的再次观察和
    同会话截图都被自己的登记挡成 blocked_browser_profile_busy——而通用日期配方的 fallback
    正是让模型点开弹层后再看一次当前页面，提示词描述的那条路整条走不通。
    """
    from app.core import storage
    from app.services import browser_profile_lock
    from app.services.ai_tools import page_session

    profile = str(storage.resolve_browser_profile_dir())

    @asynccontextmanager
    async def _fake_context(_profile: str, *, headless: bool = True) -> Any:
        yield SimpleNamespace(pages=[])

    await page_session.open_session(
        profile=profile, owner=page_session.SESSION_OWNER, context_factory=_fake_context
    )
    try:
        assert _profile_busy_block("inspect_page", allow_page_session=True) is None
        # run_flow 不能跟着放行：它在检查之前先关掉探索会话，此时还占着 profile 的只可能是别人
        blocked = _profile_busy_block("run_flow")
        assert blocked is not None and blocked["status"] == "blocked_browser_profile_busy"
    finally:
        await page_session.close_current("test_cleanup")

    # 登记名是所有探索会话共用的，所以放行不能只看登记名：会话已被空闲超时收走、或属于另一轮
    # 对话时，占用登记仍写着同一个名字。认成自己就会照常起跑去抢同一个 profile。
    browser_profile_lock.acquire(profile, page_session.SESSION_OWNER)
    try:
        stale = _profile_busy_block("inspect_page", allow_page_session=True)
        assert stale is not None and stale["status"] == "blocked_browser_profile_busy"
    finally:
        browser_profile_lock.release(profile, page_session.SESSION_OWNER)


async def test_run_flow_blocks_when_another_run_holds_the_extension() -> None:
    """两次运行交错操作用户那一个浏览器窗口，症状只是「选择器找不到元素」，模型会照着这个假象
    一路改流程。必须在起跑前拦住并点名占用方。"""
    task_manager = FakeTaskManager(
        with_failing_tasks=False,
        extension_connected=True,
        extension_holder="抓取订单 · 运行 t_1",
    )
    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._run_flow("flow-1", browser_executor="extension")

    assert result["status"] == "blocked_extension_busy"
    assert result["holder"] == "抓取订单 · 运行 t_1"
    assert "抓取订单 · 运行 t_1" in result["user_message"]
    assert "不要改流程" in result["message"]
    assert not task_manager.started

    # Playwright 执行器用的是应用自己的 profile，扩展被占不该拦它
    playwright_result = await executor._run_flow("flow-1")
    assert playwright_result.get("status") != "blocked_extension_busy"


async def test_inspect_page_stops_immediately_on_http_403(monkeypatch) -> None:
    """浏览器和静态通道都失败后才终止，避免把单通道 403 误判成站点不可达。"""
    import app.services.ai_tools.executor as executor_module

    class _Response:
        status = 403

    class _Page:
        url = "https://www.nodeseek.com/post-1"

        async def goto(self, *_args: Any, **_kwargs: Any) -> _Response:
            return _Response()

    class _Context:
        pages = [_Page()]

    @asynccontextmanager
    async def _context(*_args: Any, **_kwargs: Any):
        yield _Context()

    monkeypatch.setattr(executor_module, "find_spec", lambda _name: object())
    monkeypatch.setattr(executor_module, "persistent_browser_context", _context)
    async def blocked_static(_url: str) -> dict[str, Any]:
        return {"status": "blocked", "http_status": 403, "error": "静态抓取返回 HTTP 403"}

    monkeypatch.setattr(executor_module, "inspect_static_page", blocked_static)
    monkeypatch.setattr(executor_module.browser_profile_lock, "acquire", lambda *_args: None)
    monkeypatch.setattr(executor_module.browser_profile_lock, "release", lambda *_args: None)

    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=FakeTaskManager())  # type: ignore[arg-type]
    result = await executor._inspect_page("https://www.nodeseek.com/post-1")

    assert result["status"] == "blocked_page_access"
    assert result["http_status"] == 403
    assert result["required_action"] == "report_to_user_and_stop"
    assert "SPA" not in result["error"]
    assert [item["channel"] for item in result["access_attempts"]] == [
        "stealth_browser",
        "scrapling_static",
    ]
    assert "已依次尝试" in result["user_message"]


async def test_inspect_page_falls_back_to_static_fetch_after_browser_403(monkeypatch) -> None:
    import app.services.ai_tools.executor as executor_module

    class _Response:
        status = 403

    class _Page:
        url = "https://forum.example/post-1"

        async def goto(self, *_args: Any, **_kwargs: Any) -> _Response:
            return _Response()

    class _Context:
        pages = [_Page()]

    @asynccontextmanager
    async def _context(*_args: Any, **_kwargs: Any):
        yield _Context()

    async def successful_static(_url: str) -> dict[str, Any]:
        return {
            "status": "success",
            "inspection_source": "scrapling_static",
            "selector_candidates": [{"selector": ".reply"}],
            "recommended_node_type": "browser.fetch",
        }

    monkeypatch.setattr(executor_module, "find_spec", lambda _name: object())
    monkeypatch.setattr(executor_module, "persistent_browser_context", _context)
    monkeypatch.setattr(executor_module, "inspect_static_page", successful_static)
    monkeypatch.setattr(executor_module.browser_profile_lock, "acquire", lambda *_args: None)
    monkeypatch.setattr(executor_module.browser_profile_lock, "release", lambda *_args: None)

    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=FakeTaskManager())  # type: ignore[arg-type]
    result = await executor._inspect_page("https://forum.example/post-1")

    assert result["status"] == "success"
    assert result["inspection_source"] == "scrapling_static"
    assert result["browser_attempt"]["http_status"] == 403
    assert result["requested_url"] == "https://forum.example/post-1"


async def test_inspect_page_falls_back_to_static_fetch_on_challenge_page(monkeypatch) -> None:
    import app.services.ai_tools.executor as executor_module

    class _Response:
        status = 200

    class _Page:
        url = "https://forum.example/post-1"

        async def goto(self, *_args: Any, **_kwargs: Any) -> _Response:
            return _Response()

        async def wait_for_load_state(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        async def wait_for_timeout(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    class _Context:
        pages = [_Page()]

    @asynccontextmanager
    async def _context(*_args: Any, **_kwargs: Any):
        yield _Context()

    async def successful_static(_url: str) -> dict[str, Any]:
        return {"status": "success", "inspection_source": "scrapling_static"}

    async def challenge(_page: object) -> SimpleNamespace:
        return SimpleNamespace(label="Cloudflare 验证页", summary="需要验证")

    monkeypatch.setattr(executor_module, "find_spec", lambda _name: object())
    monkeypatch.setattr(executor_module, "persistent_browser_context", _context)
    monkeypatch.setattr(executor_module, "inspect_static_page", successful_static)
    monkeypatch.setattr(executor_module, "detect_blocking_interstitial", challenge)
    monkeypatch.setattr(executor_module.browser_profile_lock, "acquire", lambda *_args: None)
    monkeypatch.setattr(executor_module.browser_profile_lock, "release", lambda *_args: None)

    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=FakeTaskManager())  # type: ignore[arg-type]
    result = await executor._inspect_page("https://forum.example/post-1")

    assert result["status"] == "success"
    assert result["browser_attempt"]["status"] == "blocked_challenge_page"
    assert result["browser_attempt"]["challenge_label"] == "Cloudflare 验证页"


async def test_get_run_error_returns_root_cause_hints_for_login_detection_failure() -> None:
    task_manager = FakeTaskManager()
    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._get_run_error("task-0")

    assert result["failed_node_id"] == "n13"
    assert result["root_cause_hints"][0]["type"] == "login_detection_may_have_skipped_login"


def test_lint_flow_blocks_login_without_a_submit_or_navigation() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {"id": "open", "type": "browser.open", "targetUrl": "https://example.com/"},
        {"id": "user", "type": "browser.fill", "selector": "input[placeholder='用户名']", "inputValue": "${var.username}"},
        {"id": "pwd", "type": "browser.fill", "selector": "input[type='password']", "inputValue": "${var.password}"},
        {"id": "wait_table", "type": "browser.wait", "selector": "table"},
        {"id": "extract", "type": "browser.extract", "selector": ".orders tbody tr", "extractMode": "table", "outputVariable": "rows"},
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "open"},
        {"source": "open", "target": "user"},
        {"source": "user", "target": "pwd"},
        {"source": "pwd", "target": "wait_table"},
        {"source": "wait_table", "target": "extract"},
        {"source": "extract", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=["username", "password"])
    single_nav = next(finding for finding in findings if finding["issue"] == "login_without_navigation_to_data_page")

    assert single_nav["severity"] == "error"


def test_lint_flow_blocks_clear_storage_when_login_persistence_is_expected() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {"id": "open", "type": "browser.open", "targetUrl": "https://example.com/", "clearStorage": True},
        {"id": "detect", "type": "browser.extract", "title": "检测登录表单", "selector": "input[type='password']", "extractMode": "count", "countVariable": "login_count"},
        {"id": "cond", "type": "control.condition", "inputValue": "login_count > 0"},
        {"id": "pwd", "type": "browser.fill", "selector": "input[type='password']", "inputValue": "${var.password}"},
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "open"},
        {"source": "open", "target": "detect"},
        {"source": "detect", "target": "cond"},
        {"source": "cond", "target": "pwd", "label": "true"},
        {"source": "pwd", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=["password"])
    clear_storage = next(finding for finding in findings if finding["issue"] == "clear_storage_breaks_login_persistence")

    assert clear_storage["severity"] == "error"


def test_lint_flow_enforces_single_variable_contract() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {"id": "set", "type": "variable.set", "variableName": "${var.login_count}", "value": "0"},
        {"id": "detect", "type": "browser.extract", "selector": "input[type='password']", "extractMode": "count", "countVariable": "${var.login_count}"},
        {"id": "cond", "type": "control.condition", "inputValue": "${var.login_count} > 0"},
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "set"},
        {"source": "set", "target": "detect"},
        {"source": "detect", "target": "cond"},
        {"source": "cond", "target": "end", "label": "true"},
        {"source": "cond", "target": "end", "label": "false"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=[])
    issues = {finding["issue"] for finding in findings}

    assert "variable_name_field_uses_template" in issues
    assert "condition_expression_uses_template" in issues


def test_lint_flow_accepts_canonical_variable_contract() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {"id": "set", "type": "variable.set", "variableName": "login_count", "value": "0"},
        {"id": "detect", "type": "browser.extract", "selector": "input[type='password']", "extractMode": "count", "countVariable": "login_count"},
        {"id": "cond", "type": "control.condition", "inputValue": "login_count > 0"},
        {"id": "fill", "type": "browser.fill", "selector": "input", "inputValue": "${var.username}"},
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "set"},
        {"source": "set", "target": "detect"},
        {"source": "detect", "target": "cond"},
        {"source": "cond", "target": "fill", "label": "true"},
        {"source": "cond", "target": "end", "label": "false"},
        {"source": "fill", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=["password"])
    issues = {finding["issue"] for finding in findings}

    assert "variable_name_field_uses_template" not in issues
    assert "condition_expression_uses_template" not in issues


def test_lint_flow_requires_first_value_when_script_consumes_text_extract() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {
            "id": "extract",
            "type": "browser.extract",
            "selector": "#Main",
            "extractMode": "text",
            "outputVariable": "topic_text",
        },
        {
            "id": "script",
            "type": "script.python",
            "code": "text = _vars.get('topic_text', '') or ''\nprint(text.splitlines()[0])",
        },
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "extract"},
        {"source": "extract", "target": "script"},
        {"source": "script", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=[])
    scalar_contract = next(
        finding for finding in findings
        if finding["issue"] == "extract_scalar_output_consumed_by_script_without_first_value"
    )

    assert scalar_contract["severity"] == "error"
    assert scalar_contract["output_variable"] == "topic_text"
    assert scalar_contract["downstream_script_ids"] == ["script"]


def test_lint_flow_accepts_first_value_variable_for_script_text_extract() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {
            "id": "extract",
            "type": "browser.extract",
            "selector": "#Main",
            "extractMode": "text",
            "outputVariable": "topic_texts",
            "firstValueVariable": "topic_text",
        },
        {
            "id": "script",
            "type": "script.python",
            "code": "text = _vars.get('topic_text', '') or ''\nprint(text.splitlines()[0])",
        },
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "extract"},
        {"source": "extract", "target": "script"},
        {"source": "script", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=[])
    issues = {finding["issue"] for finding in findings}

    assert "extract_scalar_output_consumed_by_script_without_first_value" not in issues
    assert "undefined_variable_ref" not in issues


def test_lint_flow_accepts_script_that_normalizes_extract_list() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {
            "id": "extract",
            "type": "browser.extract",
            "selector": "#Main",
            "extractMode": "text",
            "outputVariable": "topic_text",
        },
        {
            "id": "script",
            "type": "script.python",
            "code": (
                "raw = _vars.get('topic_text', '')\n"
                "text = '\\n'.join(str(item) for item in raw) if isinstance(raw, list) else str(raw or '')\n"
                "print(text.splitlines()[0] if text else '')"
            ),
        },
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "extract"},
        {"source": "extract", "target": "script"},
        {"source": "script", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=[])
    issues = {finding["issue"] for finding in findings}

    assert "extract_scalar_output_consumed_by_script_without_first_value" not in issues


def test_lint_flow_rejects_playwright_text_selector_in_css_field() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {
            "id": "click_workspace",
            "type": "browser.click",
            "title": "点击工作台",
            "selector": "text=工作台",
        },
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "click_workspace"},
        {"source": "click_workspace", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=[])
    selector_issue = next(
        finding for finding in findings
        if finding["issue"] == "unsupported_selector_syntax"
    )

    assert selector_issue["severity"] == "error"
    assert selector_issue["node_id"] == "click_workspace"
    assert "text=" in selector_issue["message"]


def test_lint_flow_warns_when_script_http_fetch_replaces_browser_flow() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {
            "id": "fetch_page",
            "type": "script.python",
            "title": "脚本抓取页面",
            "code": "import urllib.request\nprint(urllib.request.urlopen('https://example.com').read())",
            "outputVariable": "page_text",
        },
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "fetch_page"},
        {"source": "fetch_page", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=[])
    drift = next(
        finding for finding in findings
        if finding["issue"] == "script_http_fetch_without_browser_flow"
    )

    assert drift["severity"] == "warn"
    assert drift["node_id"] == "fetch_page"
    assert "urllib.request" in drift["message"]


def test_lint_flow_warns_when_shell_script_replaces_browser_flow_with_curl() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {
            "id": "fetch_page",
            "type": "script.shell",
            "title": "脚本抓取页面",
            "code": "curl -s https://example.com > page.html",
            "outputVariable": "page_text",
        },
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "fetch_page"},
        {"source": "fetch_page", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=[])
    drift = next(
        finding for finding in findings
        if finding["issue"] == "script_http_fetch_without_browser_flow"
    )

    assert drift["severity"] == "warn"
    assert drift["node_id"] == "fetch_page"
    assert "curl" in drift["message"]


def test_lint_flow_warns_when_curl_has_no_trailing_space_before_marker() -> None:
    """`curl` inside a list literal (subprocess.run(['curl', url])) has no trailing
    space/paren after it, so the old substring-only marker list ("curl " with a
    trailing space) missed it entirely. The word-boundary marker pattern must catch
    this bare-word form too."""
    nodes = [
        {"id": "start", "type": "start"},
        {
            "id": "fetch_page",
            "type": "script.python",
            "title": "脚本抓取页面",
            "code": "import subprocess\nsubprocess.run(['curl', url])",
            "outputVariable": "page_text",
        },
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "fetch_page"},
        {"source": "fetch_page", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=[])
    drift = next(
        finding for finding in findings
        if finding["issue"] == "script_http_fetch_without_browser_flow"
    )

    assert drift["severity"] == "warn"
    assert drift["node_id"] == "fetch_page"


def test_lint_flow_warns_on_decorative_variable_parsing_and_hardcoded_prose() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {
            "id": "summarize",
            "type": "script.python",
            "title": "生成总结",
            "code": (
                "import json, os\n"
                "_vars = json.loads(os.environ.get('RPA_VARIABLES_JSON', '{}'))\n"
                "rows = [{'section': '一句话总结', "
                "'content': '该帖围绕一段投入较多的亲密关系展开，讨论集中在尊重感与现实条件差异。'}]\n"
                "print(json.dumps(rows, ensure_ascii=False))\n"
            ),
            "outputVariable": "summary_rows",
        },
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "summarize"},
        {"source": "summarize", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=[])
    issues = {finding["issue"] for finding in findings if finding.get("node_id") == "summarize"}

    assert "script_decorative_variable_parsing" in issues
    assert "script_hardcoded_prose_literal" in issues


def test_lint_flow_allows_script_using_parsed_variable_and_interpolated_text() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {
            "id": "summarize",
            "type": "script.python",
            "title": "生成总结",
            "code": (
                "import json, os\n"
                "_vars = json.loads(os.environ.get('RPA_VARIABLES_JSON', '{}'))\n"
                "topic = _vars.get('topic_text', '')\n"
                "summary = f'本次抓取到的正文长度为 {len(topic)} 字符。'\n"
                "print(summary)\n"
            ),
            "outputVariable": "summary_text",
        },
        {"id": "end", "type": "end"},
    ]
    edges = [
        {"source": "start", "target": "summarize"},
        {"source": "summarize", "target": "end"},
    ]

    findings = _lint_flow(nodes, edges, input_variable_names=[])
    issues = {finding["issue"] for finding in findings if finding.get("node_id") == "summarize"}

    assert "script_decorative_variable_parsing" not in issues
    assert "script_hardcoded_prose_literal" not in issues


async def test_get_run_error_returns_selector_diagnostic_for_zero_match() -> None:
    class SelectorTaskManager(FakeTaskManager):
        def __init__(self) -> None:
            super().__init__()
            self.tasks[0].error = (
                "Page.click: Timeout 30000ms exceeded. "
                "[selector '.menu:has-text(\"Reports\")' 页面匹配 0 个元素]"
            )

        async def get_logs(self, task_id: str):
            return [
                TaskLogEntry(
                    taskId=task_id,
                    level="error",
                    message="任务失败",
                    detail=self.tasks[0].error or "",
                    nodeId="n13",
                )
            ]

    task_manager = SelectorTaskManager()
    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._get_run_error("task-0")

    assert result["selector_diagnostic"]["kind"] == "selector_zero_match"
    assert result["selector_diagnostic"]["matched_count"] == 0


async def test_failure_page_url_takes_precedence_over_earlier_navigation(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    manager = FakeTaskManager()
    manager.tasks[0].error = (
        "Locator.wait_for: Timeout 30000ms exceeded. locator('#rows') "
        "[失败时页面: https://example.com/login]"
    )
    monkeypatch.setattr(manager, "get_logs", AsyncMock(return_value=[TaskLogEntry(
        taskId="task-0", level="info", message="打开页面", detail="https://example.com/data", nodeId="open",
    )]))
    result = await RpaToolExecutor(FakeFlowService(), manager)._get_run_error("task-0")
    assert result["last_browser_url"] == "https://example.com/login"
    assert "https://example.com/login" in result["inspect_hint"]


def test_repeated_navigation_failures_end_in_asking_the_user_for_the_target_url() -> None:
    """点不动同一个导航节点两次，出路不是第三次盲改 selector，而是向用户要目标 URL。"""
    state = _ready_state()
    result = {
        "inspect_hint": "selector timeout",
        "last_browser_url": "https://example.com/#/index",
        "failed_node_id": "nav_menu",
        "failed_node_config": {
            "id": "nav_menu",
            "type": "browser.click",
            "title": "打开报表入口",
            "selector": ".menu:has-text('Reports')",
        },
        "selector_diagnostic": {"kind": "selector_zero_match", "matched_count": 0},
    }
    failure = {"status": "error", "error": "click timeout 30000ms"}

    _orchestrator_guard_after_tool("run_flow", failure, state)
    _orchestrator_guard_after_tool("get_run_error", result, state)
    assert state.navigation_failure_hint["node_id"] == "nav_menu"
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is not None  # requires inspect_page first
    _orchestrator_guard_after_tool("inspect_page", {"url": "https://example.com/#/index"}, state)
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is None

    # 同一条错误再来一次按两份算，额度到此为止
    _orchestrator_guard_after_tool("run_flow", failure, state)
    _orchestrator_guard_after_tool("get_run_error", result, state)
    blocked = _orchestrator_guard_before_tool("run_flow", {}, state)

    assert blocked is not None
    assert blocked["required_action"] == "needs_user_navigation_target"
    # 出路必须具体到「要什么」，只说「我卡住了」用户无从配合
    assert any("URL" in item for item in blocked["needed_from_user"])
    assert blocked["user_message"]


def test_runtime_escape_finding_survives_a_clean_static_scan() -> None:
    """静态扫描漏掉的未定义变量，不能被下一轮「静态检查通过」的状态块冲掉。

    阻断集每轮由状态块重算，所以运行期逃逸只能单独记账再并进来；如果跟静态诊断
    共用一个键，模型被拦下后什么都不改、下一轮重算就自动放行了。
    """
    from app.services.ai_flow_state import FlowState
    from app.services.ai_orchestrator import _blocking_diagnostics

    state = _ready_state(runtime_escape_findings=[])
    _orchestrator_guard_after_tool(
        "run_flow", {"status": "error", "error": "变量未定义: order_no"}, state
    )

    # 状态块这一轮报「静态检查通过」，逃逸项仍然要挡住运行
    clean = FlowState(flow_id="f1", findings=[])
    state.blocking_diagnostics = _blocking_diagnostics(clean, state)
    blocked = _orchestrator_guard_before_tool("run_flow", {}, state)
    assert blocked is not None
    assert blocked["required_action"] == "fix_blocking_diagnostics_first"
    assert any(
        f.get("issue") == "undefined_variable_ref_runtime_escape"
        for f in blocked["lint_findings"]
    )

    # 真实的结构性修复才允许解锁
    _orchestrator_guard_after_tool("update_flow", {"status": "updated"}, state)
    state.blocking_diagnostics = _blocking_diagnostics(clean, state)
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is None


def test_explicit_channel_switch_requires_verb_and_target_not_just_a_substring() -> None:
    """A message like "用 python 处理一下数据" only wants a cleanup script, not an
    execution-channel switch — the old substring-only keyword list ("用 python")
    would have wrongly disabled the guard for it. Only messages that pair a
    switch verb (改用/换成/不用...) with a channel target (脚本/python/http/api/...)
    should opt out."""
    assert _is_explicit_channel_switch_request("用 python 处理一下提取到的数据") is False
    assert _is_explicit_channel_switch_request("用python写个清洗脚本处理结果") is False
    assert _is_explicit_channel_switch_request("抓不全，帮我修一下") is False

    assert _is_explicit_channel_switch_request("不用浏览器了，改用 python 脚本直接抓") is True
    assert _is_explicit_channel_switch_request("改用脚本方案") is True
    assert _is_explicit_channel_switch_request("换成 api 请求") is True
    assert _is_explicit_channel_switch_request("直接用 requests 抓这个页面吧") is True


class FakeRenamableFlowService:
    """Fake FlowService whose flow name starts as a placeholder and tracks update_flow calls."""

    def __init__(self, initial_name: str) -> None:
        now = datetime.now(UTC)
        self.flow = FlowSnapshot(
            flowId="flow-rename-1",
            name=initial_name,
            version="v1.0.0",
            status="active",
            inputVariables=[],
            definition={
                "nodes": [{"id": "start", "type": "start"}, {"id": "end", "type": "end"}],
                "edges": [{"source": "start", "target": "end"}],
            },
            createdAt=now,
            updatedAt=now,
        )

    async def get_flow(self, flow_id: str) -> FlowSnapshot:
        return self.flow

    async def update_flow(self, flow_id: str, request) -> FlowSnapshot:  # noqa: ANN001
        if request.name is not None:
            self.flow = self.flow.model_copy(update={"name": request.name})
        if request.definition is not None:
            self.flow = self.flow.model_copy(update={"definition": request.definition})
        return self.flow


async def test_update_flow_renames_placeholder_flow_when_ai_supplies_name() -> None:
    flow_service = FakeRenamableFlowService(initial_name="新建 RPA 流程")
    executor = RpaToolExecutor(flow_service=flow_service, task_manager=FakeTaskManager())  # type: ignore[arg-type]

    result = await executor.execute("update_flow", {"flow_id": "flow-rename-1", "name": "抖店登录流程"})

    assert result["flow_name"] == "抖店登录流程"
    assert flow_service.flow.name == "抖店登录流程"


async def test_update_flow_does_not_overwrite_an_already_meaningful_name() -> None:
    flow_service = FakeRenamableFlowService(initial_name="抖店登录流程")
    executor = RpaToolExecutor(flow_service=flow_service, task_manager=FakeTaskManager())  # type: ignore[arg-type]

    result = await executor.execute("update_flow", {"flow_id": "flow-rename-1", "name": "别的名字"})

    assert result["flow_name"] == "抖店登录流程"
    assert flow_service.flow.name == "抖店登录流程"


async def test_apply_node_fix_refuses_structural_fields() -> None:
    """id/type 不在这个工具的权限内。

    放开它，apply_node_fix 就成了「不过结构校验的 update_flow」：换 type 会让
    连线指向一个不存在的能力，改 id 直接把节点从 edges 上摘掉，而这条路上
    _validate_update_structure、start/end 保护、孤儿检查一条都不执行。
    """
    executor = RpaToolExecutor(  # type: ignore[arg-type]
        flow_service=FakeRenamableFlowService(initial_name="新建 RPA 流程"),
        task_manager=FakeTaskManager(),
    )

    for patch, expected in (
        ({"type": "browser.click"}, ["type"]),
        ({"id": "other"}, ["id"]),
        ({"id": "other", "type": "browser.click", "selector": "td"}, ["id", "type"]),
    ):
        result = await executor.execute("apply_node_fix", {
            "flow_id": "flow-rename-1", "node_id": "start", "config_patch": patch,
        })
        assert result["status"] == "blocked_structural_patch"
        assert result["rejected_fields"] == expected
        assert result["error"]


# ─── Schedule & task-control tools ─────────────────────────────────────────────

def _make_flow_snapshot(
    *,
    nodes: list[dict] | None = None,
    input_variables: list[dict] | None = None,
    default_browser_executor: str = "playwright",
) -> FlowSnapshot:
    now = datetime.now(UTC)
    return FlowSnapshot(
        flowId="flow-sched-1",
        name="定时抓取流程",
        version="v1.0.0",
        status="active",
        inputVariables=input_variables or [],
        defaultBrowserExecutor=default_browser_executor,
        definition={
            "nodes": nodes
            or [
                {"id": "start", "type": "start"},
                {"id": "n1", "type": "browser.open", "targetUrl": "https://example.com"},
            ],
            "edges": [{"source": "start", "target": "n1"}],
        },
        createdAt=now,
        updatedAt=now,
    )


class FakeScheduleFlowService:
    def __init__(self, flow: FlowSnapshot | None) -> None:
        self.flow = flow

    async def get_flow(self, flow_id: str) -> FlowSnapshot | None:
        return self.flow


class FakeScheduleService:
    def __init__(self) -> None:
        from app.models.schemas import ScheduleSnapshot

        self.created_request = None
        self.updated: tuple[str, object] | None = None
        now = datetime.now(UTC)
        self._snapshot_cls = ScheduleSnapshot
        self._now = now

    def _snapshot(self, *, name: str, cron: str, task, status: str = "enabled"):
        return self._snapshot_cls(
            scheduleId="sched-1",
            name=name,
            cronExpression=cron,
            timezone="Asia/Shanghai",
            status=status,
            task=task,
            createdAt=self._now,
            updatedAt=self._now,
            nextRunAt=self._now,
        )

    async def create_schedule(self, request):
        self.created_request = request
        return self._snapshot(name=request.name, cron=request.cron_expression, task=request.task)

    async def list_schedules(self):
        from app.models.schemas import RunTaskRequest

        return [self._snapshot(name="已有任务", cron="0 9 * * *", task=RunTaskRequest(flow_id="flow-sched-1", flow_name="定时抓取流程"))]

    async def update_schedule(self, schedule_id: str, request):
        from app.models.schemas import RunTaskRequest

        self.updated = (schedule_id, request)
        if schedule_id != "sched-1":
            return None
        return self._snapshot(
            name="已有任务",
            cron="0 9 * * *",
            task=RunTaskRequest(flow_id="flow-sched-1", flow_name="定时抓取流程"),
            status="enabled" if request.enabled else "disabled",
        )


async def test_create_schedule_rejects_flow_with_pause_nodes() -> None:
    flow = _make_flow_snapshot(
        nodes=[
            {"id": "start", "type": "start"},
            {"id": "n_input", "type": "variable.input", "variableName": "captcha", "title": "输入验证码"},
        ]
    )
    executor = RpaToolExecutor(
        flow_service=FakeScheduleFlowService(flow),  # type: ignore[arg-type]
        task_manager=FakeTaskManager(with_failing_tasks=False),  # type: ignore[arg-type]
        schedule_service=FakeScheduleService(),  # type: ignore[arg-type]
    )
    result = await executor.execute("create_schedule", {"flow_id": "flow-sched-1", "cron_expression": "0 9 * * *"})
    assert "不适合定时无人值守运行" in result["error"]
    assert result["pause_nodes"][0]["id"] == "n_input"


async def test_create_schedule_rejects_missing_input_variable_defaults() -> None:
    flow = _make_flow_snapshot(
        input_variables=[
            {"name": "username", "type": "String", "value": "", "category": "credential"},
        ]
    )
    executor = RpaToolExecutor(
        flow_service=FakeScheduleFlowService(flow),  # type: ignore[arg-type]
        task_manager=FakeTaskManager(with_failing_tasks=False),  # type: ignore[arg-type]
        schedule_service=FakeScheduleService(),  # type: ignore[arg-type]
    )
    result = await executor.execute("create_schedule", {"flow_id": "flow-sched-1", "cron_expression": "0 9 * * *"})
    assert "无默认值" in result["error"]
    assert result["missing_variables"][0]["name"] == "username"


async def test_create_schedule_uses_flow_default_executor_and_returns_snapshot() -> None:
    flow = _make_flow_snapshot(default_browser_executor="extension")
    schedule_service = FakeScheduleService()
    executor = RpaToolExecutor(
        flow_service=FakeScheduleFlowService(flow),  # type: ignore[arg-type]
        task_manager=FakeTaskManager(with_failing_tasks=False),  # type: ignore[arg-type]
        schedule_service=schedule_service,  # type: ignore[arg-type]
    )
    result = await executor.execute("create_schedule", {"flow_id": "flow-sched-1", "cron_expression": "0 9 * * *"})
    assert result["schedule_id"] == "sched-1"
    assert schedule_service.created_request.task.browser_executor == "extension"
    assert "warning" in result  # extension 模式必须携带无人值守告警


async def test_create_schedule_rejects_invalid_cron_expression() -> None:
    executor = RpaToolExecutor(
        flow_service=FakeScheduleFlowService(_make_flow_snapshot()),  # type: ignore[arg-type]
        task_manager=FakeTaskManager(with_failing_tasks=False),  # type: ignore[arg-type]
        schedule_service=FakeScheduleService(),  # type: ignore[arg-type]
    )
    result = await executor.execute("create_schedule", {"flow_id": "flow-sched-1", "cron_expression": "9:00 每天运行"})
    assert "定时任务参数无效" in result["error"]


async def test_toggle_and_list_schedules() -> None:
    schedule_service = FakeScheduleService()
    executor = RpaToolExecutor(
        flow_service=FakeScheduleFlowService(_make_flow_snapshot()),  # type: ignore[arg-type]
        task_manager=FakeTaskManager(with_failing_tasks=False),  # type: ignore[arg-type]
        schedule_service=schedule_service,  # type: ignore[arg-type]
    )
    listed = await executor.execute("list_schedules", {})
    assert listed["count"] == 1
    assert listed["schedules"][0]["flow_id"] == "flow-sched-1"

    toggled = await executor.execute("toggle_schedule", {"schedule_id": "sched-1", "enabled": False})
    assert toggled["status"] == "disabled"
    missing = await executor.execute("toggle_schedule", {"schedule_id": "nope", "enabled": True})
    assert "不存在" in missing["error"]


async def test_schedule_tools_report_unavailable_without_service() -> None:
    executor = RpaToolExecutor(
        flow_service=FakeScheduleFlowService(_make_flow_snapshot()),  # type: ignore[arg-type]
        task_manager=FakeTaskManager(with_failing_tasks=False),  # type: ignore[arg-type]
    )
    for call in (
        ("list_schedules", {}),
        ("create_schedule", {"flow_id": "flow-sched-1", "cron_expression": "0 9 * * *"}),
        ("toggle_schedule", {"schedule_id": "sched-1", "enabled": True}),
    ):
        result = await executor.execute(*call)
        assert result["error"] == "定时任务服务不可用"


async def test_stop_run_stops_running_task() -> None:
    class StoppableTaskManager(FakeTaskManager):
        def __init__(self) -> None:
            super().__init__(with_failing_tasks=False)
            self.stopped_task_id: str | None = None

        async def stop_task(self, task_id: str):
            if task_id == "missing":
                return None
            self.stopped_task_id = task_id
            now = datetime.now(UTC)
            return TaskSnapshot(
                taskId=task_id,
                flowId="flow-sched-1",
                flowName="定时抓取流程",
                mode="run",
                status="stopped",
                progress=RuntimeProgress(currentStep=1, totalSteps=2, percent=50, elapsedMs=1000),
                createdAt=now,
                updatedAt=now,
            )

    task_manager = StoppableTaskManager()
    executor = RpaToolExecutor(
        flow_service=FakeScheduleFlowService(_make_flow_snapshot()),  # type: ignore[arg-type]
        task_manager=task_manager,  # type: ignore[arg-type]
    )
    result = await executor.execute("stop_run", {"task_id": "task-9"})
    assert result["status"] == "stopped"
    assert task_manager.stopped_task_id == "task-9"

    missing = await executor.execute("stop_run", {"task_id": "missing"})
    assert "不存在" in missing["error"]


def test_lint_flow_reports_critical_continue_on_error_without_crashing() -> None:
    """回归：_collect_downstream_nodes 曾被同名函数覆盖，导致这条规则一触发就抛
    TypeError，并连带中断 _lint_flow 后续所有规则。"""
    nodes = [
        {"id": "n_submit", "type": "browser.click", "title": "提交筛选条件",
         "selector": "#submit", "continueOnError": True,
         "position": {"x": 0, "y": 0}},
        {"id": "n_extract", "type": "browser.extract", "title": "抓取结果表格",
         "selector": "tbody tr", "outputVariable": "rows",
         "position": {"x": 0, "y": 200}},
    ]
    edges = [{"id": "e1", "source": "n_submit", "target": "n_extract"}]

    findings = _lint_flow(nodes, edges)

    hit = next(f for f in findings if f["issue"] == "critical_action_continue_on_error")
    assert hit["node_id"] == "n_submit"
    assert "n_extract" in hit["downstream_node_ids"]


def test_lint_flow_still_runs_rules_declared_after_continue_on_error_check() -> None:
    """规则顺序回归：确认 _lint_critical_continue_on_error 之后注册的规则仍会执行。"""
    nodes = [
        {"id": "n_click", "type": "browser.click", "title": "点击提交",
         "selector": "#go", "continueOnError": True, "position": {"x": 0, "y": 0}},
        {"id": "n_wait", "type": "browser.wait", "title": "等待结果",
         "selector": ".result", "position": {"x": 0, "y": 200}},
        {"id": "n_orphan", "type": "browser.extract", "title": "孤儿节点",
         "selector": ".x", "outputVariable": "x", "position": {"x": 900, "y": 0}},
    ]
    edges = [{"id": "e1", "source": "n_click", "target": "n_wait"}]

    findings = _lint_flow(nodes, edges)

    assert any(f["issue"] == "critical_action_continue_on_error" for f in findings)
    # unreachable_node 在该规则之前、_lint_visual_layout 等在其之后，两侧都要在
    assert any(f["issue"] == "unreachable_node" for f in findings)


def test_lint_flags_table_mode_selector_that_is_not_table_like() -> None:
    """回归：extractMode='table' 却指向指标卡片容器。

    旧版被两道门跳过——_TABLE_HINT_KEYWORDS 要求 selector/title 带表格字样，
    _is_table_container_token 又要求 class 字面含 'table'。
    """
    nodes = [
        {"id": "n_extract", "type": "browser.extract", "title": "抽取核心业务指标",
         "selector": ".workbench-page, .stats-section, .stats-grid, .stats-card",
         "extractMode": "table", "outputVariable": "metrics", "position": {"x": 0, "y": 0}},
    ]

    findings = _lint_flow(nodes, [])

    hit = next(f for f in findings if f["issue"] == "table_extract_selector_not_table_like")
    # warn 且不阻断运行：判据是 selector 文本猜测，而圈错范围执行一次就有定论
    assert hit["severity"] == "warn"
    assert hit["node_id"] == "n_extract"


def test_lint_does_not_flag_row_level_table_selector() -> None:
    nodes = [
        {"id": "n_extract", "type": "browser.extract", "title": "抽取订单表",
         "selector": ".order-list tbody tr", "extractMode": "table",
         "outputVariable": "orders", "countVariable": "order_count", "position": {"x": 0, "y": 0}},
    ]

    findings = _lint_flow(nodes, [])

    assert not any(f["issue"] == "table_extract_selector_not_table_like" for f in findings)


def test_lint_reports_id_named_table_as_container_not_as_unknown_structure() -> None:
    """`#bill-table` 指的就是 <table id="bill-table">，两条 error 的分流必须落在容器那条。

    两条都是提示级，差别在 fix 文案：容器那条直说「改成行选择器」，
    而 not_table_like 那条会让模型怀疑目标压根不是表格、改用 extractMode='text'。
    判据只认 class 不认 id 时，最常见的 id 命名会被指去这条错的岔路。
    真实后果见 evals：模型为此连开 4 次 apply_node_fix、3 次 inspect_page 仍没修好。
    """
    nodes = [
        {"id": "n_extract", "type": "browser.extract", "title": "提取筛选后表格",
         "selector": "#bill-table", "extractMode": "table",
         "outputVariable": "rows", "countVariable": "rows_count", "position": {"x": 0, "y": 0}},
    ]

    issues = {f["issue"] for f in _lint_flow(nodes, [])}

    assert "table_extract_selector_targets_container" in issues
    assert "table_extract_selector_not_table_like" not in issues


def test_lint_keeps_id_named_row_selector_out_of_the_container_branch() -> None:
    """id 里含 table 但收窄到了行，就不该再报容器：`#bill-table tbody tr` 抓的是行。"""
    nodes = [
        {"id": "n_extract", "type": "browser.extract", "title": "提取筛选后表格",
         "selector": "#bill-table tbody tr", "extractMode": "table",
         "outputVariable": "rows", "countVariable": "rows_count", "position": {"x": 0, "y": 0}},
    ]

    issues = {f["issue"] for f in _lint_flow(nodes, [])}

    assert "table_extract_selector_targets_container" not in issues
    assert "table_extract_selector_not_table_like" not in issues


def test_lint_flags_forked_path_whose_downstream_is_swallowed() -> None:
    """复现 grok-4.5 生成的拓扑：校验腿在分叉另一侧，又汇合回抽取节点。

    n4 有两条出边 n5/n6，n6→n7→n8 后连回 n5。单条 DFS 下 n5 先出栈先执行，
    n8 执行时 n5 已在 visited 里，`e9` 被丢——日期校验这条腿一次都不跑。
    两个节点从起点都可达，所以 unreachable_node 看不见它。
    """

    def node(nid: str, ntype: str, title: str) -> dict:
        return {"id": nid, "type": ntype, "title": title,
                "selector": "#x", "position": {"x": 0, "y": 0}}

    nodes = [
        node("n4", "browser.press", "按Enter提交"),
        node("n5", "browser.extract", "提取筛选后表格"),
        node("n6", "browser.extract", "回读开始日期值"),
        node("n7", "browser.extract", "回读结束日期值"),
        node("n8", "script.python", "校验日期范围"),
    ]
    nodes[4]["outputVariable"] = "rows"
    edges = [
        {"id": "e5", "source": "n4", "target": "n5"},
        {"id": "e6", "source": "n4", "target": "n6"},
        {"id": "e7", "source": "n6", "target": "n7"},
        {"id": "e8", "source": "n7", "target": "n8"},
        {"id": "e9", "source": "n8", "target": "n5"},
    ]

    findings = _lint_flow(nodes, edges)

    hit = next(f for f in findings if f["issue"] == "forked_path_downstream_swallowed")
    assert hit["severity"] == "warn"
    assert hit["node_id"] == "n4"
    # 报告里必须点名是哪条腿被吞，否则模型只知道"有分叉"、不知道该改哪条边
    assert "n5" in hit["message"] and "n6" in hit["message"]


def test_lint_keeps_condition_and_loop_fanout_out_of_the_swallowed_fork_rule() -> None:
    """条件/循环的分叉由 label 决定走哪条，两条腿本来就不都执行，不能按吞并报。"""
    cond = {"id": "c1", "type": "control.condition", "title": "有数据吗",
            "expression": "${var.n} > 0", "position": {"x": 0, "y": 0}}
    loop = {"id": "l1", "type": "control.foreach", "title": "逐行",
            "listVariable": "rows", "itemVariable": "row", "position": {"x": 0, "y": 0}}
    leaf_a = {"id": "a1", "type": "browser.click", "title": "A", "selector": "#a", "position": {"x": 0, "y": 0}}
    leaf_b = {"id": "a2", "type": "browser.click", "title": "B", "selector": "#b", "position": {"x": 0, "y": 0}}
    leaf_c = {"id": "a3", "type": "browser.click", "title": "C", "selector": "#c", "position": {"x": 0, "y": 0}}

    edges = [
        {"source": "c1", "target": "a1", "label": "true"},
        {"source": "c1", "target": "a2", "label": "false"},
        {"source": "l1", "target": "a3", "label": "body"},
        {"source": "l1", "target": "a1", "label": "exit"},
        {"source": "a3", "target": "a1"},
    ]

    issues = {f["issue"] for f in _lint_flow([cond, loop, leaf_a, leaf_b, leaf_c], edges)}

    assert "forked_path_downstream_swallowed" not in issues


def test_lint_does_not_flag_plain_linear_chain() -> None:
    """串联的正常流程不能因为规则新增就报分叉。"""
    nodes = [
        {"id": f"n{i}", "type": "browser.click", "title": f"N{i}",
         "selector": "#x", "position": {"x": 0, "y": 0}} for i in range(1, 4)
    ]
    edges = [{"source": "n1", "target": "n2"}, {"source": "n2", "target": "n3"}]

    assert not any(
        f["issue"] == "forked_path_downstream_swallowed" for f in _lint_flow(nodes, edges)
    )


async def test_update_flow_drops_leftover_start_to_end_skeleton_edge() -> None:
    """空流程自带的 start→end 边在接上真实链路后必须消失。"""
    flow_service = FakeRenamableFlowService(initial_name="新建 RPA 流程")
    executor = RpaToolExecutor(flow_service=flow_service, task_manager=FakeTaskManager())  # type: ignore[arg-type]

    await executor.execute("update_flow", {
        "flow_id": "flow-rename-1",
        "add_nodes": [
            {"id": "n1", "type": "browser.open", "title": "打开页面", "url": "https://example.com"},
            {"id": "n2", "type": "browser.extract", "title": "抽取", "selector": ".t tbody tr",
             "extractMode": "table", "outputVariable": "rows"},
        ],
        "add_edges": [
            {"source": "start", "target": "n1"},
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "end"},
        ],
    })

    edges = flow_service.flow.definition["edges"]
    pairs = {(e["source"], e["target"]) for e in edges}
    assert ("start", "end") not in pairs
    assert ("start", "n1") in pairs


async def test_update_flow_keeps_start_to_end_edge_when_it_is_the_only_path() -> None:
    """还没接上真实节点时不能把骨架边删掉，否则流程直接断开。"""
    flow_service = FakeRenamableFlowService(initial_name="新建 RPA 流程")
    executor = RpaToolExecutor(flow_service=flow_service, task_manager=FakeTaskManager())  # type: ignore[arg-type]

    await executor.execute("update_flow", {"flow_id": "flow-rename-1", "name": "占位重命名"})

    pairs = {(e["source"], e["target"]) for e in flow_service.flow.definition["edges"]}
    assert ("start", "end") in pairs


def test_check_structured_rows_flags_header_echoed_as_data_row() -> None:
    """表头被当成数据行抽下来：每个字段的值和它的列名一模一样。"""
    issue = _check_structured_rows([
        {"品牌": "品牌", "融资金额": "融资金额", "轮次": "轮次"},
        {"品牌": "甲公司", "融资金额": "1000万", "轮次": "A轮"},
    ])

    assert issue is not None
    assert issue["issue"] == "header_row_as_data"


def test_check_structured_rows_flags_mostly_empty_rows() -> None:
    """大量近乎空行 = selector 圈进了非数据行。"""
    issue = _check_structured_rows([
        {"品牌": "甲公司", "融资金额": "1000万", "轮次": "A轮"},
        {"品牌": "乙公司", "融资金额": "2000万", "轮次": "B轮"},
        {"品牌": "审批", "融资金额": "", "轮次": ""},
        {"品牌": "操作", "融资金额": "", "轮次": ""},
        {"品牌": "", "融资金额": "", "轮次": "查看"},
    ])

    assert issue is not None
    assert issue["issue"] == "sparse_rows"


def test_check_structured_rows_allows_a_single_summary_row() -> None:
    """单条合计行不应触发 sparse_rows。"""
    rows = [{"项目": f"项目{i}", "金额": f"{i}00", "备注": "正常"} for i in range(9)]
    rows.append({"项目": "合计", "金额": "4500", "备注": ""})

    assert _check_structured_rows(rows) is None


def test_check_structured_rows_flags_a_text_blob_wearing_a_table_shell() -> None:
    """真实规避：被判 deliverable_not_table 后，模型把整页文本切段塞进「内容」列凑出 list[dict]。"""
    rows = [
        {"序号": i + 1, "类型": "回复" if i else "主题正文", "内容": f"第{i}段正文" + "文" * 60}
        for i in range(20)
    ]

    issue = _check_structured_rows(rows)

    assert issue is not None
    assert issue["issue"] == "single_column_text_shell"
    assert issue["payload_column"] == "内容"


def test_check_structured_rows_allows_a_real_table_with_one_long_column() -> None:
    """正文长不是缺陷：只要其余列装着页面上真实存在的字段（作者、时间各不相同）就算数。"""
    rows = [
        {"作者": f"user{i}", "时间": f"2026-07-{i + 1:02d}", "正文": "文" * 200}
        for i in range(20)
    ]

    assert _check_structured_rows(rows) is None


def test_check_structured_rows_allows_a_narrow_two_column_table() -> None:
    """序号 + 短标题的两列表没有「一列吞掉全部信息」的问题，不该按文本壳判。"""
    rows = [{"序号": i + 1, "标题": f"第 {i} 号议题"} for i in range(20)]

    assert _check_structured_rows(rows) is None


def test_parse_tool_arguments_reports_duplicate_keys() -> None:
    """模型想一次改多个节点时会重复写 node_id/config_patch，json 只保留最后一份。"""
    args, duplicates = _parse_tool_arguments(
        '{"flow_id":"f1","node_id":"n4","config_patch":{"timeoutMs":5000},'
        '"node_id":"n14","config_patch":{"extractMode":"text"}}'
    )

    assert duplicates == ["node_id", "config_patch"]
    assert args["node_id"] == "n14"


def test_parse_tool_arguments_accepts_repeated_keys_in_sibling_objects() -> None:
    """不同对象里的同名键是正常结构，不能误判。"""
    args, duplicates = _parse_tool_arguments(
        '{"update_nodes":[{"id":"n1","patch":{"selector":"a"}},{"id":"n2","patch":{"selector":"b"}}]}'
    )

    assert duplicates == []
    assert len(args["update_nodes"]) == 2


def test_annotate_login_redirect_marks_target_page_inspect_that_landed_on_login() -> None:
    """请求工作台、落到 /login?redirect=/workbench，返回的是登录表单而非目标页。"""
    result = {
        "url": "https://example.com/#/login?redirect=%2Fworkbench",
        "inputs": [{"type": "text"}, {"type": "password"}],
    }

    annotate_observation(result, "https://example.com/#/workbench")

    assert result["redirected_to_login"] is True
    assert result["page_outcome"] == "redirected_to_login"
    assert "不是目标页结构" in result["warning"]


def test_annotate_login_redirect_ignores_intentional_login_page_inspect() -> None:
    """本来就在查登录页时没有重定向，不该报警。"""
    result = {
        "url": "https://example.com/#/login",
        "inputs": [{"type": "text"}, {"type": "password"}],
    }

    annotate_observation(result, "https://example.com/#/login")

    assert "redirected_to_login" not in result
    assert result["page_outcome"] == "page_observed"


def test_annotate_without_requested_url_does_not_report_login_redirect() -> None:
    """没给 requested_url 就不下「被送去登录」的结论——扩展通道每次观察都带着会话的
    requested_url，interact_page 之后那次也带；若从结果里读，点击后落到登录页会只在
    扩展通道报 redirected_to_login，Playwright 不报，两条通道的结论就此分叉。"""
    landed_on_login = {
        "url": "https://example.com/#/login?redirect=%2Fworkbench",
        "inputs": [{"type": "text"}, {"type": "password"}],
        "requested_url": "https://example.com/#/workbench",
    }

    annotate_observation(landed_on_login)

    assert "redirected_to_login" not in landed_on_login
    assert landed_on_login["page_outcome"] == "page_observed"


def test_annotate_reports_still_loading_before_empty_content() -> None:
    """加载指示 + 零元素必须报还在渲染：报成「页面就是这样」会让模型拿空列表写 selector。"""
    result = {
        "url": "https://example.com/#/workbench",
        "page_classes": ["el-table", "is-loading"],
        "inputs": [], "buttons": [], "links": [], "tables": [],
    }

    spa_loading = annotate_observation(result, "https://example.com/#/workbench")

    assert spa_loading is True
    assert result["page_outcome"] == "still_loading"
    assert "页面元素为空" not in result["warning"]


def test_annotate_reports_empty_content_for_a_rendered_but_empty_page() -> None:
    result = {
        "url": "https://example.com/#/workbench",
        "page_classes": ["el-table"],
        "inputs": [], "buttons": [], "links": [], "tables": [],
    }

    annotate_observation(result, "https://example.com/#/workbench")

    assert result["page_outcome"] == "empty_content"
    assert "页面元素为空" in result["warning"]


def test_annotate_reports_target_content_ready_when_the_table_has_data_rows() -> None:
    result = {
        "url": "https://example.com/#/workbench",
        "inputs": [], "buttons": [], "links": [],
        "tables": [{"container_selector": ".custom-table", "row_count": 12}],
    }

    annotate_observation(result, "https://example.com/#/workbench")

    assert result["page_outcome"] == "target_content_ready"
    assert "warning" not in result


def test_annotate_reports_page_observed_when_only_navigation_is_present() -> None:
    """只有一排导航按钮：页面确实读到了，但没有任何目标区域的证据。
    报 target_content_ready 会让模型拿导航页的 DOM 去写提取节点。"""
    result = {
        "url": "https://example.com/#/workbench",
        "inputs": [], "links": [], "tables": [],
        "buttons": [{"text": "首页"}, {"text": "报表"}],
    }

    annotate_observation(result, "https://example.com/#/workbench")

    assert result["page_outcome"] == "page_observed"


def test_annotate_reports_page_observed_for_an_article_page_with_no_controls() -> None:
    """有正文没控件的文章页不是空页面：报 empty_content 会让模型去等一个
    这页上永远不会出现的控件。"""
    result = {
        "url": "https://example.com/posts/1",
        "inputs": [], "buttons": [], "links": [], "tables": [],
        "page_layout": [{"tag": "article", "html": "<article>正文……</article>"}],
    }

    annotate_observation(result, "https://example.com/posts/1")

    assert result["page_outcome"] == "page_observed"
    assert "warning" not in result


def test_annotate_reports_target_content_ready_when_scope_pinned_the_region() -> None:
    result = {
        "url": "https://example.com/#/workbench",
        "scope_selector": "#report",
        "inputs": [], "links": [], "tables": [],
        "buttons": [{"text": "导出"}],
    }

    annotate_observation(result, "https://example.com/#/workbench")

    assert result["page_outcome"] == "target_content_ready"


def test_annotate_reports_target_content_ready_when_wait_selector_was_satisfied() -> None:
    result = {
        "url": "https://example.com/#/workbench",
        "wait_result": {"status": "satisfied", "selector": "#report-table"},
        "inputs": [], "links": [], "tables": [],
        "buttons": [{"text": "导出"}],
    }

    annotate_observation(result, "https://example.com/#/workbench")

    assert result["page_outcome"] == "target_content_ready"


def test_classify_reports_access_failed_for_blocked_and_for_unread_dom() -> None:
    """blocked_* 与「结构键一个都没有」都是没能开始观察，不是「页面是空的」。"""
    blocked = {
        "status": "blocked_challenge_page",
        "url": "https://example.com/#/workbench",
        "inputs": [], "buttons": [], "links": [], "tables": [],
    }
    unread = {"url": "https://example.com/#/workbench"}

    assert classify_page_outcome(blocked, "https://example.com/#/workbench") == "access_failed"
    assert classify_page_outcome(unread, "https://example.com/#/workbench") == "access_failed"


def test_annotate_gives_no_page_outcome_when_scope_selector_missed() -> None:
    """scope 没命中时探测没看整页：报空内容会把模型推去等一个不存在的元素，
    而它该改的是 scope_selector（结果自带的 required_action 已经这么说）。"""
    result = {
        "url": "https://example.com/#/workbench",
        "scope_selector": ".nope",
        "scope_missing": True,
        "error": "scope_selector 在当前页面上没有命中任何元素，未回退到整页探测。",
        "required_action": "retry_without_scope_or_fix_selector",
        "inputs": [], "buttons": [], "links": [], "tables": [],
    }

    annotate_observation(result, "https://example.com/#/workbench")

    assert "page_outcome" not in result
    assert "warning" not in result
    assert result["required_action"] == "retry_without_scope_or_fix_selector"


def test_login_redirected_inspect_does_not_unlock_selector_circuit_breaker() -> None:
    """落到登录页的检查看到的是登录表单，不构成目标页证据。"""
    state = GuardState()

    _orchestrator_guard_after_tool(
        "inspect_page",
        {"url": "https://example.com/#/login?redirect=%2Fworkbench", "redirected_to_login": True},
        state,
    )

    assert not state.fresh_page_evidence


def test_lint_flags_extract_selector_built_as_a_class_union() -> None:
    """真实缺陷：并集里 .workbench-page 是其余三项的祖先，抽取塌成整页。"""
    nodes = [
        {"id": "n14", "type": "browser.extract", "title": "提取核心业务指标",
         "selector": ".workbench-page, .stats-section, .stats-grid, .stats-card",
         "extractMode": "text", "outputVariable": "metrics", "position": {"x": 0, "y": 0}},
    ]

    hit = next(
        f for f in _lint_flow(nodes, [])
        if f["issue"] == "extract_selector_union_used_as_fallback"
    )

    assert hit["severity"] == "warn"
    assert hit["node_id"] == "n14"


def test_lint_flags_extract_union_of_landmark_tags_and_substring_attributes() -> None:
    """真实缺陷：修复时把 body 换成这串并集，正文按嵌套层数重复，产物反而从 471KB 涨到 925KB。"""
    nodes = [
        {"id": "n3", "type": "browser.extract", "title": "提取正文",
         "selector": "article, main, .post, .topic, [class*='post'], [class*='topic']",
         "extractMode": "text", "outputVariable": "raw", "position": {"x": 0, "y": 0}},
    ]

    hit = next(
        f for f in _lint_flow(nodes, [])
        if f["issue"] == "extract_selector_union_used_as_fallback"
    )

    assert hit["severity"] == "warn"
    assert "[class*='post']" in hit["message"]


def test_lint_flags_a_substring_attribute_paired_with_one_scoped_selector() -> None:
    """子串匹配自己就覆盖祖先与后代，并集里有没有第三项跟这个缺陷无关。"""
    nodes = [
        {"id": "n3", "type": "browser.extract", "title": "提取正文",
         "selector": "#Main .topic_content, [class*='reply']",
         "extractMode": "text", "outputVariable": "raw", "position": {"x": 0, "y": 0}},
    ]

    assert any(
        f["issue"] == "extract_selector_union_used_as_fallback"
        for f in _lint_flow(nodes, [])
    )


def test_lint_allows_extract_union_of_exact_attribute_matches() -> None:
    """`[data-role="row"]` 是全等匹配，划得清一层，不会顺带命中它的父容器。"""
    nodes = [
        {"id": "n3", "type": "browser.extract", "title": "提取行",
         "selector": "#grid [data-role='row'], #grid [role='row']",
         "extractMode": "text", "outputVariable": "rows", "position": {"x": 0, "y": 0}},
    ]

    assert not any(
        f["issue"] == "extract_selector_union_used_as_fallback"
        for f in _lint_flow(nodes, [])
    )


def test_lint_allows_union_selector_on_wait_nodes() -> None:
    """并集在 wait 上是「任一出现即可」，属正常用法。"""
    nodes = [
        {"id": "n2", "type": "browser.wait", "title": "等待壳层",
         "selector": ".el-menu, .sidebar, .layout-container", "position": {"x": 0, "y": 0}},
    ]

    assert not any(
        f["issue"] == "extract_selector_union_used_as_fallback"
        for f in _lint_flow(nodes, [])
    )


def test_lint_allows_extract_union_with_scoped_selectors() -> None:
    """带层级/属性的并集是有意区分结构，不是由粗到细的兜底堆叠。"""
    nodes = [
        {"id": "n5", "type": "browser.extract", "title": "抽取正文",
         "selector": "#Main .topic_content, #Main .reply_content, #Main .header",
         "extractMode": "text", "outputVariable": "body", "position": {"x": 0, "y": 0}},
    ]

    assert not any(
        f["issue"] == "extract_selector_union_used_as_fallback"
        for f in _lint_flow(nodes, [])
    )


async def test_audit_rejects_evidence_from_an_old_flow_revision() -> None:
    now = datetime.now(UTC)
    task = TaskSnapshot(
        taskId="stale-task",
        flowId="flow-1",
        flowName="订单流程",
        flowRevision=1,
        mode="run",
        status="success",
        progress=RuntimeProgress(currentStep=2, totalSteps=2, percent=100, elapsedMs=1000),
        createdAt=now,
        updatedAt=now,
    )
    task_manager = SimpleNamespace(get_task=lambda task_id: None)

    async def get_task(task_id: str):
        return task if task_id == task.task_id else None

    async def get_flow(flow_id: str):
        return SimpleNamespace(flow_id="flow-1", revision=2)

    task_manager.get_task = get_task
    flow_service = SimpleNamespace(get_flow=get_flow)
    executor = RpaToolExecutor(flow_service=flow_service, task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor.execute("audit_run", {"task_id": "stale-task"})

    assert result["passed"] is False
    assert result["issues"][0]["issue"] == "stale_run_evidence"
    # 结论要自带「新版本号是几」，否则模型只知道旧了，不知道该重跑到哪一版
    assert "revision 2" in result["issues"][0]["message"]


async def test_audit_rejects_orphaned_or_digest_mismatched_evidence() -> None:
    now = datetime.now(UTC)
    task = TaskSnapshot(
        taskId="evidence-task",
        flowId="flow-1",
        flowName="订单流程",
        flowRevision=1,
        definitionDigest="old-digest",
        mode="run",
        status="success",
        progress=RuntimeProgress(currentStep=2, totalSteps=2, percent=100, elapsedMs=1000),
        createdAt=now,
        updatedAt=now,
    )

    async def get_task(_task_id: str):
        return task

    async def missing_flow(_flow_id: str):
        return None

    orphan_executor = RpaToolExecutor(  # type: ignore[arg-type]
        flow_service=SimpleNamespace(get_flow=missing_flow),
        task_manager=SimpleNamespace(get_task=get_task),
    )
    orphaned = await orphan_executor.execute("audit_run", {"task_id": task.task_id})
    assert orphaned["issues"][0]["issue"] == "orphaned_run_evidence"

    async def changed_flow(_flow_id: str):
        return SimpleNamespace(flow_id="flow-1", revision=1, definition={"nodes": [], "edges": []})

    digest_executor = RpaToolExecutor(  # type: ignore[arg-type]
        flow_service=SimpleNamespace(get_flow=changed_flow),
        task_manager=SimpleNamespace(get_task=get_task),
    )
    mismatched = await digest_executor.execute("audit_run", {"task_id": task.task_id})
    assert mismatched["issues"][0]["issue"] == "definition_digest_mismatch"


def test_session_requirement_text_keeps_only_user_turns():
    text = _session_requirement_text([
        {"role": "system", "content": "你是 RPA 助手"},
        {"role": "user", "content": "抓取工作台核心业务指标"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "币种符号切错列了，修一下"},
    ])
    assert text == "抓取工作台核心业务指标\n币种符号切错列了，修一下"


def test_claiming_the_flow_was_created_without_a_single_write_is_withdrawn():
    """评测里逐字抄回来的假话：只 inspect_page 过一次，就宣称流程已创建。

    这是用户报障的原样——回复自信、画布空的，而假话本身不带任何错误码，
    只有「宣称落盘」对上「一个节点都没有」才判得出来。
    """
    state = GuardState()
    correction = _overstated_result_claim(
        "流程已创建（无登录节点，直接抓取表格）。请在「输入变量」面板中查看默认输出路径，并运行流程以确认结果。",
        state,
    )
    assert correction is not None
    assert "create_flow" in correction

    # 每会话只纠正一次，否则改口后的回复会再次命中同一批词
    assert _overstated_result_claim("**已创建流程**：调用接口并写入本地文件。", state) is None


def test_pasting_a_flow_definition_with_a_revision_is_not_a_write():
    """模型把流程 JSON 连 revision 一起贴进回复当交付物。

    revision 只由平台在写入成功时下发，模型手上没有这个数，贴出来就是编的回执；
    只查词表会漏掉这一支——它一个「已创建」都没说。
    """
    correction = _overstated_result_claim(
        '```json\n{"flow_id":"example-list-to-excel","revision":1,'
        '"acceptance_contract":{"requirements":[]}}\n```',
        GuardState(),
    )
    assert correction is not None
    assert "不是交付物" in correction


def test_created_claim_is_allowed_once_the_write_landed():
    state = GuardState()
    _orchestrator_guard_after_tool(
        "create_flow", {"status": "created", "flow_id": "f1", "revision": 1}, state
    )
    assert _overstated_result_claim("流程已创建，共 9 个节点，尚未运行验证。", state) is None


def test_a_follow_up_turn_may_refer_to_last_turns_write():
    """判据挂空画布而不只挂「本会话写没写过」的原因。

    current_flow_revision 每次请求从零开始：续跑一轮说「流程已更新」指的是上一轮那次写入，
    只看它就会把一句真话撤回，模型接着自我否认，用户更懵。
    """
    state = GuardState(flow_has_nodes=True)
    assert _overstated_result_claim("流程已更新，等你确认后再跑一次。", state) is None


def test_saying_the_flow_is_not_saved_yet_passes():
    """出路必须真的走得通：据实说没保存、缺什么，不该也被撤回。"""
    assert _overstated_result_claim(
        "流程尚未保存：我还需要目标列表页的 URL 才能按真实 DOM 建节点，请提供后我再创建。", GuardState()
    ) is None


def test_static_checks_alone_cannot_be_called_acceptance():
    state = GuardState()
    correction = _overstated_result_claim("审查结果：验收通过，lint_flow 与 validate_flow 均无问题。", state)
    assert correction is not None
    assert "acceptance_audit.passed=true" in correction

    # 每会话只纠正一次，否则改口后的回复会再次命中同一批词
    assert _overstated_result_claim("验收通过", state) is None


def test_acceptance_claim_is_allowed_after_a_passing_audit():
    state = GuardState()
    _orchestrator_guard_after_tool(
        "run_flow", {"status": "success", "acceptance_audit": {"passed": True, "issues": []}}, state
    )
    assert _overstated_result_claim("验收通过，10 行数据与页面一致。", state) is None


def test_ordinary_completion_wording_is_not_treated_as_an_acceptance_claim():
    assert _overstated_result_claim("lint_flow：通过，无 error。已修改节点 n14。", GuardState(run_succeeded=True)) is None


def test_fix_claim_needs_a_successful_run_after_the_change():
    state = GuardState()
    _orchestrator_guard_after_tool("apply_node_fix", {"status": "applied"}, state)
    correction = _overstated_result_claim("已修复 n14 的拆分逻辑。", state)
    assert correction is not None
    assert "run_flow" in correction


def test_stating_only_what_was_changed_passes_while_unverified():
    state = GuardState()
    _orchestrator_guard_after_tool("apply_node_fix", {"status": "applied"}, state)
    assert _overstated_result_claim("已按你的要求把 selector 改成 .stats-card，尚未运行验证。", state) is None


def test_editing_the_flow_invalidates_the_earlier_run_and_audit():
    state = GuardState()
    _orchestrator_guard_after_tool("run_flow", {"status": "success"}, state)
    _orchestrator_guard_after_tool(
        "run_flow", {"status": "success", "acceptance_audit": {"passed": True, "issues": []}}, state
    )
    assert _overstated_result_claim("验收通过。", state) is None

    state.result_claim_corrected = False
    _orchestrator_guard_after_tool("update_flow", {"status": "applied"}, state)
    assert state.run_succeeded is False and state.audit_passed is False
    assert _overstated_result_claim("验收通过。", state) is not None


def test_requirement_text_drops_bare_commands_and_repeats():
    text = _session_requirement_text([
        {"role": "user", "content": "抓取工作台核心业务指标并导出 Excel"},
        {"role": "user", "content": "标题和数据要分开写入表格"},
        {"role": "user", "content": "流程审查验收"},
        {"role": "user", "content": "流程审查验收"},
        {"role": "user", "content": "修复"},
        {"role": "user", "content": "再跑一次"},
    ])
    # 指令句进了需求文本就会被当成需求关键词，在输出里永远找不到，误报内容不匹配
    assert text == "抓取工作台核心业务指标并导出 Excel\n标题和数据要分开写入表格"


def test_requirement_text_never_goes_empty():
    # 空需求会让 requirement_text 接管失效，模型又能自己填需求
    assert _session_requirement_text([{"role": "user", "content": "流程审查验收"}]) == "流程审查验收"


def test_requirement_text_keeps_the_latest_correction_when_over_budget():
    """超额时从中间丢，两端都要在。

    最新那句是 acceptance_contract sourceQuote 的唯一来源，从尾部截掉它，
    模型改验收契约时引什么都对不上，只能换措辞反复撞同一条护栏；
    首条是主条款的出处，同样不能丢。
    """
    messages = [{"role": "user", "content": "抓取工作台核心业务指标并导出 Excel"}]
    messages += [
        {"role": "user", "content": f"把第 {i} 列改成千分位并保留两位小数" * 8}
        for i in range(30)
    ]
    messages.append({"role": "user", "content": "只要状态为已通过的行"})

    text = _session_requirement_text(messages)

    assert len(text) <= 2000
    assert "抓取工作台核心业务指标并导出 Excel" in text
    assert "只要状态为已通过的行" in text
    assert "把第 29 列" in text
    assert "把第 0 列" not in text


def test_acceptance_request_without_a_single_run_is_pushed_back():
    state = _ready_state(latest_user_message="流程审查验收")
    correction = _unmet_verification_request(state)
    assert correction is not None
    assert "run_flow" in correction
    # 会话内只催一次，模型坚持不跑时不能空转
    assert _unmet_verification_request(state) is None


def test_no_nudge_once_the_flow_was_actually_run():
    state = GuardState(latest_user_message="验收一下")
    _orchestrator_guard_after_tool("run_flow", {"status": "timeout"}, state)
    assert _unmet_verification_request(state) is None


def test_review_request_alone_does_not_demand_a_run():
    state = GuardState(latest_user_message="帮我审查一下这个流程的结构")
    assert _unmet_verification_request(state) is None


def test_user_saying_not_to_run_is_respected():
    state = GuardState(latest_user_message="验收一下，但不要运行流程")
    assert _unmet_verification_request(state) is None


def test_a_runtime_blocker_prevents_another_nudge():
    state = _ready_state(latest_user_message="验收")
    _orchestrator_guard_after_tool("run_flow", {"status": "paused_for_human"}, state)
    assert _unmet_verification_request(state) is None


def _click_chain(count: int, selector: str) -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = [
        {"id": f"c{i}", "type": "browser.click", "title": f"翻页 {i}", "selector": selector}
        for i in range(count)
    ]
    edges = [{"source": f"c{i}", "target": f"c{i + 1}"} for i in range(count - 1)]
    return nodes, edges


def test_unrolled_repeat_chain_is_flagged():
    nodes, edges = _click_chain(5, ".el-picker-panel__icon-btn.el-icon-arrow-left")
    finding = next(f for f in _lint_flow(nodes, edges) if f["issue"] == "unrolled_repeat_click_chain")
    assert finding["severity"] == "error"
    assert finding["chain_node_ids"] == ["c0", "c1", "c2", "c3", "c4"]


def test_justified_fixed_count_chain_is_downgraded_to_warn():
    """规则的 fix 文案明说「确实是固定次数的业务动作可以保留」，那就不能同时阻断它。

    次数写在 description 里有依据时降为 warn；不写依据仍是 error——
    要拦的是「凭生成当天算出来的常量」，不是「业务上就是固定次数」。
    """
    nodes, edges = _click_chain(4, ".wizard-next")
    for node in nodes:
        node["description"] = "开户向导固定 4 步，步数由业务流程定义，与运行时间无关"
    finding = next(f for f in _lint_flow(nodes, edges) if f["issue"] == "unrolled_repeat_click_chain")
    assert finding["severity"] == "warn"
    assert "control.repeat_until" in finding["fix"]

    bare_nodes, bare_edges = _click_chain(4, ".wizard-next")
    bare = next(f for f in _lint_flow(bare_nodes, bare_edges) if f["issue"] == "unrolled_repeat_click_chain")
    assert bare["severity"] == "error"


def test_unrolled_load_more_chain_is_flagged_like_any_other_repeat():
    """规则的 fix 文案点名了「点到加载更多消失」，却漏掉了 browser.clickLoadMore 本身。

    加载更多是这条规则最典型的形态，而它的行选择器叫 targetSelector——
    字段名换一个就整条规避，等于闸门只对模型选了 browser.click 的那一半生效。
    """
    nodes = [
        {"id": f"m{i}", "type": "browser.clickLoadMore", "title": "加载更多",
         "targetSelector": ".load-more"}
        for i in range(4)
    ]
    edges = [{"source": f"m{i}", "target": f"m{i + 1}"} for i in range(3)]

    finding = next(f for f in _lint_flow(nodes, edges) if f["issue"] == "unrolled_repeat_click_chain")
    assert finding["severity"] == "error"
    assert finding["chain_node_ids"] == ["m0", "m1", "m2", "m3"]


def test_two_identical_clicks_are_not_a_chain():
    nodes, edges = _click_chain(2, ".next-month")
    assert not any(f["issue"] == "unrolled_repeat_click_chain" for f in _lint_flow(nodes, edges))


def test_same_selector_clicked_at_separated_points_is_not_unrolling():
    # 三个对话框各点一次「确定」：selector 相同但中间隔着别的节点，不是循环展开
    nodes = [
        {"id": "ok1", "type": "browser.click", "title": "确定", "selector": "button:has-text('确定')"},
        {"id": "step", "type": "browser.fill", "title": "填写", "selector": "#name", "inputValue": "x"},
        {"id": "ok2", "type": "browser.click", "title": "确定", "selector": "button:has-text('确定')"},
        {"id": "step2", "type": "browser.fill", "title": "填写", "selector": "#age", "inputValue": "1"},
        {"id": "ok3", "type": "browser.click", "title": "确定", "selector": "button:has-text('确定')"},
    ]
    edges = [
        {"source": "ok1", "target": "step"},
        {"source": "step", "target": "ok2"},
        {"source": "ok2", "target": "step2"},
        {"source": "step2", "target": "ok3"},
    ]
    assert not any(f["issue"] == "unrolled_repeat_click_chain" for f in _lint_flow(nodes, edges))


def test_branching_breaks_the_chain():
    nodes, edges = _click_chain(4, ".load-more")
    edges.append({"source": "c1", "target": "other"})
    nodes.append({"id": "other", "type": "browser.wait", "title": "等待", "selector": ".done"})
    findings = [f for f in _lint_flow(nodes, edges) if f["issue"] == "unrolled_repeat_click_chain"]
    assert findings == []


def test_condition_false_branch_placeholder_is_spliced_out():
    nodes = [
        {"id": "n5", "type": "control.condition", "title": "是否需要登录", "inputValue": "c > 0"},
        {"id": "n6", "type": "browser.fill", "title": "填账号", "selector": "#u", "inputValue": "a"},
        {"id": "n11", "type": "control.noop", "title": "已登录-跳过"},
        {"id": "n12", "type": "browser.open", "title": "打开工作台", "targetUrl": "https://x"},
    ]
    edges = [
        {"source": "n5", "target": "n6", "label": "true"},
        {"source": "n5", "target": "n11", "label": "false"},
        {"source": "n11", "target": "n12"},
        {"source": "n6", "target": "n12"},
    ]
    kept_nodes, kept_edges, spliced = _splice_branch_placeholder_noops(nodes, edges)

    assert spliced == ["n11"]
    assert [n["id"] for n in kept_nodes] == ["n5", "n6", "n12"]
    assert {"source": "n5", "target": "n12", "label": "false"} in kept_edges
    assert not any(e["target"] == "n11" or e["source"] == "n11" for e in kept_edges)


def test_noop_outside_a_condition_branch_is_left_alone():
    nodes = [
        {"id": "a", "type": "browser.open", "title": "打开", "targetUrl": "https://x"},
        {"id": "mark", "type": "control.noop", "title": "分隔标记"},
        {"id": "b", "type": "browser.click", "title": "点击", "selector": "#x"},
    ]
    edges = [{"source": "a", "target": "mark"}, {"source": "mark", "target": "b"}]
    kept_nodes, _, spliced = _splice_branch_placeholder_noops(nodes, edges)
    assert spliced == []
    assert len(kept_nodes) == 3


def test_noop_with_several_successors_is_not_a_passthrough():
    nodes = [
        {"id": "n5", "type": "control.condition", "title": "判断", "inputValue": "c > 0"},
        {"id": "fan", "type": "control.noop", "title": "扇出"},
        {"id": "x", "type": "browser.click", "title": "点 x", "selector": "#x"},
        {"id": "y", "type": "browser.click", "title": "点 y", "selector": "#y"},
    ]
    edges = [
        {"source": "n5", "target": "fan", "label": "false"},
        {"source": "fan", "target": "x"},
        {"source": "fan", "target": "y"},
    ]
    _, _, spliced = _splice_branch_placeholder_noops(nodes, edges)
    assert spliced == []


def _few_shot_create_flow_args() -> dict[str, Any]:
    """few-shot 里那次 create_flow 的参数。"""
    from app.services.ai_orchestrator import _build_few_shot_messages

    for message in _build_few_shot_messages():
        for call in message.get("tool_calls") or []:
            args = json.loads(call["function"]["arguments"])
            if "nodes" in args:
                return args
    raise AssertionError("few-shot 里没有建流程的 create_flow 调用")


def test_few_shot_example_passes_our_own_lint() -> None:
    """few-shot 是模型最照抄的一段，它自己必须是合法流程。

    曾经的示例用 `${var.login_count} > 0` 做条件表达式，而 lint 把模板变量判成 error——
    等于教模型写一个建完就要返工的结构。
    """
    args = _few_shot_create_flow_args()
    findings = _lint_flow(
        args["nodes"],
        args.get("edges", []),
        input_variable_names=[iv["name"] for iv in args.get("input_variables", [])],
    )
    assert findings == [], f"few-shot 违反了自己的 lint 规则：{findings}"


def test_text_only_scrape_flow_is_warned_before_running() -> None:
    """回归：文本抽取的流程要在 lint 阶段就报，而不是跑完由验收审计判 deliverable_not_table。

    真实会话 flow_da297cc0 里，这个形态骗过了 lint，跑完才被审计打回，整条修复链路白跑一次浏览器。
    """
    from app.services.ai_tools.lint_scenarios import _lint_scrape_flow_without_table_output

    text_extract = {
        "id": "n11", "type": "browser.extract", "title": "提取核心业务指标",
        "selector": ".stats-card", "extractMode": "text", "outputVariable": "stats_texts",
    }
    assert [f["issue"] for f in _lint_scrape_flow_without_table_output([text_extract])] == [
        "scrape_flow_without_table_output"
    ]

    # 抽取节点自己出表就不报
    assert _lint_scrape_flow_without_table_output([{**text_extract, "extractMode": "table"}]) == []
    # 脚本把文本整理成行也不报：脚本输出形态静态不可知，宁可漏报不误报
    parser = {"id": "n11b", "type": "script.python", "title": "整理为结构化行", "outputVariable": "stats_rows"}
    assert _lint_scrape_flow_without_table_output([text_extract, parser]) == []
    # 这是豁免不是判据：换成 JS 脚本或列变换节点做同一件事，同样不该凭空多一条误报
    for node_type in ("script.javascript", "data.list.map", "data.convert"):
        equivalent = {**parser, "id": "n11c", "type": node_type}
        assert _lint_scrape_flow_without_table_output([text_extract, equivalent]) == [], node_type
    # 没有抽取节点的流程（纯填单/点击）不在这条规则管辖内
    assert _lint_scrape_flow_without_table_output([{"id": "n1", "type": "browser.fill", "selector": "#a"}]) == []


def test_paginated_table_extract_is_linted_like_a_plain_table_extract() -> None:
    """翻页/加载更多节点的行选择器在 targetSelector 上，规则只认 browser.extract 时整段规避。"""
    from app.services.ai_tools.lint_scenarios import _lint_table_output_risks

    node = {
        "id": "n1", "type": "browser.paginateNext", "title": "翻页提取",
        "selector": ".el-pagination button.btn-next",
        "targetSelector": "tbody tr",
        "extractMode": "table",
        "outputVariable": "rows", "countVariable": "row_count",
    }
    issues = {f["issue"] for f in _lint_table_output_risks([node])}
    assert "table_extract_selector_too_broad" in issues
    # 翻页按钮 selector 不是行选择器，不能拿它去判「不像表格」
    assert "table_extract_selector_not_table_like" not in issues

    scoped = {**node, "targetSelector": ".audit-table-wrapper tbody tr"}
    assert _lint_table_output_risks([scoped]) == []
    assert [f["issue"] for f in _lint_table_output_risks([{k: v for k, v in scoped.items() if k != "countVariable"}])] == [
        "table_extract_missing_count"
    ]


def test_few_shot_carries_no_real_host_or_credential() -> None:
    """few-shot 每轮都随请求发给模型厂商，站点和凭据必须是 mock。

    域名限定在 RFC 保留 TLD（.test / .example / example.com），凭据不得是真值。
    """
    import re

    from app.services.ai_orchestrator import _build_few_shot_messages

    blob = json.dumps(_build_few_shot_messages(), ensure_ascii=False)
    hosts = set(re.findall(r"https?://([A-Za-z0-9.\-]+)", blob))
    assert hosts, "few-shot 应当含示例 URL，否则这条断言是空转的"
    for host in hosts:
        assert host.endswith((".test", ".example", "example.com", "localhost")), f"few-shot 含真实域名：{host}"

    for leaked in ("yingdiantone", '"admin"', '"123456"'):
        assert leaked not in blob, f"few-shot 含真实数据：{leaked}"


def test_few_shot_example_declares_every_required_common_field() -> None:
    """提示词把 description 列为必填公共字段，示例缺了它就是在演示可以不填。"""
    args = _few_shot_create_flow_args()
    business = [n for n in args["nodes"] if n["type"] not in {"start", "end"}]
    missing = [n["id"] for n in business if not str(n.get("description") or "").strip()]
    assert missing == [], f"这些示例节点缺 description：{missing}"


def test_login_submit_can_land_on_data_page_without_an_extra_open() -> None:
    nodes = [
        {"id": "n1", "type": "browser.ensureLogin", "title": "探测登录态", "targetUrl": "https://x.test/"},
        {"id": "n2", "type": "browser.fill", "title": "填密码", "selector": "input[type='password']", "inputValue": "${var.password}"},
        {"id": "n3", "type": "browser.click", "title": "提交登录", "selector": "button[type=submit]"},
        {"id": "n4", "type": "browser.extract", "title": "抓表格", "selector": "tbody tr", "extractMode": "table", "outputVariable": "rows"},
    ]
    edges = [{"source": "n1", "target": "n2"}, {"source": "n2", "target": "n3"}, {"source": "n3", "target": "n4"}]
    issues = [f["issue"] for f in _lint_flow(nodes, edges, input_variable_names=["password"])]
    assert "single_navigation_node" not in issues
    assert "login_without_navigation_to_data_page" not in issues


def test_few_shot_follows_the_current_turn_not_the_whole_session() -> None:
    """样例按「这一句在要什么」注入。

    以前拼接整个会话，第一轮说过「建流程抓取」之后，后面每一轮追加改动
    都会继续塞进这份完整的建流程样例，把增量修改带偏成重建。
    """
    from app.services.ai_orchestrator import _should_inject_few_shot

    create_turn = [{"role": "user", "content": "帮我创建一个流程，抓取 https://x.test 的分页表格并按日期筛选"}]
    assert _should_inject_few_shot(create_turn) is True

    # 没有 URL 时只能先追问，简单页面也用不到登录+日期+分页的重型示例
    assert _should_inject_few_shot([{"role": "user", "content": "帮我根据网页创建抓取流程"}]) is False
    assert _should_inject_few_shot([{"role": "user", "content": "抓取 https://x.test 的正文"}]) is False

    follow_up = create_turn + [
        {"role": "assistant", "content": "已创建"},
        {"role": "user", "content": "再加一列创建时间"},
    ]
    assert _should_inject_few_shot(follow_up) is False


async def test_few_shot_requirement_never_leaks_into_the_guard_state(monkeypatch) -> None:
    """few-shot 那轮虚构的 user 消息不能被当成用户需求。

    它写着「筛选创建时间 2026-06-01 至今天、项目进度为项目通过/待尽调」；混进
    user_requirement_text 后会被 acceptance_contract_sources_must_match_user 当成用户原话，
    让模型能拿 few-shot 里的句子当引用去改验收契约——而且只在"新建抓取流程"时触发，
    正是最常见的那条路径。
    """
    import litellm

    from app.services.ai_orchestrator import AiOrchestrator, _should_inject_few_shot

    user_request = "帮我创建流程，分页抓取 https://shop.test 的商品名和价格并按日期筛选"
    assert _should_inject_few_shot([{"role": "user", "content": user_request}]) is True

    captured: list[dict[str, Any]] = []

    class _Recorder:
        async def execute(self, tool_name: str, args: dict[str, Any],
                          progress_sink: Any = None, change_context: Any = None) -> dict[str, Any]:
            captured.append(args)
            return {"status": "ok"}

    class _Stream:
        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            raise StopAsyncIteration

    async def fake_acompletion(**kwargs: Any) -> Any:
        return _Stream()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    orchestrator = AiOrchestrator(tool_executor=_Recorder())  # type: ignore[arg-type]

    seen_state: dict[str, Any] = {}
    import app.services.ai_orchestrator as _mod

    original = _mod._session_requirement_text
    def _spy(msgs: list[dict[str, Any]]) -> str:
        seen_state["text"] = original(msgs)
        return seen_state["text"]
    monkeypatch.setattr(_mod, "_session_requirement_text", _spy)

    async for _ in orchestrator.stream(messages=[{"role": "user", "content": user_request}], model="test-model"):
        pass

    assert seen_state["text"] == user_request
    assert "demo-rpa.test" not in seen_state["text"]


def test_blocked_write_is_not_treated_as_a_successful_write() -> None:
    """guard 拦下来的调用不能算执行成功。

    拦截结果里没有 error 字段，只有 status=blocked_by_orchestrator_guard；
    按「没有 error 就算成功」判断，编排层会紧接着注入"变更已写入，下一步 run_flow"，
    把 read-only 模式、failure budget 锁这些拦截全部抵消掉。
    """
    from app.services.ai_orchestrator import _tool_call_succeeded

    assert _tool_call_succeeded({"flow_id": "f1", "status": "updated"}) is True
    assert _tool_call_succeeded({"status": "blocked_by_orchestrator_guard", "blocked_tool": "update_flow"}) is False
    assert _tool_call_succeeded({"status": "skipped"}) is False
    assert _tool_call_succeeded({"error": "流程不存在"}) is False
    assert _tool_call_succeeded("not a dict") is False


def test_inspect_gate_cannot_be_sidestepped_by_switching_write_tool() -> None:
    """未 inspect_page 前，三个落节点的工具都要挡住。

    阶段准入按「这一轮能推进任务的动作类别」布防，而不是按具体某一个工具；
    只挡一个的话，模型改调 create_flow 或 apply_node_fix 就绕过了整道检查。
    """
    for attempted in ("update_flow", "create_flow", "apply_node_fix"):
        state = _ready_state(
            page_evidence_required={"url": "https://x.test", "reason": "build_from_page"},
            page_evidence_done=False,
        )
        blocked = _orchestrator_guard_before_tool(attempted, {"flow_id": "f1"}, state)
        assert blocked is not None, f"{attempted} 绕过了 inspect 门"
        assert blocked["required_tools"] == ["inspect_page"]

    # 探测过之后三个都放行
    state = _ready_state(page_evidence_required={"url": "https://x.test"}, page_evidence_done=True)
    for attempted in ("update_flow", "create_flow", "apply_node_fix"):
        assert _orchestrator_guard_before_tool(attempted, {"flow_id": "f1"}, state) is None


def test_repair_ledger_carries_failed_attempts_across_sessions(tmp_path, monkeypatch) -> None:
    """防打转判定必须跨会话生效。

    guard_state 每条用户消息重建一次，计数清零。用户只要回一句"还是不行"，
    模型就能把上一轮试过并失败的 selector 原样再试一遍——这正是流程被修好几天
    仍未修好的机械原因。台账落盘后，新会话第一次写入就拦得住。
    """
    from app.services import ai_repair_ledger as ledger
    from app.services.ai_tools.lint_diff import inspect_change

    monkeypatch.setattr(ledger, "resolve_ai_dir", lambda: tmp_path)

    ledger.save(
        "flow-1",
        node_field_history={"n_date.selector": [".a-picker input", ".b-picker input"]},
        node_selector_fix_counts={"n_date": 2},
        sessions=1,
    )

    # 新会话：写入期判定直接从台账读历史，不依赖任何会话内状态
    loaded = ledger.load("flow-1")
    assert loaded["node_selector_fix_counts"]["n_date"] == 2

    before = {"nodes": [{"id": "n_date", "type": "browser.input", "selector": ".b-picker input"}]}
    after = {"nodes": [{"id": "n_date", "type": "browser.input", "selector": ".a-picker input"}]}
    report = inspect_change(before, after, ledger=loaded)
    assert report.rejected
    assert {f["issue"] for f in report.findings} == {
        "field_oscillation", "selector_fix_budget_exhausted",
    }

    # 摘要要把历史尝试直接摆给模型看
    summary = ledger.summarize(loaded)
    assert summary is not None and "n_date" in summary and ".a-picker input" in summary

    # 跑通并通过业务校验后清账，否则陈旧计数会挡住之后的正常编辑
    ledger.clear("flow-1")
    cleared = ledger.load("flow-1")
    assert cleared["node_selector_fix_counts"] == {}
    assert not inspect_change(before, after, ledger=cleared).rejected


def test_generic_date_recipe_covers_unknown_component_libraries() -> None:
    """没写过配方的组件库也要拿得到 date_controls。

    只按 class 指纹匹配时，Arco/Vant/iView/自研组件一律返回不了配方，
    模型只能凭空猜 selector 和交互方式。识别改用与框架无关的日期特征。
    """
    from app.services.skills.generic import build_generic_date_controls

    picker = [{"uid": 1, "ident": ".arco-picker", "selector": ".arco-picker"}]
    controls = build_generic_date_controls([
        {"placeholder": "开始日期", "label": "签约时间", "selector": ".arco-picker input:nth-child(1)",
         "containers": picker},
        {"placeholder": "结束日期", "label": "签约时间", "selector": ".arco-picker input:nth-child(2)",
         "containers": picker},
        {"placeholder": "关键词", "selector": "input[name='kw']"},
    ])
    assert len(controls) == 1
    recipe = controls[0]["interaction_recipe"]
    assert recipe["trigger"] == ".arco-picker input:nth-child(1)"
    assert recipe["end_input"] == ".arco-picker input:nth-child(2)"
    # Enter 必须打在输入框上；打在 body 上不会冒泡到组件的按键处理
    assert any("Enter on end_input" in step for step in recipe["steps"])
    assert not any("Enter on body" in step for step in recipe["steps"])
    # 未知框架的弹层类名只有点开后才存在，备选路线必须要求再探一次页面
    assert any("inspect_page" in step for step in recipe["fallback_steps"])
    # 回读硬门控不能因为是通用配方就省掉
    assert any("raise SystemExit" in step for step in recipe["steps"])

    assert build_generic_date_controls([{"placeholder": "用户名", "selector": "#u"}]) == []


def test_client_side_filter_is_blocked_as_masking_for_any_field() -> None:
    """脚本兜底过滤会把「页面筛选失效」完全掩盖成绿灯。

    页面筛选没生效 → 抓回未筛选结果的前几页 → 脚本把不合条件的行删掉 →
    输出全部合规、质量审计通过、流程成功。用户拿到的数据大量缺失，
    而所有信号都显示正常。分界线是断言不是过滤。

    触发条件取「脚本裁剪结果集所用的值 == 流程写进页面的筛选条件值」这个结构特征，
    所以日期只是其中一种，枚举/关键词同样拦得住。
    """
    from app.services.ai_tools.lint_scenarios import _lint_client_side_filter_masks_page_filter

    date_nodes = [
        {"id": "n_d1", "type": "browser.fill", "title": "填写开始日期",
         "selector": "input[placeholder='开始日期']", "inputValue": "${var.date_start}"},
    ]
    filtering = {
        "id": "n_filter", "type": "script.python", "title": "过滤日期范围",
        "outputVariable": "all_data",
        "code": (
            "start = _vars.get('date_start')\n"
            "rows = _vars.get('all_data', [])\n"
            "filtered = []\n"
            "for row in rows:\n"
            "    if d >= start and d <= end:\n"
            "        filtered.append(row)\n"
            "print(json.dumps(filtered))\n"
        ),
    }
    findings = _lint_client_side_filter_masks_page_filter(date_nodes + [filtering])
    assert [f["issue"] for f in findings] == ["client_side_filter_masks_page_filter"]
    assert findings[0]["severity"] == "error"

    # 断言型脚本（不合条件即失败）是我们要求的写法，不能误伤
    asserting = dict(filtering, id="n_assert", code=(
        "start = _vars.get('date_start')\n"
        "for row in rows:\n"
        "    if d < start or d > end:\n"
        "        raise SystemExit('日期越界')\n"
    ))
    assert _lint_client_side_filter_masks_page_filter(date_nodes + [asserting]) == []

    # 流程本身没有把条件写进页面时，脚本里怎么处理数据不归这条规则管
    assert _lint_client_side_filter_masks_page_filter([filtering]) == []

    # 枚举筛选：同一条规则必须照拦，不能只认日期
    enum_nodes = [
        {"id": "n_e1", "type": "browser.select", "title": "选择项目进度",
         "selector": ".progress-select", "inputValue": "${var.progress}"},
        {"id": "n_e2", "type": "script.python", "title": "过滤项目进度",
         "code": (
             "want = _vars.get('progress')\n"
             "out = []\n"
             "for row in rows:\n"
             "    if row['项目进度'] == want:\n"
             "        out.append(row)\n"
         )},
    ]
    assert [f["issue"] for f in _lint_client_side_filter_masks_page_filter(enum_nodes)] == [
        "client_side_filter_masks_page_filter"
    ]


def test_client_side_filter_is_judged_the_same_in_every_script_language() -> None:
    """静默丢数据与用哪种脚本语言写无关，这条规则却跑在三种通道上只认 Python 写法。

    两个方向都错：JS 的 filter/push 查不出来（漏掉真实数据缺失），
    JS 的 throw 又不被当断言（把正确写法判成过滤）。
    """
    from app.services.ai_tools.lint_scenarios import _lint_client_side_filter_masks_page_filter

    page_filter = {
        "id": "n_d1", "type": "browser.fill", "title": "填写开始日期",
        "selector": "input[placeholder='开始日期']", "inputValue": "${var.date_start}",
    }
    js_filtering = {
        "id": "n_js", "type": "script.javascript", "title": "整理数据",
        "code": (
            "const start = vars.date_start;\n"
            "const kept = rows.filter(r => r.date >= start);\n"
            "console.log(JSON.stringify(kept));\n"
        ),
    }
    js_asserting = dict(js_filtering, id="n_js_ok", code=(
        "const start = vars.date_start;\n"
        "const bad = rows.filter(r => r.date < start);\n"
        "if (bad.length) { throw new Error('日期越界，页面筛选没生效'); }\n"
    ))

    assert [f["issue"] for f in _lint_client_side_filter_masks_page_filter([page_filter, js_filtering])] == [
        "client_side_filter_masks_page_filter"
    ]
    assert _lint_client_side_filter_masks_page_filter([page_filter, js_asserting]) == []


def test_date_readback_gate_counts_whichever_language_wrote_it() -> None:
    """回读硬门控是不是门控，取决于「不一致就让流程失败」，不取决于用 Python 还是 JS 写。

    只扫 script.python 的话，同样一道比对写成 script.javascript 就被判成缺门控——
    这是 error 级，会直接拦住一个本来正确的流程。
    """
    from app.services.ai_tools.lint_scenarios import _lint_filter_control_risks

    write = {"id": "n_w", "type": "browser.fill", "title": "填写开始日期",
             "selector": ".start", "inputValue": "${var.date_start}"}
    readback = {"id": "n_r", "type": "browser.extract", "title": "回读开始日期",
                "selector": ".start", "extractMode": "attribute", "attribute": "value",
                "firstValueVariable": "start_actual"}
    js_gate = {"id": "n_g", "type": "script.javascript", "title": "校验日期已提交",
               "code": "if (vars.start_actual !== vars.date_start) { throw new Error('日期未提交'); }\n"}

    assert [f["issue"] for f in _lint_filter_control_risks([write, readback])] == [
        "date_filter_missing_verification"
    ], "没有任何比对时必须报"
    assert _lint_filter_control_risks([write, readback, js_gate]) == []


def test_date_filter_written_through_a_dropdown_still_needs_the_gate() -> None:
    """日期条件用 select 写、或走桌面通道写，静默失效的方式完全一样。"""
    from app.services.ai_tools.lint_scenarios import _lint_filter_control_risks

    for node_type in ("browser.select", "ui.fill"):
        node = {"id": "n_w", "type": node_type, "title": "选择开始时间",
                "selector": ".start", "inputValue": "2026-07-01"}
        assert [f["issue"] for f in _lint_filter_control_risks([node])] == [
            "date_filter_missing_verification"
        ], node_type


def test_navigation_trace_flags_route_guard_redirect() -> None:
    """导航被路由守卫打回时，证据要由工具给出，而不是让 AI 自己翻日志拼。"""
    nodes = [
        {"id": "n_open_home", "type": "browser.open", "title": "打开首页"},
        {"id": "n_open_data", "type": "browser.open", "title": "打开数据页"},
        {"id": "n_wait", "type": "browser.wait", "selector": "table"},
    ]
    logs = [
        SimpleNamespace(node_id="n_open_home", level="running", message="", detail="https://demo.test"),
        SimpleNamespace(node_id="n_open_home", level="success", message="", detail="https://demo.test/#/index"),
        SimpleNamespace(node_id="n_open_data", level="running", message="", detail="https://demo.test/#/project/list"),
        SimpleNamespace(node_id="n_open_data", level="success", message="", detail="https://demo.test/#/"),
        SimpleNamespace(node_id="n_open_data", level="success", message="", detail="file:///tmp/shot.png"),
    ]

    trace = build_navigation_trace(logs, nodes)
    by_id = {entry["node_id"]: entry for entry in trace}

    # 请求裸域名、落在应用默认路由，是 SPA 正常行为，不能报成导航失败
    assert by_id["n_open_home"]["redirected"] is False
    assert by_id["n_open_data"]["redirected"] is True
    assert by_id["n_open_data"]["landed_url"] == "https://demo.test/#/"
    # 只有导航节点进 trace，截图 detail 不能被当成落地 URL
    assert "n_wait" not in by_id

    verdict = build_navigation_verdict(trace)
    assert verdict is not None
    assert verdict["kind"] == "navigation_redirected"
    assert "n_open_data" in verdict["message"]


def test_navigation_trace_is_silent_when_every_navigation_landed() -> None:
    nodes = [{"id": "n_open", "type": "browser.open", "title": "打开列表页"}]
    logs = [
        SimpleNamespace(node_id="n_open", level="running", message="", detail="https://demo.test/#/list"),
        SimpleNamespace(node_id="n_open", level="success", message="", detail="https://demo.test/#/list?page=1"),
    ]

    trace = build_navigation_trace(logs, nodes)
    assert trace[0]["redirected"] is False  # query 参数差异不算导航失败
    assert build_navigation_verdict(trace) is None


def test_login_without_navigation_to_data_page_is_blocked() -> None:
    """登录后不导航就取数是结构缺陷，看拓扑即可判定，不必等运行时 selector 超时。"""
    nodes = [
        {"id": "n_open", "type": "browser.open", "targetUrl": "https://demo.test"},
        {"id": "n_user", "type": "browser.fill", "selector": "input[name=user]"},
        {"id": "n_pwd", "type": "browser.fill", "selector": "input[type='password']"},
        {"id": "n_submit", "type": "browser.press", "selector": "input[type='password']"},
        {"id": "n_grab", "type": "browser.extract", "selector": "tbody tr", "outputVariable": "rows", "extractMode": "table"},
    ]
    edges = [
        {"source": "n_open", "target": "n_user"},
        {"source": "n_user", "target": "n_pwd"},
        {"source": "n_pwd", "target": "n_submit"},
        {"source": "n_submit", "target": "n_grab"},
    ]

    issues = {f["issue"] for f in _lint_flow(nodes, edges)}
    assert "login_without_navigation_to_data_page" in issues

    # 补一次导航就不该再报——不限定导航方式，菜单点击同样算数
    edges_with_nav = [e for e in edges if e != {"source": "n_submit", "target": "n_grab"}] + [
        {"source": "n_submit", "target": "n_menu"},
        {"source": "n_menu", "target": "n_grab"},
    ]
    nodes_with_nav = [*nodes, {"id": "n_menu", "type": "browser.click", "selector": ".side-menu .contract"}]
    repaired = {f["issue"] for f in _lint_flow(nodes_with_nav, edges_with_nav)}
    assert "login_without_navigation_to_data_page" not in repaired


def test_probe_extract_feeding_a_branch_must_tolerate_zero_matches() -> None:
    nodes = [
        {"id": "n_probe", "type": "browser.extract", "selector": "input[type='password']",
         "extractMode": "count", "countVariable": "login_count"},
        {"id": "n_branch", "type": "control.condition", "inputValue": "login_count > 0"},
    ]
    edges = [{"source": "n_probe", "target": "n_branch"}]

    issues = {f["issue"] for f in _lint_flow(nodes, edges)}
    assert "probe_extract_without_continue_on_error" in issues

    tolerant = [{**nodes[0], "continueOnError": True}, nodes[1]]
    assert "probe_extract_without_continue_on_error" not in {
        f["issue"] for f in _lint_flow(tolerant, edges)
    }

    # 计数没有喂给任何分支时，它就是普通抽取，失败该中断，不该被要求容错
    standalone = [{**nodes[0]}]
    assert "probe_extract_without_continue_on_error" not in {
        f["issue"] for f in _lint_flow(standalone, [])
    }


def test_probe_count_feeding_a_loop_exit_has_the_same_zero_ambiguity() -> None:
    """「数到 0」的歧义与消费它的是 if 还是循环退出条件无关。

    只认 control.condition 的话，模型改用 control.repeat_until 表达同一件事就不再提示，
    而元素不存在时运行器抛的仍然是超时，流程照样直接失败。
    """
    nodes = [
        {"id": "n_probe", "type": "browser.extract", "selector": ".load-more",
         "extractMode": "count", "countVariable": "more_count"},
        {"id": "n_loop", "type": "control.repeat_until", "condition": "more_count == 0"},
    ]
    edges = [{"source": "n_probe", "target": "n_loop"}]

    assert "probe_extract_without_continue_on_error" in {f["issue"] for f in _lint_flow(nodes, edges)}


def test_runtime_variable_type_accepts_any_case() -> None:
    """大小写不携带语义，入口归一化，省得调用方靠提示词记住首字母大写。"""
    from app.models.schemas import RuntimeVariableSnapshot

    assert RuntimeVariableSnapshot(name="u", type="string", value="").type == "String"
    assert RuntimeVariableSnapshot(name="u", type="LIST", value="").type == "List"


def test_template_refs_are_stripped_from_name_and_condition_fields() -> None:
    """`${var.x}` 写在变量名字段/条件表达式里只有一种解释，入口还原即可，不必挂 lint 换一轮修复。"""
    nodes = _normalize_generated_nodes([
        {"id": "n1", "type": "browser.extract", "selector": "tbody tr",
         "outputVariable": "${var.rows}", "countVariable": "${var.row_count}"},
        {"id": "n2", "type": "control.condition", "inputValue": "${var.row_count} > 0"},
        {"id": "n3", "type": "control.repeat_until", "condition": "${var.panel_month} == ${var.target_month}"},
        {"id": "n4", "type": "browser.fill", "selector": "input", "inputValue": "${var.username}"},
    ])
    by_id = {n["id"]: n for n in nodes}

    assert by_id["n1"]["outputVariable"] == "rows"
    assert by_id["n1"]["countVariable"] == "row_count"
    assert by_id["n2"]["inputValue"] == "row_count > 0"
    assert by_id["n3"]["condition"] == "panel_month == target_month"
    # browser.fill 的 inputValue 是要填进页面的值，模板必须原样保留
    assert by_id["n4"]["inputValue"] == "${var.username}"


def test_blind_delay_before_selector_node_is_flagged() -> None:
    """猜的毫秒数不够时，失败会报在下游 selector 上，看起来像选择器写错——所以在生成期就提示。"""
    nodes = [
        {"id": "n_open", "type": "browser.open", "targetUrl": "https://x.test/", "delayMs": 3000},
        {"id": "n_click", "type": "browser.click", "selector": "button:has-text('登录')"},
    ]
    edges = [{"source": "n_open", "target": "n_click"}]

    findings = _lint_flow(nodes, edges)
    hit = [f for f in findings if f["issue"] == "blind_delay_instead_of_wait"]
    assert [f["node_id"] for f in hit] == ["n_open"]
    # 只是提示更好的写法，不该拦住保存
    assert hit[0]["severity"] == "warn"

    # 下游已经在等元素，delay 至多冗余
    waited = nodes + [{"id": "n_wait", "type": "browser.wait", "selector": "button"}]
    assert not [
        f for f in _lint_flow(waited, [{"source": "n_open", "target": "n_wait"},
                                       {"source": "n_wait", "target": "n_click"}])
        if f["issue"] == "blind_delay_instead_of_wait"
    ]

    # 几百毫秒的动画/防抖收尾是 delayMs 的正当用法
    short = [{**nodes[0], "delayMs": 300}, nodes[1]]
    assert not [f for f in _lint_flow(short, edges) if f["issue"] == "blind_delay_instead_of_wait"]


def test_eval_mock_executor_signature_tracks_the_real_executor() -> None:
    """mock 与真执行器签名漂移时，编排层会把 TypeError 当成「工具失败」吞掉，评测就不再反映真实行为。"""
    import inspect
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evals.run_evals import MockToolExecutor
    from app.services.ai_tools.executor import RpaToolExecutor

    assert (
        inspect.signature(MockToolExecutor.execute).parameters.keys()
        == inspect.signature(RpaToolExecutor.execute).parameters.keys()
    )


def test_node_catalog_is_returned_on_demand() -> None:
    """目录按需返回详情，避免一次无参调用把所有长描述塞进模型上下文。"""
    selected = select_node_types(["browser.extract", "file.write", "missing.type"])
    assert [entry["type"] for entry in selected["node_types"]] == ["browser.extract", "file.write"]
    assert selected["unknown_types"] == ["missing.type"]

    index = select_node_types(None)
    assert index["node_types"] == []
    assert index["available_types"] == [entry["type"] for entry in NODE_TYPE_CATALOG]


async def test_eval_mock_filters_the_real_node_catalog() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evals.run_evals import MockToolExecutor

    result = await MockToolExecutor().execute(
        "list_node_types", {"types": ["browser.extract", "file.write"]}
    )
    assert [entry["type"] for entry in result["node_types"]] == ["browser.extract", "file.write"]


def test_count_variable_is_derived_from_output_variable() -> None:
    """行数是判断「抓到没抓到、抓全没抓全」的唯一线索，名字无从选错，就别让调用方补。"""
    nodes = _normalize_generated_nodes([
        {"id": "n1", "type": "browser.extract", "selector": "tbody tr",
         "extractMode": "table", "outputVariable": "order_rows"},
        {"id": "n2", "type": "browser.paginateNext", "targetSelector": "tbody tr",
         "extractMode": "table", "outputVariable": "${var.all_rows}"},
        # 已经写了就别覆盖
        {"id": "n3", "type": "browser.extract", "selector": "tbody tr",
         "outputVariable": "rows", "countVariable": "my_count"},
        # count 模式本来就以 countVariable 为主输出，没有 outputVariable 可派生
        {"id": "n4", "type": "browser.extract", "selector": "input[type='password']",
         "extractMode": "count", "countVariable": "login_count"},
    ])
    by_id = {n["id"]: n for n in nodes}

    assert by_id["n1"]["countVariable"] == "order_rows_count"
    # 模板先被还原，不能派生出 `${var.all_rows}_count`
    assert by_id["n2"]["countVariable"] == "all_rows_count"
    assert by_id["n3"]["countVariable"] == "my_count"
    assert by_id["n4"]["countVariable"] == "login_count"

    # 补齐之后 lint 不该再唠叨
    assert "table_extract_missing_count" not in {
        f["issue"] for f in _lint_flow(nodes, [])
    }


def test_generating_a_format_the_environment_cannot_produce_is_blocked_before_running() -> None:
    """缺库不会让脚本报错，它会手拼字节流跑成 success，坏在用户打开的那一刻。

    所以只能在运行前拦，且必须是 error：warn 拦不住 run_flow。
    """
    nodes = [{
        "id": "n4_pdf", "type": "script.python", "title": "生成总结PDF",
        "code": "pdf_path = out_dir / f'summary_{ts}.pdf'\npdf_path.write_bytes(pdf)",
    }]

    findings = _lint_unavailable_artifact_format(nodes)

    assert [f["issue"] for f in findings] == ["unavailable_artifact_format"]
    assert findings[0]["severity"] == "error"
    assert "reportlab" in findings[0]["message"]
    assert "告诉用户" in findings[0]["fix"], "出路要写明「问用户」，否则模型只会换个写法再拼一次"


def test_installed_library_formats_and_downloads_are_left_alone() -> None:
    """xlsx 有 openpyxl 就该放行；把已有的 PDF 下载下来是传输，不需要任何库。

    这两类误报的代价是把本来能跑的流程判死，比漏报更贵。
    """
    excel = [{"id": "n1", "type": "script.python", "code": "wb.save(out / 'report.xlsx')"}]
    download = [{
        "id": "n2", "type": "script.python",
        "code": "import requests\nopen(out / 'spec.pdf', 'wb').write(requests.get(url).content)",
    }]

    assert _lint_unavailable_artifact_format(excel) == []
    assert _lint_unavailable_artifact_format(download) == []


def test_capability_blurb_names_both_what_works_and_what_does_not() -> None:
    """只说「可以执行 Python」等于没给边界，模型只能靠猜——它猜错的代价用户才看得见。"""
    blurb = describe_script_capabilities()

    assert ".xlsx" in blurb and "openpyxl" in blurb
    assert ".pdf" in blurb and "reportlab" in blurb
    assert "告诉用户" in blurb
    # 语义加工的边界和格式的边界同等重要：不写，模型就拿切句子冒充总结
    assert "语义加工" in blurb and "原文摘录" in blurb


def test_claiming_a_summary_without_any_model_node_is_blocked_before_running() -> None:
    """事故复盘：交付的「## 生成总结」是回复列表前 8 条原文逐字，助手回「已验收通过」。

    缺的是能力不是语法：切前 8 句照样 success、文件非空、内容也确实来自抓取数据，
    所以判据只能放在运行之前，且必须是 error——warn 拦不住 run_flow。
    """
    node = {
        "id": "n4_pdf",
        "type": "script.python",
        "title": "生成总结MD",
        "description": "基于采集文本生成 Markdown 总结文件",
        "outputVariable": "md_path",
    }

    findings = _lint_claimed_semantic_capability([node])

    assert [f["issue"] for f in findings] == ["claimed_semantic_capability_unavailable"]
    assert findings[0]["severity"] == "error"
    assert "由用户决定" in findings[0]["fix"], "出路必须包含停下来问用户，否则模型只会换个写法再切一次"


def test_honest_rule_based_wording_is_left_alone() -> None:
    """能力就是「原文摘录」，说成原文摘录不算冒充；这条规则管的是说法与实做不符。"""
    honest = {
        "id": "n4",
        "type": "script.python",
        "title": "生成原文摘录MD",
        "description": "把采集到的回复按原文摘录写成 Markdown",
        "outputVariable": "md_path",
    }
    unrelated = {"id": "n5", "type": "script.python", "title": "导出明细表", "outputVariable": "csv_path"}

    assert _lint_claimed_semantic_capability([honest]) == []
    assert _lint_claimed_semantic_capability([unrelated]) == []


def test_the_same_claim_is_caught_whichever_node_type_assembles_it() -> None:
    """闸门不能只认脚本节点：模型用哪种节点拼装产物是随机的，用户拿到的东西一样。

    只白名单 script.* + file.write 的话，同一句「生成总结」换成 data.string.transform
    或 excel.write 就直接放行——闸门强弱取决于模型的节点偏好，正是要抹平的差异。
    """
    disguises = [
        {"id": "a", "type": "data.string.transform", "title": "生成总结", "outputVariable": "digest"},
        {"id": "b", "type": "excel.write", "title": "写入报表", "sheetName": "内容摘要"},
        {"id": "c", "type": "variable.set", "title": "汇总", "variableName": "summary_text"},
        {"id": "d", "type": "file.write", "title": "输出", "path": "out/总结.md"},
    ]

    for node in disguises:
        findings = _lint_claimed_semantic_capability([node])
        assert [f["issue"] for f in findings] == ["claimed_semantic_capability_unavailable"], node["type"]


def test_reading_something_that_is_already_a_summary_is_not_a_claim() -> None:
    """browser/ui/只读节点上的「总结」在描述读到的东西，不是声称自己加工出来的。"""
    readers = [
        {"id": "a", "type": "browser.extract", "title": "提取页面总结区域", "outputVariable": "summary_block"},
        {"id": "b", "type": "file.read", "title": "读取上季度总结文档", "outputVariable": "last_summary"},
        {"id": "c", "type": "control.foreach", "title": "遍历每篇摘要"},
    ]

    assert _lint_claimed_semantic_capability(readers) == []


def test_semantic_node_types_stay_in_sync_with_the_catalog() -> None:
    """catalog 里出现了会调模型的节点，能力声明必须同时更新。

    catalog 在导入期就要调 script_capabilities 拼节点说明，反过来引 catalog 会成环，
    两处一致只能靠这条测试守：漏改不会报错，只会让闸门在能力已经有了之后继续拦。
    """
    in_catalog = {
        str(entry.get("type"))
        for entry in NODE_TYPE_CATALOG
        if str(entry.get("type", "")).startswith(SEMANTIC_NODE_PREFIXES)
    }

    assert in_catalog == set(semantic_rewrite_node_types())


def test_error_message_literals_are_not_read_as_hardcoded_deliverable_content() -> None:
    """`raise SystemExit('未提取到内容…')` 只在数据为空时出现，写死是对的。

    误报会跟着每一次 lint/apply_node_fix/assert 反复回给模型，教它整体忽略 lint 结论。
    """
    error_path = {
        "id": "n4",
        "title": "生成总结MD",
        "type": "script.python",
        "code": "x = _vars['a']\nif not x:\n    raise SystemExit('未提取到帖子内容，无法生成 Markdown 总结')\n",
    }
    hardcoded = {
        "id": "n5",
        "title": "生成总结MD",
        "type": "script.python",
        "code": "print('本季度营收同比增长 12%，主要来自华东区域的渠道扩张。')\n",
    }

    assert _lint_script_hardcoded_content([error_path]) == []
    assert [f["issue"] for f in _lint_script_hardcoded_content([hardcoded])] == [
        "script_hardcoded_prose_literal"
    ], "stdout 是 script 节点的交付通道，那里的固定长文本仍必须报"


def test_error_literal_exemption_holds_across_script_channels() -> None:
    """这条规则跑在三种脚本通道上，豁免却只写了 Python 写法。

    结果是同一段逻辑用 script.javascript 写就误报——闸门的宽严取决于模型选了哪种语言。
    """
    javascript = {
        "id": "n4",
        "title": "生成报表",
        "type": "script.javascript",
        "code": "if (!rows.length) {\n  throw new Error('未提取到帖子内容，无法生成报表。');\n}\n",
    }
    shell = {
        "id": "n5",
        "title": "生成报表",
        "type": "script.shell",
        "code": 'test -s "$IN" || { echo "输入文件为空，无法生成报表。" >&2; exit 1; }\n',
    }

    assert _lint_script_hardcoded_content([javascript]) == []
    assert _lint_script_hardcoded_content([shell]) == []


def test_incomplete_sweep_is_detected_when_paginate_output_equals_upstream_extract() -> None:
    """翻页输出与上游提取逐字相同 = 一页都没翻。

    这类残缺不报错：success、变量非空、行数正常，只是少了第 2 页往后的全部数据。
    """
    nodes = [
        {"id": "n3_extract", "type": "browser.extract", "selector": "#Main .reply_content",
         "outputVariable": "topic_texts"},
        {"id": "n3_paginate", "type": "browser.paginateNext", "selector": "a.normal_page_right",
         "targetSelector": "#Main .reply_content", "outputVariable": "paged_topic_texts"},
    ]
    page_one = ["回复 1", "回复 2", "回复 3"]

    findings = _find_incomplete_sweeps(nodes, {
        "topic_texts": page_one,
        "paged_topic_texts": list(page_one),
    })

    assert [f["issue"] for f in findings] == ["sweep_never_advanced"]
    assert findings[0]["node_id"] == "n3_paginate"
    assert "a.normal_page_right" in findings[0]["fix"]


def test_incomplete_sweep_is_not_reported_when_pagination_actually_collected_more() -> None:
    """真翻到第 2 页就不能报警：误报会把助手推去改一个本来正确的 selector。"""
    nodes = [
        {"id": "n1", "type": "browser.extract", "outputVariable": "page_one"},
        {"id": "n2", "type": "browser.paginateNext", "outputVariable": "all_pages"},
    ]

    findings = _find_incomplete_sweeps(nodes, {
        "page_one": ["A", "B"],
        "all_pages": ["A", "B", "C", "D"],
    })

    assert findings == []


def test_findings_that_will_block_a_run_say_so_in_the_tool_result() -> None:
    """阻断名单在编排层，模型只看得到 severity——warn 级的阻断项必须自报家门。

    否则它读到「1 个警告」，合理地判断可以先跑一次看看，然后被阻断诊断拦在
    run_flow 上：这一轮既没跑成也没修成，而它手上没有任何字段能让它提前避开。
    """
    from app.services.ai_tools.lint import annotate_lint_findings

    marked, text = annotate_lint_findings([
        {"issue": "client_side_filter_masks_page_filter", "severity": "warn", "node_id": "n2"},
        {"issue": "long_wait_timeout", "severity": "warn", "node_id": "n3"},
    ])

    assert [f["blocks_run"] for f in marked] == [True, False]
    assert "blocks_run=true" in text


def test_advisory_only_findings_do_not_read_as_a_blocked_run() -> None:
    """全是建议项时不能说「会被阻断」，不然模型会去修一堆压根不挡路的东西。"""
    from app.services.ai_tools.lint import annotate_lint_findings

    marked, text = annotate_lint_findings([{"issue": "long_wait_timeout", "severity": "warn"}])

    assert marked[0]["blocks_run"] is False
    assert "blocks_run=true" not in text


def test_extracting_the_whole_page_is_blocked_before_the_run() -> None:
    """selector=body 抓回来的是整页文本，正文只占其中一小段。

    这条必须拦在运行前：整页抽取永远"成功"，坏处要等用户打开产物才发现，
    而那时的补救通常是加一个下游脚本去洗一段本来就洗不动的连续文本。
    """
    nodes = [
        {"id": "start", "type": "start"},
        {"id": "n4", "type": "browser.extract", "selector": "body",
         "extractMode": "text", "outputVariable": "post_texts"},
        {"id": "end", "type": "end"},
    ]
    edges = [{"source": "start", "target": "n4"}, {"source": "n4", "target": "end"}]

    findings = _lint_flow(nodes, edges)
    hit = [f for f in findings if f["issue"] == "extract_scope_is_page_root"]

    assert [f["severity"] for f in hit] == ["error"]
    assert "inspect_page" in hit[0]["fix"]


def test_page_root_inside_an_extract_union_is_still_the_whole_page() -> None:
    """并集里只要有 body，收窄其余项就没有意义——它把其余项全包住了。"""
    nodes = [
        {"id": "n4", "type": "ui.extract", "selector": ".post-content, body",
         "outputVariable": "rows"},
    ]

    findings = _lint_flow(nodes, [])
    hit = [f for f in findings if f["issue"] == "extract_scope_is_page_root"]

    assert len(hit) == 1
    assert "包住了其余项" in hit[0]["message"]


def test_narrow_extract_selector_is_left_alone() -> None:
    """误报会教模型整体忽略 lint 结论，正常的正文 selector 一条都不能碰。"""
    nodes = [
        {"id": "n4", "type": "browser.extract", "selector": ".post-content .content",
         "extractMode": "text", "outputVariable": "post_texts"},
    ]

    findings = _lint_flow(nodes, [])

    assert not [f for f in findings if f["issue"] == "extract_scope_is_page_root"]


def test_wait_with_a_body_fallback_is_reported_as_no_wait() -> None:
    """`article, main, body` 这种兜底写法让等待恒成立，下游会在页面没就绪时开抓。"""
    nodes = [
        {"id": "n3", "type": "browser.wait", "selector": "article, main, .post, body",
         "timeoutMs": 30000},
    ]

    findings = _lint_flow(nodes, [])
    hit = [f for f in findings if f["issue"] == "wait_selector_is_page_root"]

    assert [f["severity"] for f in hit] == ["error"]
    assert "立即成立" in hit[0]["message"]


def test_waiting_for_the_page_root_to_disappear_reads_as_a_dead_wait() -> None:
    """同一个 selector 在 hidden 条件下是另一种坏法：永远等不到，只会耗光 timeout。"""
    nodes = [
        {"id": "n3", "type": "browser.waitFor", "selector": "body", "waitCondition": "hidden"},
    ]

    hit = [f for f in _lint_flow(nodes, []) if f["issue"] == "wait_selector_is_page_root"]

    assert "永远等不到" in hit[0]["message"]


def test_dismiss_and_press_may_still_target_the_page_root() -> None:
    """Escape 就是要打在 body 上（另一条 lint 的 fix 明说了这么写），别把它一起拦了。"""
    nodes = [
        {"id": "n2", "type": "browser.press", "selector": "body", "inputValue": "Escape"},
        {"id": "n3", "type": "browser.dismiss", "selector": "body"},
    ]

    findings = _lint_flow(nodes, [])

    assert not [f for f in findings if f["issue"].endswith("is_page_root")]


def test_cleanup_script_that_deletes_nothing_fails_the_audit() -> None:
    """"清洗"跑通了但一个字符没删——运行状态、变量、产物全绿，只有体量对比看得出来。"""
    raw = "导航 登录 注册\n" + "帖子正文内容。" * 400
    nodes = [
        {"id": "n4_extract", "type": "browser.extract", "selector": "body",
         "outputVariable": "post_texts", "firstValueVariable": "post_text"},
        {"id": "n6_clean", "type": "script.python", "outputVariable": "cleaned_post_json",
         "code": "..."},
    ]

    findings = _find_ineffective_transforms(nodes, {
        "post_texts": [raw],
        "post_text": raw,
        "cleaned_post_json": raw,
    })

    assert [f["issue"] for f in findings] == ["transform_had_no_effect"]
    assert findings[0]["input_variable"] in {"post_text", "post_texts"}
    assert "get_run_output" in findings[0]["fix"]


def test_a_transform_that_really_strips_the_noise_passes() -> None:
    nodes = [
        {"id": "n4_extract", "type": "browser.extract", "outputVariable": "post_text"},
        {"id": "n6_clean", "type": "script.python", "outputVariable": "cleaned"},
    ]
    raw = "导航 登录 注册 页脚 版权\n" + "帖子正文内容。" * 400

    findings = _find_ineffective_transforms(nodes, {
        "post_text": raw,
        "cleaned": "帖子正文内容。" * 200,
    })

    assert findings == []


def test_one_extract_nodes_own_two_variables_are_not_compared_with_each_other() -> None:
    """outputVariable 与 firstValueVariable 装的是同一份内容，互比必然"无变化"。"""
    raw = "帖子正文内容。" * 400
    nodes = [
        {"id": "n4", "type": "browser.extract", "outputVariable": "texts",
         "firstValueVariable": "text"},
    ]

    assert _find_ineffective_transforms(nodes, {"texts": [raw], "text": raw}) == []


def test_describing_a_cleanup_as_done_needs_run_evidence():
    """清洗类需求的失败是静默的，用户只能从这段回复判断做没做成。"""
    state = GuardState()
    _orchestrator_guard_after_tool(
        "update_flow",
        {"status": "applied", "changed_nodes": [
            {"id": "n6_clean", "type": "script.python", "change": "added"},
        ], "events": [{
            "type": "flow_written",
            "revision": 2,
            "affected_nodes": [{"id": "n6_clean", "type": "script.python", "change": "added"}],
        }]},
        state,
    )

    correction = _overstated_result_claim(
        "已新增清理步骤，会去除导航词、Cloudflare 提示和重复行。", state
    )

    assert correction is not None
    assert "get_run_output" in correction


def test_the_same_wording_is_fine_when_no_transform_node_was_touched():
    """只改了 selector 却说「去掉了…」说的是流程不是数据，别把正常表述也撤回。"""
    state = GuardState()
    _orchestrator_guard_after_tool(
        "update_flow",
        {"status": "applied", "changed_nodes": [
            {"id": "n3_wait", "type": "browser.wait", "change": "updated"},
        ]},
        state,
    )

    assert _overstated_result_claim("已去掉等待节点里的 body 兜底。", state) is None


def test_cleanup_wording_is_allowed_once_the_audit_has_passed():
    state = GuardState()
    _orchestrator_guard_after_tool(
        "update_flow",
        {"status": "applied", "changed_nodes": [{"id": "n6", "type": "script.python"}]},
        state,
    )
    _orchestrator_guard_after_tool("run_flow", {"status": "success"}, state)
    _orchestrator_guard_after_tool(
        "run_flow", {"status": "success", "acceptance_audit": {"passed": True, "issues": []}}, state
    )

    assert _overstated_result_claim("已去除页面导航与样式表噪声，正文从 11.8 万字符降到 4200 字符。", state) is None


async def test_execute_rejects_invalid_arguments_before_side_effects() -> None:
    executor = RpaToolExecutor(SimpleNamespace(), SimpleNamespace())
    cases = [
        ("run_flow", {"arguments": {"flow_id": "f"}}, "additionalProperties"),
        ("run_flow", {}, "required"),
        ("run_flow", {"flow_id": 7}, "type"),
        ("run_flow", {"flow_id": "f", "progress_sink": {}}, "additionalProperties"),
        ("interact_page", {"action": "not-an-action"}, "enum"),
        ("list_node_types", {"types": []}, "minItems"),
        ("update_flow", {"flow_id": "f", "update_nodes": [{"id": "n", "patch": "secret-value"}]}, "type"),
        ("inspect_page", [], "type"),
    ]
    for name, args, rule in cases:
        result = await executor.execute(name, args)
        assert result["status"] == "error"
        assert result["error"] == "invalid_arguments"
        assert result["tool"] == name
        assert any(issue["rule"] == rule for issue in result["issues"])
        assert "secret-value" not in json.dumps(result)


async def test_execute_keeps_valid_parameters_and_internal_reads(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    executor = RpaToolExecutor(SimpleNamespace(), SimpleNamespace())
    run = AsyncMock(return_value={"status": "success"})
    read = AsyncMock(return_value={"flow_id": "f"})
    monkeypatch.setattr(executor, "_run_flow", run)
    monkeypatch.setattr(executor, "_get_flow", read)
    progress = {}
    assert await executor.execute("run_flow", {"flow_id": "f", "variables": {"custom": 1}}, progress) == {
        "status": "success",
    }
    run.assert_awaited_once_with(flow_id="f", variables={"custom": 1}, progress_sink=progress)
    assert await executor.execute("get_flow", {"flow_id": "f"}) == {"flow_id": "f"}
    read.assert_awaited_once_with(flow_id="f")


def test_tool_arguments_never_turn_a_non_object_into_an_empty_call() -> None:
    import pytest

    for raw in ("[]", "null", "42", '\"text\"'):
        with pytest.raises(json.JSONDecodeError, match="工具参数必须是 JSON 对象"):
            _parse_tool_arguments(raw)


async def test_execute_rejects_unknown_fields_inside_acceptance_contract() -> None:
    """契约是审计引擎的固定读取面：多写的字段没人读，模型却以为约束已经生效。

    值不能回传：契约里装的是用户原话和页面数据，而 Pydantic 的 ValidationError 会把
    input_value 原样拼进 str(exc)，编排层对工具异常只做 {"error": str(exc)}。
    所以这一档必须在参数闸门上拦掉，不能落到 model_validate 去抛。
    """
    executor = RpaToolExecutor(SimpleNamespace(), SimpleNamespace())
    nodes = [{
        "id": "extract",
        "type": "browser.extract",
        "selector": ".rows",
        "outputVariable": "rows",
    }]

    contract_level = {**_valid_contract("rows"), "strict_mode": "leaked-value"}
    requirement_level = _valid_contract("rows")
    requirement_level["requirements"][0]["source_url"] = "leaked-value"
    deliverable_level = _valid_contract("rows")
    deliverable_level["deliverables"][0]["min_columns"] = "leaked-value"

    for label, contract in (
        ("contract", contract_level),
        ("requirement", requirement_level),
        ("deliverable", deliverable_level),
    ):
        result = await executor.execute(
            "create_flow", {"name": "订单", "nodes": nodes, "acceptance_contract": contract}
        )
        assert result["error"] == "invalid_arguments", label
        assert any(issue["rule"] == "additionalProperties" for issue in result["issues"]), label
        assert "leaked-value" not in json.dumps(result, ensure_ascii=False), label

    # 同一个契约对象换 set_acceptance_contract 进来必须一样拦。那条路上工具 schema 故意留松
    # （复用完整契约要占 2.6k 字符预算），所以判据落在两条路共用的那一层。
    from app.services.ai_tools.executor import _validated_contract

    contract, invalid = _validated_contract(contract_level)
    assert contract is None
    assert invalid["error"] == "acceptance_contract_invalid"
    assert "leaked-value" not in json.dumps(invalid, ensure_ascii=False)

    # JSON schema 镜像不了的约束（这里是 minRows > maxRows）仍旧走 Pydantic，
    # 但同样不许以异常收场：异常会被编排层拍成 {"error": str(exc)} 连原值一起回传。
    bounds = _valid_contract("rows")
    bounds["deliverables"][0].update(kind="table", min_rows=9, max_rows=1)
    bounded = await executor.execute(
        "create_flow", {"name": "订单", "nodes": nodes, "acceptance_contract": bounds}
    )
    assert bounded["error"] == "acceptance_contract_invalid"


def test_argument_gate_keeps_nodes_patch_and_variable_dicts_open() -> None:
    """节点字段由节点类型目录决定，不由工具 schema 枚举。

    这三处一旦收紧，新增节点类型或新增配置项都要先改 schema 才调得动，
    而模型看到的只是「参数非法」——它没有任何办法自己绕过去。
    """
    nodes = [{
        "id": "extract",
        "type": "browser.extract",
        "selector": ".rows",
        "outputVariable": "rows",
        "extractMode": "table",
        "columnAliases": {"金额": "amount"},
    }]
    open_cases = [
        ("create_flow", {
            "name": "订单",
            "nodes": nodes,
            "input_variables": [
                {"name": "start_date", "type": "String", "value": "", "placeholder": "开始日期"}
            ],
            "acceptance_contract": _valid_contract("rows"),
        }),
        ("update_flow", {
            "flow_id": "f",
            "add_nodes": nodes,
            "update_nodes": [{"id": "extract", "patch": {"columnAliases": {"金额": "amount"}}}],
        }),
        ("apply_node_fix", {
            "flow_id": "f",
            "node_id": "extract",
            "config_patch": {"columnAliases": {"金额": "amount"}},
        }),
    ]
    for name, args in open_cases:
        assert validate_tool_arguments(name, args) is None, name



async def test_inspect_page_continues_static_snapshot_without_refetch(monkeypatch):
    from scrapling.engines.toolbelt.custom import Response
    import app.services.ai_tools.static_page_probe as probe
    import app.services.ai_tools.static_page_content as content

    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=FakeTaskManager())
    calls = []

    async def blocked_browser(*args):
        calls.append("browser")
        return {"_browser_blocked": {"kind": "http", "status": "blocked", "http_status": 403}}

    async def fetch(url):
        calls.append("http")
        return Response(url=url, content='<article id="article">' + ''.join(
            f'<p>段落{i:02d}' + '正文' * 25 + '</p>' for i in range(8)
        ) + '</article><aside id="aside"><p>旁栏内容</p></aside>',
            status=200, reason="OK", cookies={}, headers={}, request_headers={})

    monkeypatch.setattr(content, "CONTENT_BUDGET", 100)
    monkeypatch.setattr(executor, "_inspect_page_via_browser", blocked_browser)
    monkeypatch.setattr(probe, "_fetch_static_page", fetch)
    first = await executor.execute("inspect_page", {"url": "https://example.com/", "scope_selector": "#article"})
    assert first["truncated"] is True
    second = await executor.execute("inspect_page", {"snapshot_id": first["snapshot_id"], "cursor": first["next_cursor"]})
    assert second["status"] == "success"
    assert "段落01" in second["page_text_sample"]
    scoped = await executor.execute("inspect_page", {"snapshot_id": first["snapshot_id"], "scope_selector": "#aside"})
    assert scoped["page_text_sample"] == "旁栏内容"
    assert calls == ["browser", "http"]
    invalid = await executor.execute("inspect_page", {"cursor": first["next_cursor"]})
    assert invalid["status"] == "error"
    invalid = await executor.execute("inspect_page", {"snapshot_id": first["snapshot_id"], "url": "https://example.com/"})
    assert invalid["status"] == "error"
    assert calls == ["browser", "http"]
    await executor.execute("inspect_page", {"url": "https://example.com/"})
    stale = await executor.execute("inspect_page", {"snapshot_id": first["snapshot_id"]})
    assert stale["status"] == "error"
    assert calls == ["browser", "http", "browser", "http"]


async def test_static_scope_error_is_not_reported_as_page_access_denied(monkeypatch):
    from scrapling.engines.toolbelt.custom import Response
    import app.services.ai_tools.static_page_probe as probe

    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=FakeTaskManager())

    async def blocked_browser(*args):
        return {"_browser_blocked": {"kind": "http", "http_status": 403}}

    async def fetch(url):
        return Response(url=url, content='<article><p>业务正文</p></article>',
            status=200, reason="OK", cookies={}, headers={}, request_headers={})

    monkeypatch.setattr(executor, "_inspect_page_via_browser", blocked_browser)
    monkeypatch.setattr(probe, "_fetch_static_page", fetch)
    result = await executor.execute("inspect_page", {"url": "https://example.com/", "scope_selector": "#missing"})
    assert result["status"] == "error"
    assert "scope_selector" in result["error"]
    assert result["snapshot_id"]
    recovered = await executor.execute("inspect_page", {"snapshot_id": result["snapshot_id"], "scope_selector": "article"})
    assert recovered["status"] == "success"
