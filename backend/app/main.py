"""FastAPI application entry point: route registration, middleware, and lifespan management."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.websockets import router as websocket_router
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
    # root 默认 WARNING 会挡住到达 file handler 的 INFO，需要抬高
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
from app.services.ai_config_service import DEFAULT_MODEL as DEFAULT_AI_MODEL, AiConfigService
from app.services.ai_tools import RpaToolExecutor
from app.services.ai_orchestrator import AiOrchestrator
from app.services.code_generator import ScraplingCodeGenerator
from app.services.log_broker import LogBroker
from app.services.notification_config_service import NotificationConfigService
from app.services.notifier import DingTalkNotifier
from app.services.extension_bridge_service import ExtensionBridgeService
from app.services.extension_config_service import ExtensionConfigService
from app.services.overlay_analyzer import OverlayAnalyzer
from app.services.picker_service import PickerService
from app.services.runtime_factory import create_runtime_services
from app.services.scheduler_service import SchedulerLoop
from app.services.site_analyzer import SiteAnalyzer

broker = LogBroker()
settings = load_settings()
# Electron 启动后端时会注入这个变量；直接跑 uvicorn（pnpm backend:dev / pytest）时没人注入，
# Playwright 会回退到 ~/Library/Caches/ms-playwright 找不到内核。这里补上同一个目录，
# 使浏览器内核的位置不依赖于"谁拉起了后端"。已注入时不覆盖，Electron 的值优先。
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(storage.resolve_playwright_browsers_dir()))
ai_config_service = AiConfigService(database_url=settings.database_url)
notification_config_service = NotificationConfigService()
dingtalk_notifier = DingTalkNotifier(config_service=notification_config_service)
overlay_analyzer = OverlayAnalyzer(config_service=ai_config_service)
ai_chat_store = AiChatStore()
_browser_session_dir = str(storage.resolve_browser_profile_dir())
picker_service = PickerService(session_dir=_browser_session_dir)
extension_bridge_service = ExtensionBridgeService()
extension_config_service = ExtensionConfigService()
_setup_file_logging(
    settings.log_dir,
    level=settings.log_level,
    backup_count=settings.log_backup_count,
    module_levels=settings.log_module_levels,
)
runtime_services = create_runtime_services(settings=settings, broker=broker)
task_manager = runtime_services.task_manager
task_manager.set_notifier(dingtalk_notifier)
task_manager.set_overlay_analyzer(overlay_analyzer)
task_manager.set_extension_bridge(extension_bridge_service, is_extension_enabled=lambda: bool(extension_config_service.load()["enabled"]))
code_generator = ScraplingCodeGenerator()
site_analyzer = SiteAnalyzer()
flow_service = runtime_services.flow_service
flow_run_service = runtime_services.flow_run_service
scheduler_service = runtime_services.schedule_service
_rpa_tool_executor = RpaToolExecutor(
    flow_service=flow_service,
    task_manager=task_manager,
    schedule_service=scheduler_service,
)
_ai_orchestrator = AiOrchestrator(tool_executor=_rpa_tool_executor, config_service=ai_config_service)
scheduler_loop = SchedulerLoop(schedule_service=scheduler_service)

# 定时任务失败自愈：调度任务失败后自动做一轮只读 AI 诊断，
# 诊断+修复提案写入该流程的 AI 会话，由用户确认后执行。
from app.services.self_heal_service import SelfHealService  # noqa: E402

_self_heal_service = SelfHealService(
    orchestrator=_ai_orchestrator,
    task_manager=task_manager,
    chat_store=ai_chat_store,
    config_service=ai_config_service,
)
scheduler_service.set_task_started_hook(_self_heal_service.watch_task)


class ResumeHumanTakeoverRequest(BaseModel):
    resume_mode: str = "next_node"   # "next_node" | "current_node"


class AiCatalogModelRequest(BaseModel):
    id: str
    label: str | None = None
    provider: str
    env_key: str
    context_window: int = 0
    tier: str | None = None
    recommended: bool | None = None
    no_vision: bool | None = None
    local: bool | None = None
    legacy: bool | None = None
    provider_label: str | None = None
    badge: str | None = None


class AiCatalogModelUpdateRequest(BaseModel):
    id: str
    label: str | None = None
    context_window: int | None = None
    tier: str | None = None
    recommended: bool | None = None
    legacy: bool | None = None


class AiCatalogDeleteRequest(BaseModel):
    id: str


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    ai_config_service.apply_to_env(ai_config_service.load())
    await ai_config_service.init_catalog()
    await runtime_services.start()
    task_manager.start_workers()
    scheduler_loop.start()
    try:
        yield
    finally:
        await scheduler_loop.stop()
        await task_manager.stop_workers()
        await runtime_services.close()
        await ai_config_service.close_catalog()


app = FastAPI(title="Easy RPA Backend", version="0.1.0", lifespan=lifespan)
# WebSocket router 经 app.state 复用这些单例，避免拆分路由后重复创建
app.state.log_broker = broker
app.state.task_manager = task_manager
app.state.picker_service = picker_service
app.state.extension_bridge_service = extension_bridge_service

app.add_middleware(
    CORSMiddleware,
    # 固定端口：Electron 渲染进程的开发/打包 origin 始终是这两个，不做通配
    allow_origins=["http://127.0.0.1:19174", "http://localhost:19174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(websocket_router)


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
            logging.getLogger(__name__).warning(
                "30天成功率计算失败，流程 %s 将返回空值", flow.flow_id, exc_info=True
            )
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
    try:
        return await task_manager.start_task(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


@app.post("/api/tasks/{task_id}/resume", response_model=TaskSnapshot)
async def resume_human_takeover(task_id: str, request: ResumeHumanTakeoverRequest = Body(default=ResumeHumanTakeoverRequest())) -> TaskSnapshot:
    snapshot = await task_manager.resume_human_takeover(task_id, request.resume_mode)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="任务不存在或未处于等待人工接管状态")
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
    # 手动触发的补充入口：SchedulerLoop 内部已按秒级轮询自动调用同一方法，
    # 此端点非常规运行所必需，供外部（如 Electron 主进程）强制立即检查一次

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


@app.get("/api/notifications/config")
async def get_notification_config() -> dict:
    return notification_config_service.get_masked_config()


@app.put("/api/notifications/config")
async def set_notification_config(payload: dict) -> dict:
    notification_config_service.save(payload)
    return notification_config_service.get_masked_config()


@app.get("/api/ai/models")
async def list_ai_models() -> dict:
    import os as _os
    config = ai_config_service.load()
    api_keys = config["api_keys"]
    models = []
    for m in ai_config_service.get_model_catalog():
        env_key = m.get("env_key", "")
        configured = bool(api_keys.get(env_key) or (env_key and _os.environ.get(env_key)))
        models.append({**m, "configured": configured})
    # 厂商单独给：设置页的分组不能从 models 推，否则某厂商被删空后分组消失，
    # 它的 API Key 入口和「添加模型」入口跟着没了，这个厂商再也加不回来。
    return {
        "models": models,
        "default": config["default_model"],
        "providers": ai_config_service.get_provider_groups(),
    }


@app.post("/api/ai/models")
async def add_ai_model(payload: AiCatalogModelRequest) -> dict:
    try:
        await ai_config_service.add_catalog_model(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await list_ai_models()


@app.put("/api/ai/models")
async def update_ai_model(payload: AiCatalogModelUpdateRequest) -> dict:
    try:
        patch = payload.model_dump(exclude={"id"}, exclude_none=True)
        await ai_config_service.update_catalog_model(payload.id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await list_ai_models()


@app.api_route("/api/ai/models", methods=["DELETE"])
async def delete_ai_model(payload: AiCatalogDeleteRequest) -> dict:
    try:
        await ai_config_service.delete_catalog_model(payload.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await list_ai_models()


def _test_model_error(raw: str, env_key: str, used_placeholder_key: bool) -> str:
    """原样返回上游报错。

    测试按钮的用途就是看上游到底说了什么，所以这里不翻译、不归类、不替换。翻译过一轮：
    agentrouter 的 unauthorized client detected 被裸子串 unauthorized 归进鉴权类，显示成
    「API Key 无效或已过期」，把「中转拒了这个客户端」说成了密钥失效，还删掉了上游原话里的申诉入口。
    """
    if used_placeholder_key:
        # 占位符是我们替用户编的假密钥（sk-relay），上游冲它报的错不算用户配置的诊断结论
        return f"（未配置 {env_key}，本次用占位符 sk-relay 发起）{raw}"
    return raw


@app.post("/api/ai/test-model")
async def test_ai_model(payload: dict) -> dict:
    """Make a real 1-token call to verify credentials and reachability."""
    import time as _time

    # 置位后 _test_model_error 才能声明这把密钥是我们编的，不是用户配的
    used_placeholder_key = False
    env_key: str = str(payload.get("env_key", "")).strip()
    # 目录校验之前抛异常时 except 也要回报模型名，不预绑定会在拼响应时再抛 UnboundLocalError
    catalog_model = ""

    try:
        draft_api_key: str = str(payload.get("api_key", "")).strip()
        draft_base_url: str = str(payload.get("base_url", "")).strip()

        # 探测全程不落盘：配置的唯一写入口是保存按钮，否则一次「测试」就足以改掉
        # 甚至清空用户已存的密钥和 relay 地址。
        # 掩码串是展示值不是凭据，当成草稿会拿一串星号去鉴权。
        if "****" in draft_api_key:
            draft_api_key = ""

        catalog = ai_config_service.get_model_catalog()
        requested_model = str(payload.get("model", "")).strip()
        if not requested_model:
            return {"ok": False, "error": "未指定要测试的模型"}
        # 模型必须确属这把密钥：拿 A 家的模型配 B 家的 key 去测，失败原因会指向密钥，
        # 而真正的错是选错了模型。校验放在发请求之前，错法才说得准。
        entry = next((m for m in catalog if m.get("id") == requested_model), None)
        if entry is None:
            return {"ok": False, "error": f"模型不在目录中: {requested_model}"}
        if entry.get("env_key") != env_key:
            return {"ok": False, "error": f"模型 {requested_model} 不属于 {env_key}"}
        catalog_model = requested_model

        # 草稿优先、已存值兜底：用户没重新输入密钥就是要测「已经存着的那把」，
        # 前端传空串不代表清除，只代表输入框里没有明文可给。
        api_key = draft_api_key or ai_config_service.get_api_key_for_model(catalog_model)
        # base_url 相反：输入框只要出现在请求里，它的值就是本次测试的完整意图，
        # 空串意味着「这次走官方接口」，不能回退到已存的 relay 地址。
        base_url = draft_base_url if "base_url" in payload else ai_config_service.get_base_url_for_model(catalog_model)

        if not api_key:
            if base_url:
                # relay 常自行处理鉴权，占位符让 litellm 能组出合法 Authorization header
                api_key = "sk-relay"
                used_placeholder_key = True
            else:
                return {"ok": False, "error": f"未配置 {env_key}，请先填写 API Key"}

        # 用真实对话同款的 _resolve_relay_model 解析模型，让"测试通过"等于"对话能用"
        from app.services.ai_orchestrator import _normalize_base_url, _resolve_relay_model
        normalized_base = _normalize_base_url(base_url) if base_url else None
        test_model = catalog_model
        if normalized_base:
            try:
                test_model = await _resolve_relay_model(catalog_model, normalized_base, api_key)
            except Exception:
                pass  # 回退用 catalog_model
    except Exception as exc:
        return {"ok": False, "error": _test_model_error(str(exc), env_key, used_placeholder_key)}

    # 中转没有目标模型时 _resolve_relay_model 会模糊匹配到另一个（对话也这么走）。
    # 不回报的话，「claude-opus-5 连接正常」可能是 gpt-4o-mini 答的——测试通过反而误导。
    answered_model = test_model.split("/", 1)[-1]
    served_by = answered_model if answered_model != catalog_model else None

    try:
        t0 = _time.monotonic()
        if normalized_base:
            # 用裸 httpx 测 relay：litellm 底层的 OpenAI SDK 会注入 x-stainless-* 头，很多 relay 会拦
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=15) as _client:
                _r = await _client.post(
                    normalized_base.rstrip("/") + "/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": answered_model, "messages": [{"role": "user", "content": "hi"}], "max_completion_tokens": 1},
                )
            ms = int((_time.monotonic() - t0) * 1000)
            if _r.status_code == 200:
                return {"ok": True, "latency_ms": ms, "model": catalog_model, "served_by": served_by}
            # 整个 body 原样交出：中转把关键信息散在 error.message 之外的字段里
            # （agentrouter 的 type=unauthorized_client_error 才说明拒的是客户端不是密钥），
            # 只挑 message 会把判断依据丢掉。
            raise Exception(f"HTTP {_r.status_code}: {_r.text[:1000]}")
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
        return {"ok": True, "latency_ms": ms, "model": catalog_model, "served_by": served_by}
    except Exception as exc:
        return {
            "ok": False,
            "error": _test_model_error(str(exc), env_key, used_placeholder_key),
            "model": catalog_model,
        }


@app.post("/api/ai/chat")
async def ai_chat(payload: dict) -> StreamingResponse:
    """SSE streaming chat with tool-call loop."""
    import json as _json

    messages: list[dict] = payload.get("messages", [])
    model: str = payload.get("model", "") or ai_config_service.load().get("default_model", DEFAULT_AI_MODEL)
    flow_id: str | None = payload.get("flow_id") or None

    async def event_stream():
        try:
            async for chunk in _ai_orchestrator.stream(messages=messages, model=model, flow_id=flow_id):
                yield f"data: {_json.dumps(chunk, ensure_ascii=False)}\n\n"
        except Exception as exc:
            err = _json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False)
            yield f"data: {err}\n\n"
            yield f"data: {_json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.post("/api/ai/diff/apply")
async def apply_flow_diff(payload: dict) -> dict:
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

        nodes.extend(diff.get("add_nodes", []))
        new_edges: list = list(diff.get("add_edges", []))

        # 若新边里已有 A→B+B→C 这条插入路径，旧的 A→C 直连边要丢掉，否则会分叉
        new_edge_pairs: set[tuple[str, str]] = {
            (e.get("source", ""), e.get("target", ""))
            for e in new_edges
            if isinstance(e, dict)
        }
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
    if full_path.stat().st_size > 5 * 1024 * 1024:
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


@app.get("/api/ai/chats")
async def list_chat_sessions() -> dict:
    return {"sessions": ai_chat_store.list_sessions()}


@app.get("/api/ai/chats/{session_key}")
async def get_chat_session(session_key: str) -> dict:
    messages = ai_chat_store.load(session_key)
    return {"session_key": session_key, "messages": messages}


@app.put("/api/ai/chats/{session_key}")
async def save_chat_session(session_key: str, payload: dict) -> dict:
    messages: list = payload.get("messages", [])
    if not isinstance(messages, list):
        raise HTTPException(status_code=422, detail="messages 必须是数组")
    ai_chat_store.save(session_key, messages)
    return {"session_key": session_key, "message_count": len(messages), "status": "saved"}


@app.post("/api/ai/chats/{session_key}/rename")
async def rename_chat_session(session_key: str, payload: dict) -> dict:
    to_key = str(payload.get("toKey", "")).strip()
    if not to_key:
        raise HTTPException(status_code=422, detail="toKey 不能为空")
    moved = ai_chat_store.rename(session_key, to_key)
    return {"session_key": session_key, "to_key": to_key, "moved": moved}


@app.delete("/api/ai/chats/{session_key}")
async def delete_chat_session(session_key: str) -> dict:
    deleted = ai_chat_store.delete(session_key)
    return {"session_key": session_key, "deleted": deleted}


@app.post("/api/browser/picker/open")
async def open_picker(payload: dict) -> dict:
    target_url = str(payload.get("targetUrl", "")).strip()
    mode = str(payload.get("mode", "pick")).strip()
    if not target_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="targetUrl 必须以 http:// 或 https:// 开头")
    await picker_service.open(target_url, mode=mode)
    return {"status": "opened", "mode": mode}


@app.post("/api/browser/picker/close")
async def close_picker() -> dict:
    await picker_service.close()
    return {"status": "closed"}


@app.get("/api/extension/status")
async def extension_bridge_status() -> dict:
    connected_since = extension_bridge_service.connected_since
    return {
        # connected 带 8s 断线宽限，只为 UI 指示灯防抖；canExecute 是不平滑的真实状态。
        # 运行前置门控必须看后者，否则重连窗口里按钮仍可点，动作直接发到空 socket。
        "connected": extension_bridge_service.is_connected_for_display,
        "canExecute": extension_bridge_service.is_connected,
        "enabled": extension_config_service.load()["enabled"],
        "connectedSince": datetime.fromtimestamp(connected_since, tz=timezone.utc).isoformat() if connected_since is not None else None,
    }


@app.get("/api/extension/config")
async def get_extension_config() -> dict:
    return extension_config_service.load()


@app.put("/api/extension/config")
async def set_extension_config(payload: dict) -> dict:
    return extension_config_service.save(payload)


@app.post("/api/extension/execute")
async def extension_bridge_execute(payload: dict) -> dict:
    """Manual test hook: send one action to the connected extension and return its result."""
    action = payload.get("action")
    if not isinstance(action, dict) or not action.get("type"):
        raise HTTPException(status_code=422, detail="action.type 不能为空")
    # 这个口子同样操作用户真实登录的浏览器，开关关掉时不能因为"只是手工测试"就放行。
    if not extension_config_service.load()["enabled"]:
        raise HTTPException(status_code=409, detail="插件执行器已在设置中关闭")
    try:
        result = await extension_bridge_service.execute(action)
    except ConnectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"result": result}


@app.get("/api/browser/cookies")
async def get_browser_cookies(url: str | None = Query(default=None)) -> list[dict]:
    """Return cookies saved from the last browser task run, optionally filtered by URL hostname."""
    import json
    from urllib.parse import urlparse

    cookie_file = storage.resolve_browser_cookies_path()
    if not cookie_file.exists():
        return []

    try:
        cookies: list[dict] = json.loads(cookie_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    if url:
        hostname = urlparse(url).hostname or ""
        # 去掉 domain 前导的 "." 后做后缀匹配，兼容 Set-Cookie 里常见的父域 cookie（如 ".example.com" 应匹配 "sub.example.com"）
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
