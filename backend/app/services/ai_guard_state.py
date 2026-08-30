"""guard_state 的类型化载体：把散在三个模块间、靠字符串键对齐的状态包收成带 slots 的 dataclass。

原先 guard_state 是 dict[str, Any]，36 个键分散在编排层写、ai_phases 与 ai_guards 读，
全靠键名字符串对齐，而项目没有静态类型检查。写错一个键名（state["flow_has_node"]=…）不会报错，
只会让读侧永远拿到默认值——阶段机会因此卡死在 BUILD 或漏判授权，表现和「没做过这个功能」一样。
slots=True 让「写一个没声明的字段」在运行期立刻 AttributeError，测试即刻暴露，
把这层跨模块隐式契约变成显式。

放在这个无依赖的底层模块（而不是 ai_phases 或 ai_guards）是为了不破坏分层：ai_guards 只在
TYPE_CHECKING 下引用它（运行期零 ai_* 依赖的性质保留），ai_phases 运行期引用它，两者不必互相依赖。
持久化仍走 dict——检查点是 JSON，apply_checkpoint 只把已知字段写回，未知键（版本漂移）直接丢弃。
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any


def new_budget() -> dict[str, Any]:
    """一份全新的收敛预算。attempt_budget 刻意保持 dict：它的增删由 ai_phases 的 note_* 就地改写，
    嵌套结构自成一体，拆成 dataclass 只会把预算逻辑拖进这个底层模块。"""
    return {"spent": 0, "signatures": {}, "attempts": []}


def is_valid_checkpoint_value(key: str, value: Any) -> bool:
    """检查点字段的 JSON 形状校验；坏值宁可丢弃，也不能污染本轮状态。"""
    if key == "attempt_budget":
        if not isinstance(value, dict):
            return False
        spent = value.get("spent", 0)
        signatures = value.get("signatures", {})
        attempts = value.get("attempts", [])
        return (
            isinstance(spent, int)
            and not isinstance(spent, bool)
            and spent >= 0
            and isinstance(signatures, dict)
            and all(
                isinstance(signature, str)
                and isinstance(count, int)
                and not isinstance(count, bool)
                and count >= 0
                for signature, count in signatures.items()
            )
            and isinstance(attempts, list)
            and all(isinstance(attempt, dict) for attempt in attempts)
        )
    if key in {
        "failure_budget_lock",
        "audit_findings",
        "navigation_failure_hint",
        "page_evidence_required",
    }:
        return isinstance(value, dict)
    if key == "runtime_escape_findings":
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)
    if key == "page_evidence_source":
        return isinstance(value, str)
    return False


@dataclass(slots=True)
class GuardState:
    """一次 stream() 里贯穿始终的会话状态。字段按生命周期分组，全部带默认值：
    默认局面是「事实未知、不设限」，编排层的意图接线再逐项收紧（见 ai_orchestrator 的初始化）。

    run_authorized 默认 False 是 fail-closed 的一环：VERIFY 是唯一放行 run_flow 的阶段，
    未显式授权就不该拉起真实浏览器。
    """

    # 阶段机读的事实（见 ai_phases.resolve_phase）
    flow_has_nodes: bool = False
    page_evidence_required: dict[str, Any] | None = None
    page_evidence_done: bool = False
    run_authorized: bool = False
    blocking_diagnostics: list[Any] = field(default_factory=list)
    audit_findings: dict[str, Any] | None = None
    attempt_budget: dict[str, Any] = field(default_factory=new_budget)
    evidence_collected: list[str] = field(default_factory=list)

    # 验收台账事实：构造时由 load_verification_state 给，本轮内由
    # reduce_evidence_state 按工具事件推进（写流程作废已验证的 revision）
    current_flow_revision: int | None = None
    run_verified_revision: int | None = None
    accepted_revision: int | None = None

    # 修复台账（ai_repair_ledger）跨会话累计的节点级轨迹，会写回检查点
    repair_sessions: int = 0
    node_field_history: dict[str, Any] = field(default_factory=dict)
    node_selector_fix_counts: dict[str, Any] = field(default_factory=dict)

    # 运行期才暴露、静态诊断算不出、需跨轮存的逃逸项
    runtime_escape_findings: list[Any] = field(default_factory=list)

    # 一次性的锁与提示：命中即跨轮生效，直到会话结束
    challenge_page_lock: dict[str, Any] | None = None
    failure_budget_lock: dict[str, Any] | None = None
    navigation_failure_hint: dict[str, Any] | None = None

    # 本轮意图与能力（每轮重算，刻意不跨轮存）
    read_only_tools: bool = False
    model_no_vision: bool = False
    user_requirement_text: str | None = None
    latest_user_message: str | None = None
    active_task: dict[str, Any] | None = None
    turn_intent_actionable: bool = False
    repair_intent: str | None = None
    browser_chain_node_ids: list[str] = field(default_factory=list)
    page_evidence_source: str | None = None

    # 本轮工具执行的结果与一次性纠正标志（每轮重算）
    run_attempted: bool = False
    run_succeeded: bool = False
    audit_passed: bool = False
    fresh_page_evidence: bool = False
    transform_node_touched: bool = False
    result_claim_corrected: bool = False
    verification_nudged: bool = False
    refusal_corrected: bool = False
    closing_statement_only: bool = False
    terminal_response_only: bool = False

    # 传参用的临时位：不是事实，只在一次工具调用内有效
    flow_id: str | None = None
    _last_tool_args: dict[str, Any] | None = None

    def apply_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """把中断续跑的检查点（已过滤且校验过的 dict）写回本状态。

        只认当前声明过的字段：老检查点里被删掉的键、或更新版本还不认识的键，
        以及类型已经损坏的值，直接丢弃而不抛错——检查点是省钱的优化，
        不该因版本漂移或半写文件把整轮请求带崩。
        """
        known = {f.name for f in fields(self)}
        for key, value in checkpoint.items():
            if key in known and is_valid_checkpoint_value(key, value):
                setattr(self, key, value)
