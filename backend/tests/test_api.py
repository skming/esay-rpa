from __future__ import annotations

import asyncio
import json
import time

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.models.schemas import AnalyzeSiteRequest
from app.main import app
from app.services.site_analyzer import SiteAnalyzer


def acceptance_contract(variable: str) -> dict:
    return {
        "requirements": [{
            "id": "api-output",
            "description": "API 测试交付",
            "sourceKind": "product_default",
            "confidence": 1,
            "confirmed": True,
        }],
        "deliverables": [{
            "id": "api-result",
            "variable": variable,
            "kind": "table",
            "requirementIds": ["api-output"],
        }],
    }


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
                                "outputVariable": "api_rows",
                                "timeoutMs": 1000,
                            },
                        ],
                        "edges": [{"source": "start", "target": "n1"}],
                    },
                    "acceptanceContract": acceptance_contract("api_rows"),
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
                        {"id": "n2", "type": "control.condition"},
                        {
                            "id": "n3",
                            "type": "browser.fetch",
                            "targetUrl": "https://quotes.toscrape.com/",
                            "selector": ".quote .author::text",
                            "fetcher": "static",
                            "extractMode": "text",
                            "outputVariable": "task_rows",
                            "timeoutMs": 1000,
                        },
                    ],
                    "edges": [{"source": "start", "target": "n2"}, {"source": "n2", "target": "n3"}],
                },
                "acceptanceContract": acceptance_contract("task_rows"),
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
                        "acceptanceContract": acceptance_contract("debug_name"),
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
                                "outputVariable": "api_rows",
                                "timeoutMs": 1000,
                            },
                        ],
                        "edges": [{"source": "start", "target": "fetch"}],
                    },
                    "acceptanceContract": acceptance_contract("api_rows"),
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
                "flowDefinition": {
                    "nodes": [
                        {"id": "start", "type": "start"},
                        {
                            "id": "fetch",
                            "type": "browser.fetch",
                            "targetUrl": "https://quotes.toscrape.com/",
                            "selector": ".quote .text::text",
                            "fetcher": "static",
                            "extractMode": "text",
                            "timeoutMs": 1000,
                        },
                    ],
                    "edges": [{"source": "start", "target": "fetch"}],
                },
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


async def test_test_model_never_writes_config(monkeypatch) -> None:
    """回归：测试连接曾经先把草稿 save 进配置再读回，于是一次探测就能改配置——
    前端没回填已存 base_url 时传的空串会把它 pop 掉，掩码串会顶掉真钥。
    探测是只读的：无论请求里带什么，落盘内容必须逐字节不变。"""
    import app.main as main_module

    service = main_module.ai_config_service
    env_key = "ANTHROPIC_API_KEY"
    try:
        service.save({"api_keys": {env_key: "sk-ant-real-key-value"},
                      "base_urls": {env_key: "https://relay.example.com/v1"}})
        before = json.dumps(service.load(), sort_keys=True)

        # 让探测在选模型阶段就返回，测试关心的是落盘副作用而非网络结果；
        # 改造前的 save 发生在这一步之前，所以这里照样能抓住回归
        monkeypatch.setattr(service, "get_model_catalog", lambda: [])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            # 空 base_url + 掩码密钥：改造前这两个字段一个清空 relay 地址、一个顶掉真钥
            response = await client.post("/api/ai/test-model", json={
                "env_key": env_key, "model": "claude-sonnet-5",
                "api_key": "sk-a****alue", "base_url": "",
            })

        assert response.status_code == 200
        assert json.dumps(service.load(), sort_keys=True) == before, "测试连接写了配置"
    finally:
        service.save({"api_keys": {env_key: ""}, "base_urls": {env_key: ""}})


async def test_test_model_falls_back_to_the_stored_key(monkeypatch) -> None:
    """前端不再回传掩码，未重新输入时 api_key 是空串——这不代表「没有密钥」，
    而是「用已经存着的那把测」。回退丢了的话，已配置的服务商会报未配置或拿占位符去鉴权。"""
    import litellm

    import app.main as main_module

    service = main_module.ai_config_service
    env_key = "ANTHROPIC_API_KEY"
    seen: dict[str, object] = {}
    try:
        service.save({"api_keys": {env_key: "sk-ant-stored-key"}})

        async def fake_completion(**kwargs):
            seen.update(kwargs)
            return None

        monkeypatch.setattr(litellm, "acompletion", fake_completion)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post("/api/ai/test-model", json={
                "env_key": env_key, "model": "claude-sonnet-5", "api_key": "", "base_url": "",
            })

        assert response.json()["ok"] is True, response.json()
        assert seen["api_key"] == "sk-ant-stored-key"
        assert response.json()["model"] == "claude-sonnet-5"
    finally:
        service.save({"api_keys": {env_key: ""}})


def test_upstream_error_reaches_the_user_verbatim() -> None:
    """翻译层删除的回归。agentrouter 的 unauthorized client detected 曾被裸子串 unauthorized
    归进鉴权类，显示成「API Key 无效或已过期」——拒的是客户端不是密钥，同一把密钥换模型照样被拒，
    重填永远修不好；上游原话里的申诉入口也一并被删掉。测试按钮要的就是上游原话。"""
    from app.main import _test_model_error

    raw = '{"error":{"message":"unauthorized client detected, contact support for assistance at https://discord.gg/aYq5B4RW3"},"message":"UNAUTHENTICATED","success":false,"type":"unauthorized_client_error"}'
    message = _test_model_error(raw, "OPENAI_API_KEY", used_placeholder_key=False)
    assert message == raw

    # 判断依据散在 error.message 之外：type 才说明拒的是客户端
    assert "unauthorized_client_error" in message
    assert "discord.gg" in message
    assert "无效或已过期" not in message


def test_placeholder_key_is_disclosed_not_translated() -> None:
    """占位符 sk-relay 是我们替用户编的假密钥，上游冲它报的错不是用户配置的诊断结论。
    但这只能加一句说明，不能把上游原话换掉——换掉就又回到「照着提示修一个不存在的问题」。"""
    from app.main import _test_model_error

    raw = "invalid api key"
    placeholder = _test_model_error(raw, "ANTHROPIC_API_KEY", used_placeholder_key=True)
    assert raw in placeholder
    assert "sk-relay" in placeholder
    assert "未配置 ANTHROPIC_API_KEY" in placeholder

    # 用户真填了密钥时不加任何前缀，上游说什么就是什么
    assert _test_model_error(raw, "ANTHROPIC_API_KEY", used_placeholder_key=False) == raw


async def test_test_model_requires_a_model_belonging_to_the_key() -> None:
    """后端自己从目录里挑模型的话，绿勾说的是「第 0 个模型能答」却被读成「这个服务商通了」；
    挑中的模型在中转上不存在时，失败又会落到密钥头上。拿别家模型配这把密钥测同理。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        missing = await client.post("/api/ai/test-model", json={"env_key": "ANTHROPIC_API_KEY"})
        assert missing.json()["ok"] is False
        assert "未指定" in missing.json()["error"]

        unknown = await client.post("/api/ai/test-model", json={
            "env_key": "ANTHROPIC_API_KEY", "model": "no-such-model-id",
        })
        assert unknown.json()["ok"] is False
        assert "不在目录" in unknown.json()["error"]

        # claude-sonnet-5 存在，但它不属于 OPENAI_API_KEY
        mismatched = await client.post("/api/ai/test-model", json={
            "env_key": "OPENAI_API_KEY", "model": "claude-sonnet-5",
        })
        assert mismatched.json()["ok"] is False
        assert "不属于" in mismatched.json()["error"]


async def test_relay_failure_body_reaches_the_user_whole(monkeypatch) -> None:
    """端到端：中转非 200 时整个 body 连状态码一起原样交出。曾经只挑 error.message 再翻译，
    于是 type=unauthorized_client_error 这个「拒的是客户端不是密钥」的判断依据被丢掉，
    剩下一句「API Key 无效或已过期」把人指向了错的方向。"""
    import app.main as main_module
    import app.services.ai_orchestrator as orch

    service = main_module.ai_config_service
    env_key = "OPENAI_API_KEY"
    relay_body = '{"error":{"message":"unauthorized client detected, contact support for assistance at https://discord.gg/aYq5B4RW3"},"message":"UNAUTHENTICATED","success":false,"type":"unauthorized_client_error"}'
    orch._relay_models_cache.clear()
    try:
        monkeypatch.setattr(service, "get_model_catalog",
                            lambda: [{"id": "gpt-5.6-sol", "env_key": env_key}])

        async def fake_resolve(model, base_url, api_key):
            return f"openai/{model}"

        monkeypatch.setattr(orch, "_resolve_relay_model", fake_resolve)

        sent: dict[str, object] = {}

        class _Resp:
            status_code = 401
            headers = {"content-type": "application/json"}
            text = relay_body

            def json(self) -> dict:
                return json.loads(relay_body)

        class _Client:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args) -> bool:
                return False

            async def post(self, url, headers=None, json=None):
                sent.update({"url": url, "json": json})
                return _Resp()

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", _Client)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post("/api/ai/test-model", json={
                "env_key": env_key, "model": "gpt-5.6-sol",
                "api_key": "sk-relay-real-key", "base_url": "https://relay.example.com/v1",
            })

        body = response.json()
        assert body["ok"] is False, body
        assert "HTTP 401" in body["error"]
        assert relay_body in body["error"], "整个 body 都要在，判断依据散在 error.message 之外"
        assert "无效或已过期" not in body["error"]
        # 点中的模型就是发出去的模型，不能被同族匹配换掉
        assert sent["json"]["model"] == "gpt-5.6-sol"
    finally:
        orch._relay_models_cache.clear()


async def test_extension_status_reports_canexecute_separately_from_display_connected() -> None:
    """connected 带 8s 断线宽限、用于指示灯防抖；canExecute 不平滑——运行前置门控必须看后者，
    否则重连窗口里运行按钮仍可点，动作直接发到空 socket。"""
    import app.main as main_module

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/api/extension/status")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) >= {"connected", "canExecute", "enabled", "connectedSince"}
    assert body["canExecute"] is main_module.extension_bridge_service.is_connected
    assert body["canExecute"] is False, "测试进程里没有真实插件连接"


async def test_extension_execute_hook_refuses_when_disabled_in_settings(monkeypatch) -> None:
    """手工测试口子同样操作用户真实登录的浏览器，开关关掉时不能因为"只是测试"就放行。"""
    import app.main as main_module

    monkeypatch.setattr(main_module.extension_config_service, "load", lambda: {"enabled": False})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/extension/execute", json={"action": {"type": "browser.click", "selector": "#x"}}
        )

    assert response.status_code == 409
    assert "关闭" in response.json()["detail"]
