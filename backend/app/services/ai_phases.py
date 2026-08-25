"""会话的阶段机与收敛判据：把「先做 X 再做 Y」和「别再原地打转」各收成一处。

这两件事原来摊在十条独立闸门里（`pre_create_inspect_gate`、`requires_inspect_page`、
`pending_repair_gate`、`requires_lint_fix`、`requires_quality_fix`、`repair_autorun_lock`、
`consecutive_inspect_limit`、`repair_cycle_lock`、`quality_budget_lock`、
`navigation_budget_lock`）。每条自己判自己的 state 键、自己定自己的作用域，于是有两类问题：

- **顺序靠人记。** 十条闸的相对优先级只存在于 GUARDS 的行号里。粗粒度熔断必须排在
  按节点计数的预算之前，否则模型每轮换个节点改就一条都不触发——这条约束一旦被
  重排破坏，没有任何症状，直到线上出现一次本该被拦下的空转。
- **额度能被绕。** 七个计数器各管一个维度，模型换个维度就是一份新额度：改节点 A 跑一次、
  改节点 B 跑一次、改 selector 跑一次，三次都不重复，三个计数器各自才 1。

这里换成两条判据：

1. **阶段**（`resolve_phase`）——由事实推导，不存。存下来的阶段是第二份真相，会像
   S1 那份「注入一次的流程定义」一样过期；判据只能每轮从 state 重算。
   阶段只约束「写流程」和「跑流程」两类工具，读工具永不受约束：挡掉读工具等于
   没收诊断手段，模型只能在剩下几个工具间空转。
2. **收敛**（`note_failed_attempt` / `note_evidence` / `note_guard_block`）——一份总预算 +
   一份证据指纹集。预算的关键在计价：**重复的失败按两份算，新的失败按一份算**。于是同一条 3 的上限
   同时复现了三个旧阈值：同一签名连错 2 次 = 1+2 = 3（旧 NAV_FAILURE_BUDGET / 质量预算），
   三次各不相同的失败 = 1+1+1 = 3（旧 MAX_REPAIR_CYCLES）。换维度不再是换额度。
   护栏拦截也走这一份（`note_guard_block`）：拦截的判定与历史无关，但「同一条拦截反复出现」
   跟改了又跑还是没成没有区别，不计价它就是唯一没有上限的空转形态。

与旧实现的两处刻意差异，都是拿一轮的余量换掉一种更贵的失败模式：

- DISCOVER 不再挡 `get_run_output`（旧 `requires_inspect_page` 挡）。省一轮换来的是
  「诊断手段永不没收」这条不变量没有例外。
- 预算耗尽（REPORT）仍放行 `apply_node_fix`（旧 `repair_cycle_lock` 挡）。它是单节点
  单字段的精准改动，做不成盲改循环的载体，而它滥用的那一面已经由
  [[ai_tools/lint_diff]] 的 selector 预算和字段回摆判据各自挡着；`run_flow` 在 REPORT
  是关的，所以这里改完也无法自证，模型仍然只能交回用户。
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from app.services.ai_guard_state import GuardState, new_budget
from app.services.ai_guards import FLOW_WRITE_TOOLS, call_fingerprint

# 「改流程 → 跑 → 又失败」的总额度。重复签名计两份，见模块 docstring 的计价说明。
VERIFY_ATTEMPT_BUDGET = 3

# 纯取证工具：调用它们只为了把外部事实搬进上下文，没有副作用。
# 重复取证判据只作用在这一组上——重复调 run_flow 是「又试了一次」，由预算管，不是打转。
EVIDENCE_TOOLS = frozenset({
    "inspect_page", "inspect_screenshot", "get_run_error", "get_run_logs", "get_run_output",
})

# 受阶段约束的工具。publish_flow / create_schedule / toggle_schedule / stop_run 不在内：
# 它们不是「构建—验证」这条主线上的动作，用户随时可能单独要求，挡掉只会答不上话。
# stop_run 尤其不能挡——它是运行失控时唯一的出路。
PHASE_GATED_TOOLS = FLOW_WRITE_TOOLS | frozenset({"run_flow"})


class Phase(str, Enum):
    """当前这一轮能推进任务的动作类别。

    取值顺序即推导顺序，也是「粗到细」：REPORT 是熔断后的终局，排在最前，
    在已经打不动的局面下报一个前置门只会把模型引向另一个同样被挡住的动作。
    """

    REPORT = "report"      # 预算耗尽：只能向用户交底
    DISCOVER = "discover"  # 缺页面证据：先看页面
    BUILD = "build"        # 流程还不存在：先建出来
    FIX = "fix"            # 有阻断诊断或审计不合格：先修
    VERIFY = "verify"      # 可以跑了


_ADMISSION: dict[Phase, frozenset[str]] = {
    Phase.REPORT: frozenset({"apply_node_fix"}),
    Phase.DISCOVER: frozenset(),
    Phase.BUILD: FLOW_WRITE_TOOLS,
    Phase.FIX: FLOW_WRITE_TOOLS,
    Phase.VERIFY: FLOW_WRITE_TOOLS | frozenset({"run_flow"}),
}

# 阶段推进不到位时，模型下一步该干什么。写成数据是因为它同时要进拦截结果
# （给模型看）和 system prompt 契约（让模型预先知道顺序，而不是撞上才知道）。
_PHASE_CONTRACTS: tuple[tuple[Phase, str], ...] = (
    (
        Phase.DISCOVER,
        "涉及页面元素时，先 inspect_page 拿真实 DOM 再写流程或改节点："
        "状态块里的诊断只读流程定义，读不到页面。selector 只能来自检查结果。",
    ),
    (
        Phase.BUILD,
        "流程还没建出来之前不要 run_flow：没有可跑的东西，只会白等一次浏览器启动。",
    ),
    (
        Phase.FIX,
        "状态块里 severity=error 的诊断、以及 acceptance_audit 给出的 repair_plan，"
        "必须先修完才能 run_flow——带着已知阻断项重跑只会再拿一次同样的失败。",
    ),
    (
        Phase.VERIFY,
        "用户只说「修一下 / 报错了」时，改完交回用户，不要顺手 run_flow；要不要重跑由用户决定。",
    ),
)

# 走到 REPORT 时向用户要什么，按压垮预算的那类失败给。
# 这是旧 navigation_budget_lock 最有价值的部分：它给的不是「我卡住了」，而是三条可执行的补充路径。
_NEEDED_FROM_USER: dict[str, list[str]] = {
    "navigation": [
        "目标页面完整 URL（path/query/hash/完整 URL 均可）",
        "或完整菜单路径（从首页开始，每一级菜单名称）",
        "或允许用户手动打开目标页后，由助手读取当前 URL 和 DOM",
    ],
    "audit": [
        "确认交付物应有的字段与行数口径（哪些行算数、哪些该排除）",
        "或一份期望结果的样例（几行即可）",
    ],
    "run_error": [
        "该站点的登录/权限前提（是否需要先登录、有无验证码）",
        "或允许用户手动把页面开到目标状态后，由助手接着读",
    ],
    # 压垮预算的是「同一条起跑前拒绝反复出现」：流程一次都没跑，要的不是诊断信息，
    # 而是清掉那条拒绝本身。具体是哪一条已经在拒绝返回里写明了，这里只给动作。
    "run_refused": [
        "在右侧「输入变量」面板补齐有引用但没有值的变量（凭据请你自己填，助手不接收）",
        "或按拒绝提示打开浏览器扩展 / 释放被占用的浏览器 profile 后告诉我再试",
    ],
    # 护栏反复拦同一条：模型已经换过几种说法都没通过校验，缺的是用户对口径的一句确认。
    # 具体拦的是哪一条写在拒绝返回里，这里只给用户能做的动作。
    "guard_refused": [
        "确认这件事按什么口径做（被拦的那条校验要求已在上一条工具返回里）",
        "或直接告诉我改用哪种做法，我按你说的执行，不再自己猜",
    ],
}

# 走到 REPORT 时那句给用户的话的开头。默认那句假定「改了流程又跑过」，
# 护栏类耗尽时流程可能一次都没落盘，照抄就是在报一件没发生的事。
_EXHAUSTED_OPENING: dict[str, tuple[str, str]] = {
    "guard_refused": (
        "同一个前置校验我已经连着撞了几次，每次换的说法都没通过。",
        "再换说法撞的还是同一条校验，需要你确认一下口径我才能接着做。",
    ),
}
_EXHAUSTED_OPENING_DEFAULT = (
    "我已经改了流程并重试了几次，仍然没有跑通。",
    "再继续盲改只会重复同样的失败，需要你补充一点信息我才能接着做。",
)

# 走到 REPORT 时收尾话的措辞。needs_user_navigation_target 与 report_to_user_and_stop
# 都在编排层的 _TERMINAL_GUARD_ACTIONS 里，区别只在给用户的那句话具体到什么程度。
_TERMINAL_ACTIONS: dict[str, str] = {
    "navigation": "needs_user_navigation_target",
}

# 阶段拦截会打出的全部 guard_id。可枚举的理由跟 GUARDS 一样：评测场景里写错一个名字
# 不会报错，只会让 expect_guards_not_triggered 永远静默通过，变成一盏假绿灯。
# 与 `_blocked` 各调用点的一致性由 test_ai_phases 的元测试守。
PHASE_GUARD_IDS = frozenset({
    "evidence_already_collected",
    "page_evidence_required",
    "flow_must_exist_before_run",
    "blocking_diagnostics_must_be_fixed",
    "audit_findings_must_be_fixed",
    "attempt_budget_exhausted",
    "run_not_authorized",
})


# ── 事实初始化 ────────────────────────────────────────────────────────────────


def initial_facts(
    *,
    flow_has_nodes: bool,
    page_evidence_required: dict[str, Any] | None,
    page_evidence_done: bool,
    run_authorized: bool,
) -> GuardState:
    """阶段机读的那几个事实，其余字段走 GuardState 的默认值（事实未知、不设限）。

    分开一个函数而不是让编排层手写字面量，是为了让「阶段读哪些事实」可枚举——
    漏掉一个键时阶段会静默地退回 BUILD，而只有 VERIFY 放行 run_flow。
    """
    return GuardState(
        flow_has_nodes=bool(flow_has_nodes),
        page_evidence_required=page_evidence_required,
        page_evidence_done=bool(page_evidence_done),
        run_authorized=bool(run_authorized),
    )


# ── 阶段推导 ──────────────────────────────────────────────────────────────────


def resolve_phase(state: GuardState) -> Phase:
    """从 state 里的事实推出当前阶段。不缓存、不存盘、每次重算。"""
    budget = state.attempt_budget or {}
    if int(budget.get("spent") or 0) >= VERIFY_ATTEMPT_BUDGET:
        return Phase.REPORT
    if state.page_evidence_required and not state.page_evidence_done:
        return Phase.DISCOVER
    if not state.flow_has_nodes:
        return Phase.BUILD
    if state.blocking_diagnostics or state.audit_findings:
        return Phase.FIX
    return Phase.VERIFY


def admitted_tool_names(all_names: frozenset[str], state: GuardState) -> frozenset[str]:
    """本轮该暴露给模型的工具名。

    与 `apply_phase_gate` 用同一张准入表：暴露了却会被拦，等于故意让模型白花一轮。
    """
    phase = resolve_phase(state)
    if phase is Phase.DISCOVER and not state.flow_has_nodes:
        # 流程还不存在时，除了「去看页面」没有任何别的动作能推进——
        # 连 get_run_error 都没有 run 可读。窄到一个工具是为了省 schema token。
        return all_names & frozenset({"inspect_page"})
    return (all_names - PHASE_GATED_TOOLS) | (all_names & _ADMISSION[phase])


# ── 准入 ──────────────────────────────────────────────────────────────────────


def _blocked(tool_name: str, guard_id: str, phase: Phase, **payload: Any) -> dict[str, Any]:
    # 沿用 guard 的 status：前端按 blocked_ 前缀渲染「已阻断」，
    # ai_tool_events 按同一前缀跳过事件写入，评测断言读的也是 guard_id。
    return {
        "status": "blocked_by_orchestrator_guard",
        "blocked_tool": tool_name,
        "guard_id": guard_id,
        "phase": phase.value,
        **payload,
    }


def apply_phase_gate(
    tool_name: str,
    args: dict[str, Any],
    state: GuardState,
) -> dict[str, Any] | None:
    """阶段与收敛判据的统一入口；返回 None 表示放行。

    在护栏之前调用：护栏判的是「这次调用本身不该发生」（凭据外泄、契约被改写），
    与失败历史无关；阶段判的是「现在还不到做这件事的时候」。
    """
    phase = resolve_phase(state)

    repeated = _check_repeated_evidence(tool_name, args, state, phase)
    if repeated is not None:
        return repeated

    if tool_name in PHASE_GATED_TOOLS and tool_name not in _ADMISSION[phase]:
        return _refuse_for_phase(tool_name, state, phase)

    if tool_name == "run_flow" and not state.run_authorized:
        return _refuse_unauthorized_run(tool_name, state, phase)

    return None


def _check_repeated_evidence(
    tool_name: str,
    args: dict[str, Any],
    state: GuardState,
    phase: Phase,
) -> dict[str, Any] | None:
    """同一次取证重复调用：证据已经在上下文里，再取一次只会拿到同一份。

    判据是「这次调用与上次之间流程没有任何变化」——写入或运行成功会清空指纹集
    （见 `note_progress`），所以改完再看同一个页面是正当的，连着看两次同一个页面不是。
    比旧的「连续 3 次」严格更准：三次探测三个不同 URL 不再被误挡，
    重复探测同一个 URL 在第 2 次就拦下而不是第 4 次。
    """
    if tool_name not in EVIDENCE_TOOLS:
        return None
    seen = state.evidence_collected or []
    fingerprint = call_fingerprint(tool_name, args)
    if fingerprint not in seen:
        return None
    return _blocked(
        tool_name,
        "evidence_already_collected",
        phase,
        required_action="use_evidence_already_in_context",
        allowed_next_tools=sorted(_ADMISSION[phase]) or ["inspect_page"],
        message=(
            f"这次 {tool_name} 的参数与本轮已经取过的一次完全相同，流程期间没有任何改动，"
            "结果只会是同一份。请直接用上下文里已有的那份证据下判断；"
            "确实需要新证据时，换目标（另一个 URL / 另一个 task_id）或先落一次改动。"
        ),
    )


def _refuse_for_phase(
    tool_name: str,
    state: GuardState,
    phase: Phase,
) -> dict[str, Any]:
    if phase is Phase.REPORT:
        return _refuse_exhausted(tool_name, state, phase)
    if phase is Phase.DISCOVER:
        required = state.page_evidence_required
        required = required if isinstance(required, dict) else {}
        payload: dict[str, Any] = {
            "required_tools": ["inspect_page"],
            "required_action": "inspect_page_first",
            "message": (
                "写流程或改节点之前必须先 inspect_page 看一眼页面现状："
                "状态块里的诊断只读流程定义，读不到 DOM，selector 只能来自检查结果。"
                "看过之后写入工具自动解锁。"
            ),
        }
        if required.get("url"):
            payload["suggested_args"] = {"url": required["url"]}
            if required.get("wait_selector"):
                payload["suggested_args"]["wait_selector"] = required["wait_selector"]
        if required.get("reason"):
            payload["reason"] = required["reason"]
        return _blocked(tool_name, "page_evidence_required", phase, **payload)
    if phase is Phase.BUILD:
        return _blocked(
            tool_name,
            "flow_must_exist_before_run",
            phase,
            required_action="build_the_flow_first",
            allowed_next_tools=sorted(FLOW_WRITE_TOOLS),
            message="流程还没有任何节点，先把流程建出来再谈运行。",
        )
    # FIX：阻断诊断优先报，它是模型手上真正能改的东西；审计问题次之。
    diagnostics = state.blocking_diagnostics or []
    if diagnostics:
        return _blocked(
            tool_name,
            "blocking_diagnostics_must_be_fixed",
            phase,
            required_action="fix_blocking_diagnostics_first",
            lint_findings=diagnostics,
            message=(
                "状态块里还有 severity=error 的诊断没修完。带着已知阻断项重跑，"
                "只会再拿一次同样的失败。"
            ),
        )
    audit = state.audit_findings
    audit = audit if isinstance(audit, dict) else {}
    return _blocked(
        tool_name,
        "audit_findings_must_be_fixed",
        phase,
        required_action="fix_audit_findings_first",
        issues=audit.get("issues") or [],
        repair_plan=audit.get("repair_plan") or [],
        message=(
            "上一次运行技术上成功了，但验收审计不合格。审计由平台自己算，"
            "你无从「再审一次」——按 repair_plan 改流程是唯一出路。"
        ),
    )


def _refuse_exhausted(
    tool_name: str,
    state: GuardState,
    phase: Phase,
) -> dict[str, Any]:
    budget = state.attempt_budget or {}
    attempts = list(budget.get("attempts") or [])
    kind = attempts[-1].get("kind") if attempts else None
    tried = [a.get("detail") for a in attempts if a.get("detail")]
    opening, closing = _EXHAUSTED_OPENING.get(kind or "", _EXHAUSTED_OPENING_DEFAULT)
    return _blocked(
        tool_name,
        "attempt_budget_exhausted",
        phase,
        required_action=_TERMINAL_ACTIONS.get(kind or "", "report_to_user_and_stop"),
        attempts=attempts,
        needed_from_user=_NEEDED_FROM_USER.get(kind or "", _NEEDED_FROM_USER["run_error"]),
        allowed_next_tools=sorted(EVIDENCE_TOOLS | {"apply_node_fix"}),
        # 拦截要能直接变成一句对用户说的话，否则用户只看到一个空气泡。
        user_message=(
            opening
            + ("已经试过的方向：" + "；".join(str(t) for t in tried) + "。" if tried else "")
            + closing
        ),
    )


def _refuse_unauthorized_run(
    tool_name: str,
    state: GuardState,
    phase: Phase,
) -> dict[str, Any]:
    """用户只要求修复时，改完不许顺手把流程跑起来。

    拦错了，用户补一句「跑一下」；放行错了，就是在用户没点运行的情况下拉起浏览器
    去操作真实站点。所以宁可偏向拦。

    修复是否已经落盘决定这轮该不该收尾：落了盘，剩下的只是「要不要跑」这一个决定，
    交给用户（ask_user 会让编排层进收尾态）；还没落盘，这轮活没干完，不能收尾。
    """
    landed = state.current_flow_revision is not None
    return _blocked(
        tool_name,
        "run_not_authorized",
        phase,
        required_action="ask_user" if landed else "explain_and_wait",
        message=(
            "用户这一轮要的是修复，没有要求运行。改完把结论交回用户，"
            "由用户决定要不要重跑——run_flow 会拉起真实浏览器去操作站点。"
        ),
    )


# ── 收敛记账 ──────────────────────────────────────────────────────────────────


def note_failed_attempt(
    state: GuardState,
    *,
    kind: str,
    signature: str,
    detail: str | None = None,
    charge_only_if_repeated: bool = False,
) -> dict[str, Any]:
    """记一次「改了再跑还是没成」。

    重复签名计两份：这是把三个旧计数器合成一个的关键。同一个节点、同一个质量问题、
    同一条错误连着失败两次，就等于花掉全部额度——换维度不再是换额度。

    `charge_only_if_repeated` 给起跑前就被拒的运行用：这份额度定价的是「真跑过一次」的
    代价，没起跑就收费会把一条本该由用户清除的拦路条件（凭据为空、扩展未连、熔断锁）
    变成自我加固——拒绝在花掉产生这条拒绝的额度。但仍然要记签名：同一条拒绝再来一次
    就是原地打转，从第二次起按普通重复失败计价，否则这类拒绝一条收口都没有。
    """
    budget = state.attempt_budget
    if not isinstance(budget, dict):
        budget = state.attempt_budget = new_budget()
    signatures: dict[str, int] = budget.setdefault("signatures", {})
    repeat = signature in signatures
    if repeat:
        cost = 2
    else:
        cost = 0 if charge_only_if_repeated else 1
    budget["spent"] = int(budget.get("spent") or 0) + cost
    signatures[signature] = signatures.get(signature, 0) + 1
    budget.setdefault("attempts", []).append({
        "kind": kind,
        "signature": signature,
        "detail": detail,
        "repeat": repeat,
    })
    return budget


def note_guard_block(
    state: GuardState,
    tool_name: str,
    blocked: dict[str, Any],
) -> dict[str, Any]:
    """把一次护栏拦截计入同一份收敛预算；额度见底时改判为收尾。

    护栏本身的判定与失败历史无关（见 [[ai_guards]] 的策略表注释），所以计价只能挂在
    外面这一层。缺这一笔的代价实测过：acceptance_contract_sources_must_match_user
    在一个会话里连拦 11 次，模型每轮换个说法重试，烧掉 294k prompt token，
    而阶段机、失败预算、重复取证判据一个都数不到——护栏拦截对它们是不存在的事件。

    首次不计价（`charge_only_if_repeated`）：护栏第一次拦下来是在纠正一个具体错误，
    模型改对就能过，让这条拒绝花掉它自己的改正额度等于拒绝自我加固。同一条从第二次起
    按重复失败计价，于是第三次撞上同一面墙时额度正好见底——与起跑前被拒的收口同一套定价。

    签名只含 guard_id，不含工具名：同一条 check 换个工具调用是同一面墙，
    带上工具名就又是一份新额度（见模块 docstring 的「换维度不再是换额度」）。
    """
    guard_id = str(blocked.get("guard_id") or "unknown")
    note_failed_attempt(
        state,
        kind="guard_refused",
        signature=f"guard:{guard_id}",
        detail=str(blocked.get("message") or guard_id)[:200],
        charge_only_if_repeated=True,
    )
    budget = state.attempt_budget or {}
    if int(budget.get("spent") or 0) < VERIFY_ATTEMPT_BUDGET:
        return blocked
    # 额度见底后不能再返回护栏那条「改对就能过」的提示：模型已经照它改过三次了。
    # 换成收尾拦截，让编排层走 _TERMINAL_GUARD_ACTIONS 那条路把话交回用户。
    return _refuse_exhausted(tool_name, state, Phase.REPORT)


def reclassify_last_attempt(state: GuardState, *, kind: str) -> None:
    """把最近一次失败重新归类，不动额度。

    一次失败的运行只能扣一次费，但「是哪一类失败」往往要等 get_run_error 读完现场
    才知道。归类决定 REPORT 时向用户要什么——导航类要的是目标 URL，别的类不是——
    所以这里只改 kind：再扣一次费等于同一次失败按两次算，预算会比设计的早一轮见底。

    只覆盖 run_error 这个默认归类：audit / navigation 都是已经判明的类别，
    后来的读取不该把它们冲掉。
    """
    budget = state.attempt_budget
    if not isinstance(budget, dict):
        return
    attempts = budget.get("attempts") or []
    if not attempts or not isinstance(attempts[-1], dict):
        return
    if attempts[-1].get("kind") != "run_error":
        return
    attempts[-1]["kind"] = kind


def note_verified(state: GuardState) -> None:
    """流程跑通且审计合格：额度归零，未了结的义务一并清掉。"""
    state.attempt_budget = new_budget()
    state.blocking_diagnostics = []
    state.audit_findings = None
    note_progress(state)


def note_evidence(state: GuardState, tool_name: str, args: dict[str, Any]) -> None:
    """记下一次已经拿到的取证调用，供重复取证判据比对。"""
    if tool_name not in EVIDENCE_TOOLS:
        return
    fingerprint = call_fingerprint(tool_name, args)
    if fingerprint not in state.evidence_collected:
        state.evidence_collected.append(fingerprint)


def note_progress(state: GuardState) -> None:
    """流程真的变了（写入落盘或跑了一次）：旧证据不再算「已经有了」。

    页面和运行结果都可能因为这次改动而不同，此时重探同一个目标是正当动作。
    """
    state.evidence_collected = []


# ── 提示词与自省 ──────────────────────────────────────────────────────────────


def phase_contract_lines() -> list[str]:
    """写进 system prompt 的阶段契约。

    只收录「模型需要预先知道才能改变行为」的顺序约束。收敛预算刻意不进——
    提前告诉它「三次会被锁」，等于把上限当额度用。
    """
    return [f"- {contract}" for _, contract in _PHASE_CONTRACTS]


def describe_phases() -> list[dict[str, Any]]:
    """给文档/调试面板用的阶段清单。"""
    contracts = dict(_PHASE_CONTRACTS)
    return [
        {
            "phase": phase.value,
            "precedence": index,
            "admits": sorted(_ADMISSION[phase]),
            "contract": contracts.get(phase),
        }
        for index, phase in enumerate(Phase)
    ]


__all__ = [
    "EVIDENCE_TOOLS",
    "PHASE_GATED_TOOLS",
    "PHASE_GUARD_IDS",
    "VERIFY_ATTEMPT_BUDGET",
    "Phase",
    "admitted_tool_names",
    "apply_phase_gate",
    "describe_phases",
    "initial_facts",
    "new_budget",
    "note_evidence",
    "note_failed_attempt",
    "note_guard_block",
    "note_progress",
    "note_verified",
    "phase_contract_lines",
    "reclassify_last_attempt",
    "resolve_phase",
]
