"""跨请求保留失败预算和未完成的修复要求，避免中断重置额度。

验收完成且无未完成义务时清空；按 flow_id + task_key 隔离，诊断请求不改交互状态。
节点级修复轨迹由生命周期不同的 ai_repair_ledger 管理。
LangGraph SqliteSaver 只保存语义检查点，不重放图节点或浏览器副作用。
检查点写入失败不阻断任务，正确性不能依赖检查点可用。"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import time
import sqlite3
from contextlib import closing, contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from app.core.storage import resolve_ai_dir
from app.services.ai_guard_state import is_valid_checkpoint_value
from app.services.ai_phases import VERIFY_ATTEMPT_BUDGET
from app.services.ai_tool_events import current_verification_status

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # 类型标注不引入加载期的反向依赖。
    from app.services.ai_guard_state import GuardState

# 只有「重来一次要再付一次代价」的状态值得存。
# 页面探测结果、消息、requirement 文本都不在此列：它们随下一轮请求重新给。
_PERSISTED_KEYS = (
    "task_key", "pending_run", "run_accounted_key",
    # 收敛预算：进程重启不该重新发一份试错额度，否则「一路盲改」只要断线就能续上。
    "attempt_budget",
    "failure_budget_lock",
    "audit_findings",
    "navigation_failure_hint",
    # 运行期报出的取证义务（selector 超时 → 必须先看真实 DOM）。静态诊断算不出它，
    # 丢了下一轮就直接改 selector 再重跑，正是最贵的那条空转路径。
    # 配套的 page_evidence_done 刻意不存：它每轮从对话历史重算，
    # 中断后没探过就还是 False，探过了义务自然解除。
    "page_evidence_required",
    # 静态诊断刻意不存：它每轮由状态块重算，存一份只会在流程已经修好之后还挡着人。
    # 运行期逃逸不同——它的前提是静态扫描看不见它，重算重算不出来，只能存。
    "runtime_escape_findings",
    # 例外：这条不是预算而是「本次证据只能证明哪条执行通道」。正常轮次由
    # _resolve_resumable_task_state 从对话历史里重算，这里存的是前端裁剪过历史、
    # 或进程重启后仍要认的那一份——判据只有一条，来源可以有两条。
    # 解锁不依赖过期：任何一次成功的浏览器 inspect_page 都会把它覆写成 browser_dom。
    "page_evidence_source",
)

# 刻意不进检查点的键。这份名单要能枚举，理由跟 GUARDS 一样：漏一个不会报错，
# 只会在中断续跑后静默丢掉一条义务——`page_evidence_required` 就这么丢过一次，
# 表现和「没做过这个功能」完全一致。元测试保证编排层写过的每个键都落在两张表之一，
# 新增一个键时必须在这里表态「为什么不用存」，不能靠没人想起来它。
_PER_ROUND_KEYS = frozenset({
    # 每轮由状态块或对话历史重算：存一份只会在事实已经变了之后还按旧的判
    "blocking_diagnostics", "flow_has_nodes", "run_succeeded", "audit_passed",
    "page_evidence_done", "evidence_collected", "evidence_refresh", "fresh_page_evidence",
    # 每轮由 build_flow_state 从流程的 acceptance_contract 重算（草稿无契约即为 False）。
    # 存下来只会在用户中途补齐契约后还按旧的「仍是草稿」挡着运行。
    "acceptance_contract_initialized",
    # 本轮用户这句话的意图。跨轮留着就是把上一句的授权当这一句的——
    # run_authorized 尤其不能存：那等于在用户没点运行的情况下拉起浏览器操作真实站点
    "run_authorized", "repair_intent", "browser_chain_node_ids", "run_attempted",
    # 一次性的锁与一次性的纠正。跨轮留着会把唯一的出路也锁死（用户下一句往往正是
    # 「我过完验证了，再跑一次」），或者对同一段回复反复撤回重写
    "challenge_page_lock", "closing_statement_only", "terminal_response_only",
    "refusal_corrected", "result_claim_corrected", "verification_nudged",
    "fake_tool_call_corrected",
    "transform_node_touched",
    # 另有归属：修复台账按 flow 单独落盘（ai_evidence_ledger），跨会话累计不靠这里
    "node_field_history", "node_selector_fix_counts",
    # 同样另有归属：验收台账已按 flow_id + revision 落盘，每轮开场由
    # load_verification_state 重算。存一份反而更危险——流程在会话之外被改过，
    # 旧的 accepted_revision 会让「这版已验收」凭空成立
    "current_flow_revision", "current_definition_digest", "run_verified_revision", "accepted_revision", "verification_task_id",
    # flow_id 已用于线程标识；_last_tool_args 只在一次调用内传参。
    "flow_id", "_last_tool_args",
    # 本轮请求本身：下一轮换了一句话，这些就全过期了
    "latest_user_message", "user_requirement_text", "turn_intent_actionable", "active_task",
    # 调用方与模型这一轮的授权/能力，由 stream() 的入参决定，不是会话事实：
    # 存下来会让下一轮沿用上一轮的调用方授权（自愈诊断的只读模式尤其不能漏出去）
    "read_only_tools", "model_no_vision",
    # 另有归属：修复台账（ai_repair_ledger）按 flow 落盘，每轮开场重读
    "repair_sessions",
})

# 中断后隔了很久再回来，页面和流程多半都变了，旧预算不该再挡人。
_STALE_SECONDS = 2 * 3600


def _checkpoint_dir() -> Path:
    return resolve_ai_dir() / "checkpoints"


def _checkpoint_path() -> Path:
    return _checkpoint_dir() / "langgraph.sqlite3"


@contextmanager
def _checkpointer() -> Iterator[SqliteSaver]:
    serde = JsonPlusSerializer(allowed_json_modules=[], allowed_msgpack_modules=[])
    with closing(sqlite3.connect(str(_checkpoint_path()), timeout=0.2)) as connection:
        yield SqliteSaver(connection, serde=serde)


def _config(flow_id: str, task_key: str = "") -> dict[str, Any]:
    identity = json.dumps([flow_id, task_key], separators=(",", ":"))
    return {"configurable": {"thread_id": identity, "checkpoint_ns": ""}}


def _validated_state(data: Any, *, task_key: str | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    try:
        updated_at = float(data.get("updated_at"))
    except (TypeError, ValueError):
        return {}
    age = time.time() - updated_at
    if not math.isfinite(updated_at) or age < 0 or age > _STALE_SECONDS:
        return {}
    state = data.get("state")
    if not isinstance(state, dict) or (task_key is not None and state.get("task_key", "") != task_key):
        return {}
    persisted = {k: v for k, v in state.items() if k in _PERSISTED_KEYS}
    if any(not is_valid_checkpoint_value(key, value) for key, value in persisted.items()):
        return {}
    return persisted


def load(flow_id: str | None, *, task_key: str | None = None) -> dict[str, Any]:
    """精确读取任务；缺少任务身份时，仅恢复唯一且未过期的检查点。"""
    if not flow_id or not _checkpoint_path().is_file():
        return {}
    try:
        with _checkpointer() as saver:
            if task_key is not None:
                stored = saver.get_tuple(_config(flow_id, task_key))
                return _validated_state(stored.checkpoint["channel_values"], task_key=task_key) if stored else {}
            candidates = [
                state for item in saver.list(None, filter={"flow_id": flow_id})
                if (state := _validated_state(item.checkpoint["channel_values"], task_key=None))
            ]
        return candidates[0] if len(candidates) == 1 else {}
    except Exception:
        logger.warning("AI checkpoint load failed (flow_id=%s)", flow_id, exc_info=True)
        return {}


def save(flow_id: str | None, state: GuardState, *, rounds: int) -> None:
    if not flow_id or state.read_only_tools:
        return
    snapshot = {k: v for k in _PERSISTED_KEYS if (v := getattr(state, k, None))}
    task_key = snapshot.pop("task_key", "")
    budget = snapshot.get("attempt_budget")
    if isinstance(budget, dict) and not any(budget.get(key) for key in ("spent", "signatures", "attempts")):
        snapshot.pop("attempt_budget", None)
    if not snapshot:
        clear(flow_id, task_key=task_key)
        return
    if task_key:
        snapshot["task_key"] = task_key
    try:
        _checkpoint_dir().mkdir(parents=True, exist_ok=True)
        with _checkpointer() as saver:
            checkpoint = empty_checkpoint()
            checkpoint["id"] = "latest"
            checkpoint["channel_values"] = {"state": snapshot, "rounds": rounds, "updated_at": time.time()}
            saver.put(_config(flow_id, task_key), checkpoint, {
                "source": "update", "step": rounds, "parents": {}, "flow_id": flow_id,
            }, {})
    except Exception:
        logger.warning("AI checkpoint save failed (flow_id=%s)", flow_id, exc_info=True)


def finish(flow_id: str | None, state: GuardState, *, rounds: int) -> None:
    if state.read_only_tools:
        return
    run = state.pending_run or {}
    unfinished = run.get("status") in {"queued", "pending", "running", "timeout", "awaiting_confirmation"}
    budget = state.attempt_budget
    unfinished_attempts = any(budget.get(key) for key in ("spent", "signatures", "attempts")) and current_verification_status(state) != "accepted"
    if (unfinished or unfinished_attempts or state.audit_findings or state.runtime_escape_findings
            or state.failure_budget_lock or state.navigation_failure_hint
            or (state.page_evidence_required and not state.page_evidence_done)):
        save(flow_id, state, rounds=rounds)
    else:
        clear(flow_id, task_key=state.task_key or "")


def clear(flow_id: str | None, *, task_key: str = "") -> None:
    if not flow_id or not _checkpoint_path().is_file():
        return
    try:
        with _checkpointer() as saver:
            saver.delete_thread(_config(flow_id, task_key)["configurable"]["thread_id"])
    except Exception:
        logger.warning("AI checkpoint clear failed (flow_id=%s)", flow_id, exc_info=True)


def clear_flow(flow_id: str) -> None:
    """显式清理流程下的全部任务检查点，仅用于隔离评测的重置。"""
    if not _checkpoint_path().is_file():
        return
    try:
        with _checkpointer() as saver:
            for item in list(saver.list(None, filter={"flow_id": flow_id})):
                saver.delete_thread(item.config["configurable"]["thread_id"])
    except Exception:
        logger.warning("AI checkpoint flow reset failed (flow_id=%s)", flow_id, exc_info=True)


async def _offload(operation: Any, *args: Any, **kwargs: Any) -> Any:
    # 提交回执等记账必须收尾后才能传播取消，避免已入队却丢掉可恢复的 task_id。
    worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await worker
        raise


async def aload(flow_id: str | None, *, task_key: str | None = None) -> dict[str, Any]:
    return await _offload(load, flow_id, task_key=task_key)


async def asave(flow_id: str | None, state: GuardState, *, rounds: int) -> None:
    await _offload(save, flow_id, copy.deepcopy(state), rounds=rounds)


async def afinish(flow_id: str | None, state: GuardState, *, rounds: int) -> None:
    await _offload(finish, flow_id, copy.deepcopy(state), rounds=rounds)


async def aclear(flow_id: str | None, *, task_key: str = "") -> None:
    await _offload(clear, flow_id, task_key=task_key)


def summarize(checkpoint: dict[str, Any]) -> str | None:
    """把检查点压成一条给模型看的交代——不解释就等于凭空少了额度。

    只在真有未了结事项时出声；纯计数（还没触发锁）不值得占提示词。
    """
    lines: list[str] = []
    if checkpoint.get("pending_run"):
        lines.append("- 已提交的运行会从任务服务恢复状态，请观察现有任务，不要重复启动。")
    budget = checkpoint.get("attempt_budget") if isinstance(checkpoint.get("attempt_budget"), dict) else {}
    spent = int(budget.get("spent") or 0)
    if spent >= VERIFY_ATTEMPT_BUDGET:
        lines.append("- 本任务的重试额度已经耗尽，不要再试同类改动，直接向用户说明根因判断与已试方向。")
    elif spent > 0:
        # 只说「用过了」，不报剩余数字：报数字等于把上限当额度用。
        tried = [a.get("detail") for a in (budget.get("attempts") or []) if isinstance(a, dict) and a.get("detail")]
        lines.append(
            "- 本任务此前已经「改了再跑」失败过，剩余重试额度有限"
            + ("，已试过的方向：" + "；".join(str(t) for t in tried) + "。" if tried else "。")
        )
    if checkpoint.get("audit_findings"):
        lines.append("- 上次运行的 acceptance_audit 未通过且尚未修复，必须先改流程结构再重跑。")
    if checkpoint.get("runtime_escape_findings"):
        lines.append("- 上次运行报出了静态检查漏掉的未定义变量，尚未修完，run_flow 会被阻断。")
    if checkpoint.get("navigation_failure_hint"):
        lines.append("- 上次运行是导航节点点不动，不要继续盲改同一 selector；改用 browser.open 直达目标 URL。")
    required = checkpoint.get("page_evidence_required")
    if isinstance(required, dict):
        url = required.get("url") or required.get("last_browser_url")
        lines.append(
            "- 上次运行报了 selector 超时，必须先 inspect_page 拿真实 DOM 才能继续改"
            + (f"（url={url}）。" if url else "。")
        )
    if checkpoint.get("page_evidence_source") == "scrapling_static":
        lines.append(
            "- 本任务的页面证据来自静态 HTTP 抓取（浏览器通道当时拿不到真实页面）："
            "只能用 browser.fetch + fetcher='static'，加 browser.open/click 或改成 dynamic/stealthy 都会被阻断。"
            "若需要浏览器交互，先重新 inspect_page 确认浏览器通道已经可用。"
        )
    if not lines:
        return None
    return "【上次未完成的会话】上一轮在中途结束，以下状态继续有效：\n" + "\n".join(lines)
