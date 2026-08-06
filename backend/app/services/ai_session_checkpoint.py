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

# 只有「重来一次要再付一次代价」的状态值得存。
# 页面探测结果、消息、requirement 文本都不在此列：它们随下一轮请求重新给。
_PERSISTED_KEYS = (
    "failed_run_cycles",
    "repair_cycle_lock",
    "failure_budget_lock",
    "navigation_failure_counts",
    "navigation_budget_lock",
    "quality_issue_counts",
    "quality_budget_lock",
    "requires_inspect_page",
    "requires_quality_fix",
    "requires_lint_fix",
    # 例外：这条不是预算而是「本次证据只能证明哪条执行通道」。正常轮次由
    # _resolve_resumable_task_state 从对话历史里重算，这里存的是前端裁剪过历史、
    # 或进程重启后仍要认的那一份——判据只有一条，来源可以有两条。
    # 解锁不依赖过期：任何一次成功的浏览器 inspect_page 都会把它覆写成 browser_dom。
    "page_evidence_source",
)

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
    if checkpoint.get("repair_cycle_lock"):
        cycles = int((checkpoint["repair_cycle_lock"] or {}).get("cycles") or 0)
        lines.append(f"- 本任务已经「改了再跑」失败 {cycles} 次并触发熔断，不要再试同类改动，直接向用户说明根因判断。")
    elif int(checkpoint.get("failed_run_cycles") or 0) > 0:
        lines.append(f"- 本任务此前已失败运行 {int(checkpoint['failed_run_cycles'])} 次，剩余重试额度有限。")
    if checkpoint.get("requires_quality_fix"):
        lines.append("- 上次 assert_run_output 未通过且尚未修复，必须先改流程结构再重跑。")
    if checkpoint.get("requires_lint_fix"):
        lines.append("- 上次 lint 的阻断级问题尚未修完，run_flow 会被阻断。")
    if checkpoint.get("requires_inspect_page"):
        lines.append("- 上次运行报了 selector 超时，必须先 inspect_page 拿真实 DOM 才能继续改。")
    if checkpoint.get("page_evidence_source") == "scrapling_static":
        lines.append(
            "- 本任务的页面证据来自静态 HTTP 抓取（浏览器通道当时拿不到真实页面）："
            "只能用 browser.fetch + fetcher='static'，加 browser.open/click 或改成 dynamic/stealthy 都会被阻断。"
            "若需要浏览器交互，先重新 inspect_page 确认浏览器通道已经可用。"
        )
    if not lines:
        return None
    return "【上次未完成的会话】上一轮在中途结束，以下状态继续有效：\n" + "\n".join(lines)
