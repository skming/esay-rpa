from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.models.schemas import AnalyzeSiteRequest
from app.main import app
from app.services.site_analyzer import SiteAnalyzer


async def restart_global_workers() -> None:
    import app.main as main_module

    await main_module.task_manager.stop_workers()
    main_module.task_manager.start_workers()


async def test_health_and_code_generation_endpoints() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        health_response = await client.get("/api/health")
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "ok"

        queue_response = await client.get("/api/queue")
        assert queue_response.status_code == 200
        assert queue_response.json()["backend"] == "memory"
        assert queue_response.json()["concurrency"] >= 1

        script_response = await client.post(
            "/api/code/generate",
            json={
                "flowName": "订单自动处理",
                "flowDefinition": {
                    "nodes": [
                        {"id": "start", "type": "start", "title": "开始"},
                        {"id": "open", "type": "browser.open", "title": "打开页面", "targetUrl": "https://quotes.toscrape.com/"},
                        {"id": "extract", "type": "browser.extract", "title": "提取文本", "selector": ".quote .text::text"},
                    ],
                    "edges": [
                        {"source": "start", "target": "open"},
                        {"source": "open", "target": "extract"},
                    ],
                },
            },
        )
        assert script_response.status_code == 200
        payload = script_response.json()
        assert payload["language"] == "python"
        assert "scrapling.fetchers" in payload["content"]


async def test_site_analyze_endpoint_returns_selector_risk(monkeypatch) -> None:
    class FakeAnalyzer:
        async def analyze(self, request: AnalyzeSiteRequest):
            return SiteAnalyzer().analyze_html(
                html_text="""
                <html>
                  <head><title>分析测试</title></head>
                  <body>
                    <button data-testid="submit-order" class="css-a1b2c3">提交</button>
                    <span class="css-987def">完成</span>
                  </body>
                </html>
                """,
                request=request,
            )

    import app.main as main_module

    monkeypatch.setattr(main_module, "site_analyzer", FakeAnalyzer())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/site/analyze",
            json={
                "targetUrl": "https://example.com/",
                "selector": ".css-a1b2c3",
                "maxCandidates": 4,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "分析测试"
    assert payload["riskLevel"] == "medium"
    assert payload["checkedSelector"]["matchCount"] == 1
    assert payload["candidates"][0]["selector"] == 'button[data-testid="submit-order"]'


async def test_flow_crud_endpoints() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        create_response = await client.post(
            "/api/flows",
            json={
                "name": "API 流程",
                "version": "v1.0.0",
                "description": "通过 API 保存",
                "status": "draft",
                "definition": {
                    "nodes": [{"id": "start", "type": "start"}],
                    "edges": [],
                },
            },
        )
        assert create_response.status_code == 200
        flow = create_response.json()
        flow_id = flow["flowId"]
        assert flow["name"] == "API 流程"

        list_response = await client.get("/api/flows")
        assert list_response.status_code == 200
        assert any(item["flowId"] == flow_id for item in list_response.json())

        update_response = await client.patch(f"/api/flows/{flow_id}", json={"version": "v1.1.0", "status": "active"})
        assert update_response.status_code == 200
        assert update_response.json()["version"] == "v1.1.0"
        assert update_response.json()["status"] == "active"

        archive_response = await client.post(f"/api/flows/{flow_id}/archive")
        assert archive_response.status_code == 200
        assert archive_response.json()["status"] == "archived"

        delete_response = await client.delete(f"/api/flows/{flow_id}")
        assert delete_response.status_code == 200
        assert delete_response.json()["deleted"] is True


async def test_flow_run_endpoint_starts_task_from_definition() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        try:
            create_response = await client.post(
                "/api/flows",
                json={
                    "name": "可运行流程",
                    "version": "v1.0.0",
                    "status": "active",
                    "definition": {
                        "nodes": [
                            {"id": "start", "type": "start"},
                            {
                                "id": "n1",
                                "type": "browser.fetch",
                                "targetUrl": "https://quotes.toscrape.com/",
                                "selector": ".quote .text::text",
                                "fetcher": "static",
                                "extractMode": "text",
                                "timeoutMs": 1000,
                            },
                        ],
                        "edges": [{"source": "start", "target": "n1"}],
                    },
                },
            )
            assert create_response.status_code == 200
            flow_id = create_response.json()["flowId"]

            run_response = await client.post(f"/api/flows/{flow_id}/run", json={"mode": "debug"})
            assert run_response.status_code == 200
            payload = run_response.json()
            assert payload["flowId"] == flow_id
            assert payload["flowName"] == "可运行流程"
            assert payload["mode"] == "debug"
        finally:
            await restart_global_workers()


async def test_task_endpoint_accepts_request_and_exposes_logs() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        task_response = await client.post(
            "/api/tasks",
            json={
                "flowName": "测试流程",
                "flowId": "00000000-0000-0000-0000-000000000101",
                "targetUrl": "https://quotes.toscrape.com/",
                "selector": ".quote .text::text",
                "flowDefinition": {
                    "nodes": [
                        {"id": "start", "type": "start"},
                        {"id": "n2", "type": "control.step"},
                        {
                            "id": "n3",
                            "type": "browser.fetch",
                            "targetUrl": "https://quotes.toscrape.com/",
                            "selector": ".quote .author::text",
                            "fetcher": "static",
                            "extractMode": "text",
                            "timeoutMs": 1000,
                        },
                    ],
                    "edges": [{"source": "start", "target": "n2"}, {"source": "n2", "target": "n3"}],
                },
                "scope": "from-selection",
                "startNodeId": "n3",
                "failureStrategy": "continue",
                "screenshot": False,
                "concurrency": 3,
                "timeoutMs": 1000,
            },
        )
        assert task_response.status_code == 200
        task_payload = task_response.json()
        task_id = task_payload["taskId"]
        assert task_payload["flowId"] == "00000000-0000-0000-0000-000000000101"
        assert task_payload["runConfig"] == {
            "scope": "from-selection",
            "startNodeId": "n3",
            "failureStrategy": "continue",
            "screenshot": False,
            "concurrency": 3,
        }

        for _ in range(30):
            snapshot_response = await client.get(f"/api/tasks/{task_id}")
            assert snapshot_response.status_code == 200
            if snapshot_response.json()["status"] in {"success", "error"}:
                break
            await asyncio.sleep(0.05)

        logs_response = await client.get(f"/api/tasks/{task_id}/logs")
        assert logs_response.status_code == 200
        assert len(logs_response.json()) >= 1
        assert any(log.get("nodeId") in {"start", "n1", "end"} for log in logs_response.json())
        assert any("运行配置" in log["message"] and "起点 n3" in log["message"] for log in logs_response.json())

        artifacts_response = await client.get(f"/api/tasks/{task_id}/artifacts")
        assert artifacts_response.status_code == 200
        if snapshot_response.json()["status"] == "success":
            assert len(artifacts_response.json()) >= 1
            artifact_id = artifacts_response.json()[0]["artifactId"]
            assert artifacts_response.json()[0]["metadata"]["run_scope"] == "from-selection"
            assert artifacts_response.json()[0]["metadata"]["start_node_id"] == "n3"
            assert artifacts_response.json()[0]["metadata"]["failure_strategy"] == "continue"
            assert artifacts_response.json()[0]["metadata"]["screenshot"] is False
            assert artifacts_response.json()[0]["metadata"]["concurrency"] == 3
            assert artifacts_response.json()[0]["metadata"]["selector"] == ".quote .author::text"
            artifact_content_response = await client.get(f"/api/tasks/{task_id}/artifacts/{artifact_id}")
            assert artifact_content_response.status_code == 200
            assert "values" in artifact_content_response.json()["content"]


async def test_task_debug_endpoint_continues_paused_breakpoint() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        try:
            task_response = await client.post(
                "/api/tasks",
                json={
                    "flowName": "API 断点调试流程",
                    "targetUrl": "https://quotes.toscrape.com/",
                    "selector": ".quote .text::text",
                    "mode": "debug",
                    "flowDefinition": {
                        "nodes": [
                            {"id": "start", "type": "start"},
                            {
                                "id": "set-name",
                                "title": "设置调试变量",
                                "type": "variable.set",
                                "variableName": "debug_name",
                                "value": "alice",
                                "breakpoint": True,
                            },
                        ],
                        "edges": [{"source": "start", "target": "set-name"}],
                    },
                    "timeoutMs": 1000,
                },
            )
            assert task_response.status_code == 200
            task_id = task_response.json()["taskId"]

            for _ in range(40):
                logs_response = await client.get(f"/api/tasks/{task_id}/logs")
                assert logs_response.status_code == 200
                if any("命中断点" in log["message"] for log in logs_response.json()):
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("调试任务未命中断点")

            debug_response = await client.post(f"/api/tasks/{task_id}/debug", json={"command": "continue"})
            assert debug_response.status_code == 200
            assert any(variable["name"] == "debug_command" for variable in debug_response.json()["variables"])

            for _ in range(40):
                snapshot_response = await client.get(f"/api/tasks/{task_id}")
                assert snapshot_response.status_code == 200
                if snapshot_response.json()["status"] == "success":
                    break
                await asyncio.sleep(0.05)
            assert snapshot_response.json()["status"] == "success"
            assert any(variable["name"] == "debug_name" and variable["value"] == "alice" for variable in snapshot_response.json()["variables"])
            variables_response = await client.get(f"/api/tasks/{task_id}/variables")
            assert variables_response.status_code == 200
            assert any(variable["name"] == "debug_name" and variable["value"] == "alice" for variable in variables_response.json())
            missing_variables_response = await client.get("/api/tasks/missing-task/variables")
            assert missing_variables_response.status_code == 404
        finally:
            await restart_global_workers()


async def test_schedule_crud_and_manual_trigger_endpoints() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        try:
            create_response = await client.post(
                "/api/schedules",
                json={
                    "name": "每小时采集",
                    "cronExpression": "0 * * * *",
                    "timezone": "Asia/Shanghai",
                    "enabled": True,
                    "task": {
                        "flowName": "调度 API 测试",
                        "targetUrl": "https://quotes.toscrape.com/",
                        "selector": ".quote .text::text",
                        "timeoutMs": 1000,
                    },
                },
            )
            assert create_response.status_code == 200
            schedule = create_response.json()
            schedule_id = schedule["scheduleId"]
            assert schedule["status"] == "enabled"
            assert schedule["nextRunAt"] is not None

            list_response = await client.get("/api/schedules")
            assert list_response.status_code == 200
            assert any(item["scheduleId"] == schedule_id for item in list_response.json())

            update_response = await client.patch(f"/api/schedules/{schedule_id}", json={"enabled": False})
            assert update_response.status_code == 200
            assert update_response.json()["status"] == "disabled"
            assert update_response.json()["nextRunAt"] is None

            trigger_response = await client.post(f"/api/schedules/{schedule_id}/trigger")
            assert trigger_response.status_code == 200
            assert trigger_response.json()["lastTaskId"] is not None

            tick_response = await client.post("/api/schedules:tick")
            assert tick_response.status_code == 200
            assert isinstance(tick_response.json(), list)

            delete_response = await client.delete(f"/api/schedules/{schedule_id}")
            assert delete_response.status_code == 200
            assert delete_response.json()["deleted"] is True
        finally:
            await restart_global_workers()


async def test_schedule_trigger_runs_bound_flow_endpoint() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        try:
            flow_response = await client.post(
                "/api/flows",
                json={
                    "name": "调度绑定流程",
                    "version": "v1.0.0",
                    "status": "active",
                    "definition": {
                        "nodes": [
                            {"id": "start", "type": "start"},
                            {
                                "id": "fetch",
                                "type": "browser.fetch",
                                "targetUrl": "https://quotes.toscrape.com/",
                                "selector": ".quote .author::text",
                                "fetcher": "static",
                                "extractMode": "text",
                                "timeoutMs": 1000,
                            },
                        ],
                        "edges": [{"source": "start", "target": "fetch"}],
                    },
                },
            )
            assert flow_response.status_code == 200
            flow_id = flow_response.json()["flowId"]

            schedule_response = await client.post(
                "/api/schedules",
                json={
                    "name": "每小时按流程定义采集",
                    "cronExpression": "0 * * * *",
                    "timezone": "UTC",
                    "enabled": True,
                    "task": {
                        "flowId": flow_id,
                        "flowName": "错误名称应被流程覆盖",
                        "targetUrl": "https://example.com/",
                        "selector": ".wrong::text",
                        "timeoutMs": 1000,
                    },
                },
            )
            assert schedule_response.status_code == 200
            schedule_id = schedule_response.json()["scheduleId"]

            trigger_response = await client.post(f"/api/schedules/{schedule_id}/trigger")
            assert trigger_response.status_code == 200
            task_id = trigger_response.json()["lastTaskId"]
            task_response = await client.get(f"/api/tasks/{task_id}")
            assert task_response.status_code == 200
            assert task_response.json()["flowId"] == flow_id
            assert task_response.json()["flowName"] == "调度绑定流程"
            runs_response = await client.get(f"/api/flows/{flow_id}/runs")
            assert runs_response.status_code == 200
            assert [run["taskId"] for run in runs_response.json()] == [task_id]
            filtered_response = await client.get(f"/api/tasks?flowId={flow_id}&limit=5")
            assert filtered_response.status_code == 200
            assert [run["taskId"] for run in filtered_response.json()] == [task_id]
        finally:
            await restart_global_workers()


def test_task_log_websocket_replays_existing_logs() -> None:
    with TestClient(app) as client:
        task_response = client.post(
            "/api/tasks",
            json={
                "flowName": "WebSocket 测试流程",
                "targetUrl": "https://quotes.toscrape.com/",
                "selector": ".quote .text::text",
                "timeoutMs": 1000,
            },
        )
        assert task_response.status_code == 200
        task_id = task_response.json()["taskId"]

        for _ in range(30):
            snapshot_response = client.get(f"/api/tasks/{task_id}")
            assert snapshot_response.status_code == 200
            logs_response = client.get(f"/api/tasks/{task_id}/logs")
            assert logs_response.status_code == 200
            if snapshot_response.json()["status"] in {"success", "error"} or len(logs_response.json()) >= 1:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("WebSocket 测试任务未在预期时间内产生日志")

        logs_response = client.get(f"/api/tasks/{task_id}/logs")
        assert logs_response.status_code == 200
        assert len(logs_response.json()) >= 1

        with client.websocket_connect(f"/ws/tasks/{task_id}/logs") as websocket:
            first_log = websocket.receive_json()
            assert first_log["taskId"] == task_id
            assert first_log["message"]


def test_openapi_schema_generates() -> None:
    """回归：路由返回注解引用了仅在函数内局部导入的名字时，Pydantic 解析失败会让
    /openapi.json 整体 500（/docs 页面随之空白）。这里守住全部路由的 schema 可生成。"""
    with TestClient(app) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        assert response.json()["paths"], "OpenAPI 未生成任何路径"
