"""定时任务失败自愈诊断。

cron 流程运行失败时，用只读 AI 诊断（write 工具被 orchestrator 的 read_only 策略拦截）
分析根因并把修复提案追加到该流程的 AI 对话，用户下次打开面板自行决定是否采纳，不自动改流程。
限流：每流程每 DIAGNOSIS_COOLDOWN_S 最多一次；可通过 ai config self_heal_enabled 整体关闭。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.ai_chat_store import AiChatStore
    from app.services.ai_config_service import AiConfigService
    from app.services.ai_orchestrator import AiOrchestrator
    from app.services.task_manager import TaskManager

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"success", "error", "stopped"}
_WATCH_TIMEOUT_S = 30 * 60
_WATCH_INTERVAL_S = 10
DIAGNOSIS_COOLDOWN_S = 3600

_DIAGNOSIS_PROMPT = (
    "【系统自动触发·定时任务失败自愈诊断】\n"
    "定时任务「{schedule_name}」运行失败（task_id=`{task_id}`）。\n"
    "请诊断失败根因并给出具体修复提案。要求：\n"
    "1. 先用 get_run_error / get_run_logs 获取失败节点与错误信息\n"
    "2. 拓扑与静态诊断看状态块；需要 DOM 时用 inspect_page\n"
    "3. 最终用文字输出：根因结论 + 修复提案（写明节点 id、字段、建议值）\n"
    "注意：当前为只读诊断模式，不要尝试修改流程或重新运行——修复方案由用户确认后执行。"
)


class SelfHealService:
    def __init__(
        self,
        orchestrator: "AiOrchestrator",
        task_manager: "TaskManager",
        chat_store: "AiChatStore",
        config_service: "AiConfigService",
    ) -> None:
        self._orchestrator = orchestrator
        self._task_manager = task_manager
        self._chat_store = chat_store
        self._config_service = config_service
        self._last_diagnosis_at: dict[str, float] = {}
        self._watch_tasks: set[asyncio.Task] = set()

    def watch_task(self, task_id: str, flow_id: str | None, schedule_name: str = "") -> None:
        """Fire-and-forget：后台观察任务，失败时触发诊断。"""
        if not flow_id:
            return
        if not self._enabled():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("self_heal: no running loop, skip watch for %s", task_id)
            return
        # asyncio 只对 task 持弱引用，不存进集合会在 _watch 的 sleep 期间被 GC 掉
        task = loop.create_task(self._watch(task_id, flow_id, schedule_name))
        self._watch_tasks.add(task)
        task.add_done_callback(self._watch_tasks.discard)

    def _enabled(self) -> bool:
        try:
            return bool(self._config_service.load().get("self_heal_enabled", True))
        except Exception:
            return True  # 配置读取失败时按开启处理（fail-open），避免因配置故障漏诊断

    async def _watch(self, task_id: str, flow_id: str, schedule_name: str) -> None:
        try:
            elapsed = 0
            status = ""
            while elapsed < _WATCH_TIMEOUT_S:
                await asyncio.sleep(_WATCH_INTERVAL_S)
                elapsed += _WATCH_INTERVAL_S
                snapshot = await self._task_manager.get_task(task_id)
                if snapshot is None:
                    return
                status = snapshot.status
                if status in _TERMINAL_STATUSES:
                    break
            if status != "error":
                return

            now = time.monotonic()
            last = self._last_diagnosis_at.get(flow_id, 0.0)
            if now - last < DIAGNOSIS_COOLDOWN_S:
                logger.info("self_heal: cooldown active for flow %s, skip diagnosis", flow_id)
                return
            self._last_diagnosis_at[flow_id] = now

            await self._diagnose(task_id, flow_id, schedule_name)
        except Exception as exc:
            logger.warning("self_heal watch failed for task %s: %s", task_id, exc)

    async def _diagnose(self, task_id: str, flow_id: str, schedule_name: str) -> None:
        model = str(self._config_service.load().get("default_model") or "")
        if not model:
            logger.info("self_heal: no default model configured, skip diagnosis")
            return

        prompt = _DIAGNOSIS_PROMPT.format(
            schedule_name=schedule_name or flow_id, task_id=task_id
        )
        logger.info("self_heal: diagnosing failed task %s (flow %s)", task_id, flow_id)

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        error_message: str | None = None
        try:
            async for event in self._orchestrator.stream(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                flow_id=flow_id,
                read_only=True,
            ):
                etype = event.get("type")
                if etype == "text":
                    text_parts.append(str(event.get("delta") or ""))
                elif etype == "tool_start":
                    tool_calls.append({
                        "id": f"sh-{uuid.uuid4().hex[:8]}",
                        "tool": str(event.get("tool") or ""),
                        "args": str(event.get("args") or ""),
                        "status": "running",
                    })
                elif etype == "tool_result" and tool_calls:
                    tool_calls[-1]["result"] = event.get("result")
                    tool_calls[-1]["status"] = "done"
                elif etype == "error":
                    error_message = str(event.get("message") or "")
        except Exception as exc:
            error_message = str(exc)

        diagnosis = "".join(text_parts).strip()
        if not diagnosis and error_message:
            logger.warning("self_heal diagnosis errored for %s: %s", task_id, error_message)
            return
        if not diagnosis:
            return

        self._append_to_chat(flow_id, prompt, diagnosis, tool_calls)
        logger.info("self_heal: diagnosis saved to chat for flow %s", flow_id)

    def _append_to_chat(
        self,
        flow_id: str,
        prompt: str,
        diagnosis: str,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        session_key = f"flow_{flow_id}"
        now_ms = int(time.time() * 1000)
        messages = self._chat_store.load(session_key)
        messages.append({
            "id": f"selfheal-u-{uuid.uuid4().hex[:12]}",
            "role": "user",
            "content": prompt,
            "createdAt": now_ms,
        })
        messages.append({
            "id": f"selfheal-a-{uuid.uuid4().hex[:12]}",
            "role": "assistant",
            "content": diagnosis,
            "toolCalls": tool_calls,
            "createdAt": now_ms,
            "finishedAt": int(time.time() * 1000),
        })
        self._chat_store.save(session_key, messages)
