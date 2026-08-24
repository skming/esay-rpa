"""跨请求的会话检查点：让「上一轮已经付过的代价」在中断后依然算数。

失败预算（连续失败次数、导航/质量熔断）和未了结的义务（审计不合格必须先修、lint 阻断未清、
必须先 inspect_page）只活在一次 stream() 的 guard_state 里。用户点停止、网络断、或者只是
发下一条「还是不行」，这些计数就全部归零，同一条昂贵的路径（改一次跑一次，run_flow 常以
分钟计）于是可以被重新走满额度。

与 [[ai_repair_ledger]] 分开存，因为生命周期不同：台账记节点级轨迹、在流程跑通时清空；
检查点记会话级预算、在**拿到最终回复**时就该清，否则明天的新需求会背着今天的熔断。

写不进去一律当没有：检查点是省钱的优化，不是正确性的前提。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.core.storage import resolve_ai_dir
from app.services.ai_phases import VERIFY_ATTEMPT_BUDGET

# 只有「重来一次要再付一次代价」的状态值得存。
# 页面探测结果、消息、requirement 文本都不在此列：它们随下一轮请求重新给。
_PERSISTED_KEYS = (
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
    "page_evidence_done", "evidence_collected", "fresh_page_evidence",
    # 本轮用户这句话的意图。跨轮留着就是把上一句的授权当这一句的——
    # run_authorized 尤其不能存：那等于在用户没点运行的情况下拉起浏览器操作真实站点
    "run_authorized", "repair_intent", "browser_chain_node_ids", "run_attempted",
    # 一次性的锁与一次性的纠正。跨轮留着会把唯一的出路也锁死（用户下一句往往正是
    # 「我过完验证了，再跑一次」），或者对同一段回复反复撤回重写
    "challenge_page_lock", "closing_statement_only", "terminal_response_only",
    "refusal_corrected", "result_claim_corrected", "verification_nudged",
    "transform_node_touched",
    # 另有归属：修复台账按 flow 单独落盘（ai_evidence_ledger），跨会话累计不靠这里
    "node_field_history", "node_selector_fix_counts",
    # 不是事实：flow_id 是检查点文件名本身，_last_tool_args 只在一次调用内传参
    "flow_id", "_last_tool_args",
})

# 中断后隔了很久再回来，页面和流程多半都变了，旧预算不该再挡人。
_STALE_SECONDS = 2 * 3600


def _checkpoint_dir() -> Path:
    return resolve_ai_dir() / "checkpoints"


def _checkpoint_path(flow_id: str) -> Path:
    return _checkpoint_dir() / f"flow_{flow_id}.json"


def load(flow_id: str | None) -> dict[str, Any]:
    """读回可续跑的 guard_state 片段；缺失/损坏/过期一律当空。"""
    if not flow_id:
        return {}
    try:
        data = json.loads(_checkpoint_path(flow_id).read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    if time.time() - float(data.get("updated_at") or 0) > _STALE_SECONDS:
        return {}
    state = data.get("state")
    if not isinstance(state, dict):
        return {}
    return {k: v for k, v in state.items() if k in _PERSISTED_KEYS}


def save(flow_id: str | None, state: dict[str, Any], *, rounds: int) -> None:
    """每轮结束调一次。全空就删文件——留一份全 0 的检查点只会让 load 白读一次。"""
    if not flow_id:
        return
    snapshot = {k: state.get(k) for k in _PERSISTED_KEYS if state.get(k)}
    if not snapshot:
        clear(flow_id)
        return
    try:
        _checkpoint_dir().mkdir(parents=True, exist_ok=True)
        _checkpoint_path(flow_id).write_text(
            json.dumps({"state": snapshot, "rounds": rounds, "updated_at": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def clear(flow_id: str | None) -> None:
    """本轮对话正常收尾时调用：预算是为「这一次任务」设的，不该跨任务累积。"""
    if not flow_id:
        return
    try:
        _checkpoint_path(flow_id).unlink(missing_ok=True)
    except Exception:
        pass


def summarize(checkpoint: dict[str, Any]) -> str | None:
    """把检查点压成一条给模型看的交代——不解释就等于凭空少了额度。

    只在真有未了结事项时出声；纯计数（还没触发锁）不值得占提示词。
    """
    lines: list[str] = []
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
