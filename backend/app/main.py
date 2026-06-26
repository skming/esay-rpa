"""FastAPI application entry point: route registration, middleware, and lifespan management."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core import storage
from app.core.config import load_settings


# Third-party loggers that flood backend.log at INFO/DEBUG; pin them to WARNING.
_NOISY_LOGGERS = ("httpx", "httpcore", "litellm", "LiteLLM", "openai", "urllib3", "asyncio", "playwright", "watchfiles")


def _setup_file_logging(
    log_dir: str,
    *,
    level: str = "INFO",
    backup_count: int = 30,
    module_levels: dict[str, str] | None = None,
) -> None:
    if not log_dir:
        return
    import re
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.TimedRotatingFileHandler(
        log_path / "backend.log",
        when="midnight",
        backupCount=backup_count,
        encoding="utf-8",
    )
    # Rename rotated files: backend.log.2026-06-21 → backend-2026-06-21.log
    handler.namer = lambda name: re.sub(
        r"(.+[/\\])backend\.log\.(\d{4}-\d{2}-\d{2})$", r"\1backend-\2.log", name
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s"))
    resolved_level = getattr(logging, level.upper(), logging.INFO)
    handler.setLevel(resolved_level)
    root = logging.getLogger()
    # Root defaults to WARNING, which would swallow INFO before it reaches the
    # file handler — lift it (but never below WARNING for the noisy libs below).
    if root.level == logging.NOTSET or root.level > resolved_level:
        root.setLevel(resolved_level)
    root.addHandler(handler)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    for module, lvl in (module_levels or {}).items():
        logging.getLogger(module).setLevel(getattr(logging, lvl))


from app.models.schemas import (
    AnalyzeSiteRequest,
    CodeGenerateRequest,
    ArtifactContent,
    ArtifactSnapshot,
    DebugControlRequest,
    UserInputRequest,
    FlowCreateRequest,
    FlowMoveRequest,
    FlowRunRequest,
    FlowSnapshot,
    FlowStatusPatchRequest,
    FlowUpdateRequest,
    GeneratedScript,
    HealthResponse,
    QueueStats,
    RunTaskRequest,
    ScheduleCreateRequest,
    ScheduleSnapshot,
    ScheduleUpdateRequest,
    SiteAnalysisResult,
    TaskLogEntry,
    TaskSnapshot,
    RuntimeVariableSnapshot,
)
from app.services.ai_chat_store import AiChatStore
from app.services.ai_config_service import AI_MODEL_CATALOG, AiConfigService
from app.services.ai_tools import RpaToolExecutor
from app.services.ai_orchestrator import AiOrchestrator
from app.services.code_generator import ScraplingCodeGenerator
from app.services.log_broker import LogBroker
from app.services.picker_service import PickerService
from app.services.runtime_factory import create_runtime_services
from app.services.scheduler_service import SchedulerLoop
from app.services.site_analyzer import SiteAnalyzer

broker = LogBroker()
settings = load_settings()
ai_config_service = AiConfigService()
ai_chat_store = AiChatStore()
_browser_session_dir = str(storage.resolve_browser_profile_dir())
picker_service = PickerService(session_dir=_browser_session_dir)
_setup_file_logging(
    settings.log_dir,
    level=settings.log_level,
    backup_count=settings.log_backup_count,
    module_levels=settings.log_module_levels,
)
runtime_services = create_runtime_services(settings=settings, broker=broker)
task_manager = runtime_services.task_manager
code_generator = ScraplingCodeGenerator()
site_analyzer = SiteAnalyzer()
flow_service = runtime_services.flow_service
flow_run_service = runtime_services.flow_run_service
_rpa_tool_executor = RpaToolExecutor(flow_service=flow_service, task_manager=task_manager)
_ai_orchestrator = AiOrchestrator(tool_executor=_rpa_tool_executor, config_service=ai_config_service)
scheduler_service = runtime_services.schedule_service
scheduler_loop = SchedulerLoop(schedule_service=scheduler_service)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Apply persisted AI API keys to environment before workers start
    ai_config_service.apply_to_env(ai_config_service.load())
    await runtime_services.start()
    task_manager.start_workers()
    scheduler_loop.start()
    try:
        yield
    finally:
        await scheduler_loop.stop()
        await task_manager.stop_workers()
        await runtime_services.close()


app = FastAPI(title="Easy RPA Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:19174", "http://localhost:19174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="easy-rpa-backend")


@app.post("/api/code/generate", response_model=GeneratedScript)
async def generate_code(request: CodeGenerateRequest) -> GeneratedScript:
    return code_generator.generate(request)


@app.post("/api/site/analyze", response_model=SiteAnalysisResult)
async def analyze_site(request: AnalyzeSiteRequest) -> SiteAnalysisResult:
    try:
        return await site_analyzer.analyze(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/flows", response_model=FlowSnapshot)
async def create_flow(request: FlowCreateRequest) -> FlowSnapshot:
    return await flow_service.create_flow(request)


@app.get("/api/flows", response_model=list[FlowSnapshot])
async def list_flows() -> list[FlowSnapshot]:
    flows = await flow_service.list_flows()
    if not flows:
        return flows

    async def _enrich(flow: FlowSnapshot) -> FlowSnapshot:
        try:
            tasks = await task_manager.list_tasks(flow_id=flow.flow_id, limit=200)
            rate = flow_service.compute_success_rate_30d(tasks)
            return flow.model_copy(update={"success_rate_30d": rate})
        except Exception:
            return flow

    return list(await asyncio.gather(*(_enrich(f) for f in flows)))


@app.get("/api/flows/{flow_id}", response_model=FlowSnapshot)
async def get_flow(flow_id: str) -> FlowSnapshot:
    snapshot = await flow_service.get_flow(flow_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return snapshot


@app.patch("/api/flows/{flow_id}", response_model=FlowSnapshot)
async def update_flow(flow_id: str, request: FlowUpdateRequest) -> FlowSnapshot:
    snapshot = await flow_service.update_flow(flow_id, request)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return snapshot


@app.post("/api/flows/{flow_id}/duplicate", response_model=FlowSnapshot)
async def duplicate_flow(flow_id: str) -> FlowSnapshot:
    snapshot = await flow_service.duplicate_flow(flow_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return snapshot


@app.patch("/api/flows/{flow_id}/move", response_model=FlowSnapshot)
async def move_flow(flow_id: str, request: FlowMoveRequest) -> FlowSnapshot:
    snapshot = await flow_service.move_flow(flow_id, request.folder_path)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return snapshot


@app.patch("/api/flows/{flow_id}/status", response_model=FlowSnapshot)
async def set_flow_status(flow_id: str, request: FlowStatusPatchRequest) -> FlowSnapshot:
    snapshot = await flow_service.set_flow_status(flow_id, request.status)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return snapshot


@app.post("/api/flows/{flow_id}/archive", response_model=FlowSnapshot)
async def archive_flow(flow_id: str) -> FlowSnapshot:
    snapshot = await flow_service.archive_flow(flow_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return snapshot


@app.post("/api/flows/{flow_id}/run", response_model=TaskSnapshot)
async def run_flow(flow_id: str, request: FlowRunRequest) -> TaskSnapshot:
    snapshot = await flow_service.get_flow(flow_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    try:
        return await flow_run_service.run_flow(snapshot, mode=request.mode, run_request=request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/flows/{flow_id}", response_model=dict[str, bool])
async def delete_flow(flow_id: str) -> dict[str, bool]:
    deleted = await flow_service.delete_flow(flow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Flow not found")
    return {"deleted": True}


@app.post("/api/tasks", response_model=TaskSnapshot)
async def create_task(request: RunTaskRequest) -> TaskSnapshot:
    return await task_manager.start_task(request)


@app.get("/api/tasks", response_model=list[TaskSnapshot])
async def list_tasks(flow_id: str | None = Query(default=None, alias="flowId"), limit: int = Query(default=50, ge=1, le=200)) -> list[TaskSnapshot]:
    return await task_manager.list_tasks(flow_id=flow_id, limit=limit)


@app.get("/api/flows/{flow_id}/runs", response_model=list[TaskSnapshot])
async def list_flow_runs(flow_id: str, limit: int = Query(default=50, ge=1, le=200)) -> list[TaskSnapshot]:
    flow = await flow_service.get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return await task_manager.list_tasks(flow_id=flow_id, limit=limit)


@app.get("/api/queue", response_model=QueueStats)
async def get_queue_stats() -> QueueStats:
    return await task_manager.queue_stats()


@app.get("/api/tasks/{task_id}", response_model=TaskSnapshot)
async def get_task(task_id: str) -> TaskSnapshot:
    snapshot = await task_manager.get_task(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return snapshot


@app.post("/api/tasks/{task_id}/stop", response_model=TaskSnapshot)
async def stop_task(task_id: str) -> TaskSnapshot:
    snapshot = await task_manager.stop_task(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return snapshot


@app.post("/api/tasks/{task_id}/input", response_model=TaskSnapshot)
async def provide_task_input(task_id: str, request: UserInputRequest) -> TaskSnapshot:
    snapshot = await task_manager.provide_input(task_id, request.value)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task not found or not waiting for input")
    return snapshot


@app.post("/api/tasks/{task_id}/debug", response_model=TaskSnapshot)
async def debug_task(task_id: str, request: DebugControlRequest) -> TaskSnapshot:
    try:
        snapshot = await task_manager.debug_control(task_id, request.command)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return snapshot


@app.get("/api/tasks/{task_id}/logs", response_model=list[TaskLogEntry])
async def get_task_logs(task_id: str) -> list[TaskLogEntry]:
    logs = await task_manager.get_logs(task_id)
    if logs is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return logs


@app.get("/api/tasks/{task_id}/variables", response_model=list[RuntimeVariableSnapshot])
async def get_task_variables(task_id: str) -> list[RuntimeVariableSnapshot]:
    variables = await task_manager.get_variables(task_id)
    if variables is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return variables


@app.get("/api/tasks/{task_id}/artifacts", response_model=list[ArtifactSnapshot])
async def get_task_artifacts(task_id: str) -> list[ArtifactSnapshot]:
    artifacts = await task_manager.get_artifacts(task_id)
    if artifacts is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return artifacts


@app.get("/api/tasks/{task_id}/artifacts/{artifact_id}", response_model=ArtifactContent)
async def get_task_artifact_content(task_id: str, artifact_id: str) -> ArtifactContent:
    try:
        content = await task_manager.get_artifact_content(task_id, artifact_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if content is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return content


@app.post("/api/schedules", response_model=ScheduleSnapshot)
async def create_schedule(request: ScheduleCreateRequest) -> ScheduleSnapshot:
    try:
        return await scheduler_service.create_schedule(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/schedules", response_model=list[ScheduleSnapshot])
async def list_schedules() -> list[ScheduleSnapshot]:
    return await scheduler_service.list_schedules()


@app.get("/api/schedules/{schedule_id}", response_model=ScheduleSnapshot)
async def get_schedule(schedule_id: str) -> ScheduleSnapshot:
    snapshot = await scheduler_service.get_schedule(schedule_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return snapshot


@app.patch("/api/schedules/{schedule_id}", response_model=ScheduleSnapshot)
async def update_schedule(schedule_id: str, request: ScheduleUpdateRequest) -> ScheduleSnapshot:
    try:
        snapshot = await scheduler_service.update_schedule(schedule_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return snapshot


@app.delete("/api/schedules/{schedule_id}", response_model=dict[str, bool])
async def delete_schedule(schedule_id: str) -> dict[str, bool]:
    deleted = await scheduler_service.delete_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"deleted": True}


@app.get("/api/schedules/{schedule_id}/history", response_model=list[TaskSnapshot])
async def get_schedule_history(schedule_id: str, limit: int = Query(default=50, ge=1, le=200)) -> list[TaskSnapshot]:
    schedule = await scheduler_service.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return await task_manager.list_tasks(schedule_id=schedule_id, limit=limit)


@app.patch("/api/schedules/{schedule_id}/toggle", response_model=ScheduleSnapshot)
async def toggle_schedule(schedule_id: str) -> ScheduleSnapshot:
    current = await scheduler_service.get_schedule(schedule_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    new_enabled = current.status != "enabled"
    try:
        snapshot = await scheduler_service.update_schedule(schedule_id, ScheduleUpdateRequest(enabled=new_enabled))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return snapshot


@app.post("/api/schedules/{schedule_id}/trigger", response_model=ScheduleSnapshot)
async def trigger_schedule(schedule_id: str) -> ScheduleSnapshot:
    try:
        snapshot = await scheduler_service.trigger_schedule(schedule_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return snapshot


@app.post("/api/schedules:tick", response_model=list[ScheduleSnapshot])
async def run_due_schedules() -> list[ScheduleSnapshot]:
    try:
        return await scheduler_service.run_due_schedules()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/ai/config")
async def get_ai_config() -> dict:
    return ai_config_service.get_masked_config()


@app.put("/api/ai/config")
async def set_ai_config(payload: dict) -> dict:
    ai_config_service.save(payload)
    # Re-apply keys to env so newly configured keys take effect immediately
    ai_config_service.apply_to_env(ai_config_service.load())
    return ai_config_service.get_masked_config()


@app.get("/api/ai/models")
async def list_ai_models() -> dict:
    config = ai_config_service.load()
    models = []
    for m in AI_MODEL_CATALOG:
        env_key = m.get("env_key", "")
        configured = bool(
            config["api_keys"].get(env_key)
            or (env_key and __import__("os").environ.get(env_key))
        )
        models.append({**m, "configured": configured})
    return {"models": models, "default": config["default_model"]}


@app.post("/api/ai/test-model")
async def test_ai_model(payload: dict) -> dict:
    """Make a real 1-token call to verify credentials and reachability."""
    import time as _time

    env_key: str = str(payload.get("env_key", "")).strip()
    draft_api_key: str = str(payload.get("api_key", "")).strip()
    draft_base_url: str = str(payload.get("base_url", "")).strip()

    # Save draft values first so subsequent lookups see them
    if draft_api_key and env_key:
        ai_config_service.save({"api_keys": {env_key: draft_api_key}})
        ai_config_service.apply_to_env(ai_config_service.load())
    if env_key:
        ai_config_service.save({"base_urls": {env_key: draft_base_url}})

    # Default cheapest catalog model per provider
    _CATALOG_MODEL: dict[str, str] = {
        "ANTHROPIC_API_KEY":  "claude-haiku-4-5",
        "OPENAI_API_KEY":     "gpt-4.1-mini",
        "GEMINI_API_KEY":     "gemini/gemini-2.5-flash",
        "DEEPSEEK_API_KEY":   "deepseek/deepseek-v4-flash",
        "DASHSCOPE_API_KEY":  "openai/qwen3-32b",
        "ZAI_API_KEY":        "zai/glm-4.5-flash",
    }
    catalog_model = _CATALOG_MODEL.get(env_key)
    if not catalog_model:
        return {"ok": False, "error": f"未知提供商 key: {env_key}"}

    # Prefer the user's configured default_model when it belongs to this provider —
    # it's the model they actually use and is more likely to be accessible on their relay.
    from app.services.ai_config_service import AI_MODEL_CATALOG as _cat
    cfg_default = ai_config_service.load().get("default_model", "")
    if cfg_default:
        default_env_key = next((m.get("env_key", "") for m in _cat if m["id"] == cfg_default), "")
        if default_env_key == env_key:
            catalog_model = cfg_default

    api_key = ai_config_service.get_api_key_for_model(catalog_model)
    base_url = ai_config_service.get_base_url_for_model(catalog_model)

    if not api_key:
        if base_url:
            # Relay configured without a key — many relays handle auth themselves;
            # use a placeholder so litellm can form a valid Authorization header.
            api_key = "sk-relay"
        else:
            return {"ok": False, "error": f"未配置 {env_key}，请先填写 API Key"}

    # When a relay is configured, resolve the model the SAME way real chat does
    # (_resolve_relay_model) so "test passes" implies "chat works". Picking the
    # relay's first listed model blindly often hits a non-chat or blocked model.
    from app.services.ai_orchestrator import _normalize_base_url, _resolve_relay_model
    normalized_base = _normalize_base_url(base_url) if base_url else None
    test_model = catalog_model
    if normalized_base:
        try:
            test_model = await _resolve_relay_model(catalog_model, normalized_base, api_key)
        except Exception:
            pass  # fall through to catalog model

    try:
        t0 = _time.monotonic()
        if normalized_base:
            # Use plain httpx for relay testing — the OpenAI Python SDK (used by litellm)
            # injects x-stainless-* telemetry headers that many relay operators block.
            import httpx as _httpx
            bare_model = test_model.split("/", 1)[-1]  # strip "openai/" litellm prefix
            async with _httpx.AsyncClient(timeout=15) as _client:
                _r = await _client.post(
                    normalized_base.rstrip("/") + "/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": bare_model, "messages": [{"role": "user", "content": "hi"}], "max_completion_tokens": 1},
                )
            ms = int((_time.monotonic() - t0) * 1000)
            if _r.status_code == 200:
                return {"ok": True, "latency_ms": ms}
            _relay_err = _r.json() if _r.headers.get("content-type", "").startswith("application/json") else {}
            # Try nested error.message first, then root-level message, then raw body
            _relay_msg = (
                (_relay_err.get("error") or {}).get("message")
                or _relay_err.get("message")
                or _r.text[:300]
            )
            raise Exception(_relay_msg)
        else:
            import litellm as _litellm
            await _litellm.acompletion(
                model=test_model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                stream=False,
                api_key=api_key,
            )
        ms = int((_time.monotonic() - t0) * 1000)
        return {"ok": True, "latency_ms": ms}
    except Exception as exc:
        from app.services.ai_orchestrator import _clean_litellm_error
        return {"ok": False, "error": _clean_litellm_error(str(exc))}


@app.post("/api/ai/chat")
async def ai_chat(payload: dict) -> StreamingResponse:
    """SSE streaming chat with tool-call loop."""
    from starlette.responses import StreamingResponse as _SR
    import json as _json

    messages: list[dict] = payload.get("messages", [])
    model: str = payload.get("model", "") or ai_config_service.load().get("default_model", "claude-sonnet-4-6")
    flow_id: str | None = payload.get("flow_id") or None

    async def event_stream():
        try:
            async for chunk in _ai_orchestrator.stream(messages=messages, model=model, flow_id=flow_id):
                yield f"data: {_json.dumps(chunk, ensure_ascii=False)}\n\n"
        except Exception as exc:
            err = _json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False)
            yield f"data: {err}\n\n"
            yield f"data: {_json.dumps({'type': 'done'})}\n\n"

    return _SR(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.post("/api/ai/diff/apply")
async def apply_flow_diff(payload: dict) -> dict:
    """Apply a confirmed FlowDiff to the actual flow definition."""
    diff: dict = payload.get("diff", {})
    flow_id: str = str(diff.get("flow_id", "")).strip()
    if not flow_id:
        raise HTTPException(status_code=422, detail="diff.flow_id 是必填项")

    flow = await flow_service.get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")

    import copy as _copy
    from app.models.schemas import FlowUpdateRequest

    try:
        definition = _copy.deepcopy(dict(flow.definition))
        nodes: list = list(definition.get("nodes", []))
        edges: list = list(definition.get("edges", []))

        # Remove nodes/edges first
        remove_node_ids: set[str] = set(diff.get("remove_node_ids", []))
        remove_edge_ids: set[str] = set(diff.get("remove_edge_ids", []))
        nodes = [n for n in nodes if isinstance(n, dict) and n.get("id") not in remove_node_ids]
        edges = [e for e in edges if isinstance(e, dict) and e.get("id") not in remove_edge_ids]

        # Auto-clean dangling edges left by removed nodes (AI often forgets remove_edge_ids)
        surviving_node_ids = {n["id"] for n in nodes if isinstance(n, dict) and n.get("id")}
        # Also include newly-added node ids so their edges are not falsely pruned
        for new_node in diff.get("add_nodes", []):
            if isinstance(new_node, dict) and new_node.get("id"):
                surviving_node_ids.add(new_node["id"])
        edges = [
            e for e in edges
            if isinstance(e, dict)
            and e.get("source") in surviving_node_ids
            and e.get("target") in surviving_node_ids
        ]

        # Apply node patches — RPA nodes use flat fields, not a nested "config" object
        update_map = {u["id"]: u["patch"] for u in diff.get("update_nodes", []) if "id" in u and "patch" in u}
        for node in nodes:
            if isinstance(node, dict) and node.get("id") in update_map:
                node.update(update_map[node["id"]])

        # Add new nodes and edges
        nodes.extend(diff.get("add_nodes", []))
        new_edges: list = list(diff.get("add_edges", []))

        # Deduplicate edges: if a new edge A→C already has an intermediate path
        # A→B + B→C in the new edges, drop A→C to prevent unintended forks.
        # Build a set of (source, target) pairs from newly-added edges.
        new_edge_pairs: set[tuple[str, str]] = {
            (e.get("source", ""), e.get("target", ""))
            for e in new_edges
            if isinstance(e, dict)
        }
        # For each existing edge, drop it if both A→X and X→C exist in the new edges
        # (meaning a node was inserted between A and C).
        def _is_bypassed(e: dict) -> bool:
            src, tgt = e.get("source", ""), e.get("target", "")
            for mid in surviving_node_ids | {n.get("id", "") for n in diff.get("add_nodes", []) if isinstance(n, dict)}:
                if (src, mid) in new_edge_pairs and (mid, tgt) in new_edge_pairs:
                    return True
            return False

        edges = [e for e in edges if not (isinstance(e, dict) and _is_bypassed(e))]
        edges.extend(new_edges)

        # Remove duplicate edges (same source+target, keep first)
        seen_pairs: set[tuple[str, str]] = set()
        deduped_edges = []
        for e in edges:
            if not isinstance(e, dict):
                continue
            pair = (e.get("source", ""), e.get("target", ""))
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                deduped_edges.append(e)
        edges = deduped_edges

        definition["nodes"] = nodes
        definition["edges"] = edges

        req = FlowUpdateRequest(definition=definition)
        updated = await flow_service.update_flow(flow_id, req)
        if updated is None:
            raise HTTPException(status_code=404, detail="Flow not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"应用变更失败：{exc}") from exc

    return {"flow_id": flow_id, "status": "applied", "node_count": len(nodes), "edge_count": len(edges)}


# ── Workspace File Preview ─────────────────────────────────────────────────────

class CsvPreviewResponse(BaseModel):
    path: str
    headers: list[str]
    rows: list[list[str]]
    total_rows: int
    truncated: bool

@app.get("/api/workspace/preview-csv", response_model=CsvPreviewResponse)
async def preview_csv(path: str, limit: int = 200):
    """Preview a CSV file from the RPA workspace. path is relative to workspace root."""
    import csv as csv_module
    workspace_root = storage.resolve_workspace_root()

    # Security: only allow relative paths within workspace
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="只允许访问工作区内的相对路径")

    full_path = (workspace_root / path).resolve()
    if not full_path.is_relative_to(workspace_root.resolve()):
        raise HTTPException(status_code=400, detail="路径超出工作区范围")
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
    if full_path.suffix.lower() not in {".csv", ".tsv"}:
        raise HTTPException(status_code=400, detail="仅支持预览 CSV/TSV 文件")
    if full_path.stat().st_size > 5 * 1024 * 1024:  # 5MB limit
        raise HTTPException(status_code=400, detail="文件超过 5MB，无法预览")

    rows_data: list[list[str]] = []
    headers: list[str] = []
    truncated = False

    with full_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv_module.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                headers = row
            elif i <= limit:
                rows_data.append(row)
            else:
                truncated = True
                break

    return CsvPreviewResponse(
        path=path,
        headers=headers,
        rows=rows_data,
        total_rows=len(rows_data),
        truncated=truncated
    )


# ── AI Chat Sessions ───────────────────────────────────────────────────────────

@app.get("/api/ai/chats")
async def list_chat_sessions() -> dict:
    """List all saved chat sessions with metadata."""
    return {"sessions": ai_chat_store.list_sessions()}


@app.get("/api/ai/chats/{session_key}")
async def get_chat_session(session_key: str) -> dict:
    """Load all messages for a session."""
    messages = ai_chat_store.load(session_key)
    return {"session_key": session_key, "messages": messages}


@app.put("/api/ai/chats/{session_key}")
async def save_chat_session(session_key: str, payload: dict) -> dict:
    """Overwrite a session with the provided messages list."""
    messages: list = payload.get("messages", [])
    if not isinstance(messages, list):
        raise HTTPException(status_code=422, detail="messages 必须是数组")
    ai_chat_store.save(session_key, messages)
    return {"session_key": session_key, "message_count": len(messages), "status": "saved"}


@app.delete("/api/ai/chats/{session_key}")
async def delete_chat_session(session_key: str) -> dict:
    """Delete a session file."""
    deleted = ai_chat_store.delete(session_key)
    return {"session_key": session_key, "deleted": deleted}


@app.post("/api/browser/picker/open")
async def open_picker(payload: dict) -> dict:
    target_url = str(payload.get("targetUrl", "")).strip()
    if not target_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="targetUrl 必须以 http:// 或 https:// 开头")
    await picker_service.open(target_url)
    return {"status": "opened"}


@app.post("/api/browser/picker/close")
async def close_picker() -> dict:
    await picker_service.close()
    return {"status": "closed"}


@app.websocket("/ws/picker")
async def picker_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        result = await picker_service.wait_for_result()
        if result is None:
            await websocket.send_json({"type": "cancel"})
        else:
            await websocket.send_json({"type": "capture", **result})
    except (asyncio.CancelledError, WebSocketDisconnect):
        pass
    finally:
        await websocket.close()


@app.get("/api/browser/cookies")
async def get_browser_cookies(url: str | None = Query(default=None)) -> list[dict]:
    """Return cookies saved from the last browser task run, optionally filtered by URL hostname."""
    import json
    from urllib.parse import urlparse
    from pathlib import Path

    cookie_file = storage.resolve_browser_cookies_path()
    if not cookie_file.exists():
        return []

    try:
        cookies: list[dict] = json.loads(cookie_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    if url:
        hostname = urlparse(url).hostname or ""
        cookies = [
            c for c in cookies
            if hostname.endswith(c.get("domain", "").lstrip("."))
            or c.get("domain", "").lstrip(".") == hostname
        ]

    return [
        {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
            "expirationDate": c.get("expires") if c.get("expires", -1) != -1 else None,
        }
        for c in cookies
        if c.get("name") and c.get("value") is not None
    ]


@app.websocket("/ws/tasks/{task_id}/logs")
async def task_logs_socket(websocket: WebSocket, task_id: str) -> None:
    # 先 accept 再发错误，避免浏览器收到非标准 close code 产生控制台报错
    await websocket.accept()
    if await task_manager.get_task(task_id) is None:
        await websocket.send_json({"type": "error", "message": f"Task not found: {task_id}"})
        await websocket.close(code=1008, reason="Task not found")
        return

    queue = broker.subscribe(task_id)
    try:
        for log in await task_manager.get_logs(task_id) or []:
            await websocket.send_json(jsonable_encoder(log, by_alias=True))
        while True:
            event = await queue.get()
            await websocket.send_json(jsonable_encoder(event, by_alias=True))
    except (asyncio.CancelledError, WebSocketDisconnect):
        return
    finally:
        broker.unsubscribe(task_id, queue)
