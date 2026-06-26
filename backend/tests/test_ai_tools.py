from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.schemas import FlowSnapshot, RuntimeProgress, TaskLogEntry, TaskSnapshot
from app.services.ai_orchestrator import _orchestrator_guard_after_tool, _orchestrator_guard_before_tool
from app.services.ai_tools import RpaToolExecutor, _lint_flow, _normalize_generated_edges, _normalize_generated_nodes


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
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.started = False
        self.tasks = [
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
        return self.tasks[0]


async def test_run_flow_blocks_after_repeated_similar_failures() -> None:
    task_manager = FakeTaskManager()
    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._run_flow("flow-1")

    assert result["status"] == "blocked_by_failure_budget"
    assert result["recent_failed_nodes"] == ["n13", "n13", "n13"]
    assert task_manager.started is False


async def test_get_run_error_returns_root_cause_hints_for_login_detection_failure() -> None:
    task_manager = FakeTaskManager()
    executor = RpaToolExecutor(flow_service=FakeFlowService(), task_manager=task_manager)  # type: ignore[arg-type]

    result = await executor._get_run_error("task-0")

    assert result["failed_node_id"] == "n13"
    assert result["root_cause_hints"][0]["type"] == "login_detection_may_have_skipped_login"


def test_lint_flow_blocks_single_navigation_login_flow() -> None:
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
    single_nav = next(finding for finding in findings if finding["issue"] == "single_navigation_node")

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

    findings = _lint_flow(nodes, edges, input_variable_names=["username"])
    issues = {finding["issue"] for finding in findings}

    assert "variable_name_field_uses_template" not in issues
    assert "condition_expression_uses_template" not in issues


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


def test_navigation_failure_budget_blocks_repeated_navigation_selector_patches() -> None:
    state = {
        "requires_inspect_page": None,
        "requires_quality_fix": None,
        "requires_lint_fix": None,
        "navigation_failure_counts": {},
        "navigation_budget_lock": None,
        "quality_issue_counts": {},
        "quality_budget_lock": None,
    }
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

    _orchestrator_guard_after_tool("get_run_error", result, state)
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is not None  # requires inspect_page first
    _orchestrator_guard_after_tool("inspect_page", {"url": "https://example.com/#/index"}, state)
    assert _orchestrator_guard_before_tool("run_flow", {}, state) is None

    _orchestrator_guard_after_tool("get_run_error", result, state)
    blocked = _orchestrator_guard_before_tool("run_flow", {}, state)

    assert blocked is not None
    assert blocked["required_action"] == "needs_user_navigation_target"
    assert blocked["navigation_budget_lock"]["node_id"] == "nav_menu"
    assert "完整浏览器 URL" in blocked["user_message"]
    assert blocked["needed_from_user"]
