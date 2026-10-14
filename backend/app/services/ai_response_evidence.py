"""最终回复的证据检查与确定性收尾。"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable

from app.services.ai_guard_state import GuardState
from app.services.ai_tool_events import current_verification_status


_CLAUSE_BOUNDARY = re.compile(r"[，,。；;！!？?\n]|(?=但|不过|然而|同时|而且|且)")
_NEGATED_CLAIM = re.compile(
    r"(?:尚未|还未|并未|未曾|未能|未|没有|无法|不能|不可|不应|不要|不宜|不得|禁止)"
    r"(?:确认|认定|宣称|声称|说|表示|保证|视为|算作|证明|达到|做到|完成|已经|已|\s|[“‘「『\"'])*$"
)
_OLD_CLAIM_PREFIX = re.compile(
    r"(?:误称|误写|误报|误说|错写|错误地(?:说|写|称|宣称)|"
    r"(?:此前|之前|先前|上次|过去|旧|原先|原来)[^，,。；;！!？?\n]{0,20}"
    r"(?:错误|误|撤回|不准确|无依据)|错误结论|旧结论|撤回[^，,。；;！!？?\n]{0,12})$"
)
_OLD_CLAIM_SUFFIX = re.compile(
    r"^(?:[”’」』】）\s]*)"
    r"(?:这个|该|上述|先前|原先|旧)?(?:说法|结论|表述)?"
    r"(?:是|并|都|属于|只是)?"
    r"(?:错误|不准确|不成立|无依据|没有依据|未经证实|需撤回|应该撤回)"
)
_QUOTE_OPEN = "“‘「『【（\"'"


def factual_claim_text(text: str, phrases: tuple[str, ...]) -> str:
    """只屏蔽明确否定的断言本身，不能因同句其他否定词放过肯定结论。"""
    clauses = _CLAUSE_BOUNDARY.split(text)
    factual: list[str] = []
    for clause in clauses:
        stripped = clause.strip()
        if not stripped:
            continue
        quote_start = min((stripped.find(q) for q in _QUOTE_OPEN if q in stripped), default=-1)
        prefix = stripped[:quote_start] if quote_start >= 0 else ""
        if quote_start >= 0:
            quote_end = max(stripped.rfind(q) for q in "”’」』】）\"'")
            if quote_end > quote_start and (
                _OLD_CLAIM_PREFIX.search(prefix) or _OLD_CLAIM_SUFFIX.match(stripped[quote_end + 1 :].strip())
            ):
                stripped = stripped[:quote_start] + stripped[quote_end + 1:]
        for phrase in phrases:
            for match in reversed(list(re.finditer(re.escape(phrase), stripped))):
                if _NEGATED_CLAIM.search(stripped[:match.start()]):
                    stripped = stripped[:match.start()] + stripped[match.end():]
        factual.append(stripped)
    return "。".join(factual)


def result_claim_correction(
    text: str,
    state: GuardState,
    checker: Callable[[str, GuardState], str | None],
    phrases: tuple[str, ...],
) -> str | None:
    """每次检查当前回复；checker 是现有规则的唯一来源，状态副本隔离其一次性标志。"""
    factual_text = factual_claim_text(text, phrases)
    if not factual_text:
        return None
    snapshot = copy.copy(state)
    snapshot.result_claim_corrected = False
    return checker(factual_text, snapshot)


def conservative_result_reply(state: GuardState) -> str:
    """模型重写后仍越界时，用平台状态给出不依赖模型正文的收尾。"""
    if not state.flow_has_nodes and state.current_flow_revision is None:
        return (
            "本轮回复中的完成结论缺少平台证据，已撤回。当前画布没有业务节点，"
            "无法确认流程已写入。请补充所需信息后重试。"
        )

    revision = state.current_flow_revision
    target = f"当前流程 revision {revision}" if revision is not None else "当前流程"
    status = current_verification_status(state)
    if revision is None:
        if state.audit_passed:
            status = "accepted"
        elif state.run_succeeded:
            status = "run_verified"

    if status == "accepted":
        evidence = "已有平台验收记录，但本轮回复含有其他无法确认的完成结论。"
    elif status == "run_verified":
        evidence = "已有成功运行记录，尚无通过的产物验收记录。"
    else:
        evidence = "尚无对应的成功运行和通过的产物验收记录。"
    return f"本轮回复中的完成结论缺少平台证据，已撤回。{target}{evidence}请核对实际流程和运行结果后再继续。"
