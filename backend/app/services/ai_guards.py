"""编排层硬护栏的策略表：判「这次调用本身不该发生」。

护栏挡的是「模型明知规则仍会犯」的那类错——所以它不能写在 prompt 里靠自觉，
必须在工具真正执行之前拦下来。这里把每一条拦截做成一个 `Guard` 条目而不是
一串 if，是为了让「顺序即优先级」写成数据、能被读出来，而不是藏在行号里。

判据的边界：**只看这次调用的参数与当前授权，不看失败历史。**
凭据外泄、契约被改写、只读模式、模型没有视觉、证据通道≠执行通道、整页人机验证——
这些成立与否与之前跑了几次、改了几个节点全都无关。

带历史的那一半（「先做 X 再做 Y」和「别再原地打转」）已经搬去 [[ai_phases]]：
它们原来是十条独立闸门，各自一个 state 键、各自一份计数，有两个无症状的坑——
相对优先级只存在于本表的行号里（重排即失效），七个计数器各管一个维度
（模型换个维度就是一份新额度）。搬过去换成一条阶段推导 + 一份总预算。

副产物有三个，都是「能枚举」直接换来的：
- `guard_contract_lines()` 把契约摘要注入 system prompt，prompt 不再手抄一遍规则；
- 每条 guard 有稳定 `id`，测试按 id 逐条断言，`tests/test_ai_guards.py` 还能反过来
  检查有没有新增了却没人测的 guard；
- 拦截结果里带 `guard_id`，前端和日志能区分是哪道闸。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

# ── 工具分组 ──────────────────────────────────────────────────────────────────

# 自愈诊断等只读场景禁用的写入类工具
WRITE_TOOLS = frozenset({
    "create_flow", "update_flow", "apply_node_fix", "set_acceptance_contract", "run_flow", "publish_flow",
    "stop_run", "create_schedule", "toggle_schedule",
})

FLOW_WRITE_TOOLS = frozenset({"create_flow", "update_flow", "apply_node_fix", "set_acceptance_contract"})

# 可并发起跑的工具：纯读、无副作用、不参与收敛记账。
# 不含 inspect_page / inspect_screenshot / get_run_error——它们会写证据指纹与
# fresh_page_evidence，并发会让「同一次取证重复调用」判不出先后。
PARALLEL_SAFE_TOOLS = frozenset({
    "get_run_logs",
    "get_run_output",
    "list_node_types",
    "list_schedules",
})

_CREDENTIAL_NAME_TOKENS = (
    "password", "passwd", "pwd", "token", "secret", "api_key", "apikey", "credential",
)


def exposed_credential_values(input_variables: Any) -> list[str]:
    """挑出「声明成凭据却带着非空值」的变量名。

    判据放在这里而不是各自实现一份，是因为它有两个执行点：这一层在调用前拦下来
    （模型还能改），执行器在写盘前再判一次（真正拥有这条不变量的层）。
    两份实现会各自演化，而这条判据判漏的代价是秘密值落进流程定义。

    `defaultValue` 必须一起看：执行器把它当 `value` 的输入别名收下
    （见 executor._create_flow），只看 `value` 等于留了一条同样能写进存储的路。
    """
    exposed: list[str] = []
    for item in input_variables or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or item.get("defaultValue") or "").strip()
        if not value:
            continue
        name = str(item.get("name") or "")
        lowered = name.lower()
        if (
            item.get("category") == "credential"
            or item.get("sensitive") is True
            or any(token in lowered for token in _CREDENTIAL_NAME_TOKENS)
        ):
            exposed.append(name or "<unnamed>")
    return exposed


# ── 策略表结构 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolScope:
    """一条 guard 作用在哪些工具上。

    include=None 表示「除 exclude 外的全部工具」——failure budget 这类闸就是这么定的：
    它要挡的是「还没定位根因就动手」，而不是某几个具体工具。
    """

    include: frozenset[str] | None = None
    exclude: frozenset[str] = frozenset()

    def matches(self, tool_name: str) -> bool:
        if tool_name in self.exclude:
            return False
        return self.include is None or tool_name in self.include

    def describe(self) -> str:
        if self.include is not None:
            return "/".join(sorted(self.include))
        if self.exclude:
            return f"除 {'/'.join(sorted(self.exclude))} 外的全部工具"
        return "全部工具"


GuardCheck = Callable[[str, dict[str, Any], dict[str, Any]], "dict[str, Any] | None"]


@dataclass(frozen=True)
class Guard:
    """一条硬拦截。

    `contract` 是写进 system prompt 的一句话；留空表示这条不必让模型预先知道
    （比如「连续 inspect 三次」——提前说反而像在鼓励它数次数）。
    """

    id: str
    summary: str
    scope: ToolScope
    check: GuardCheck
    contract: str | None = None
    requires_state: tuple[str, ...] = field(default=())

    def applies(self, tool_name: str, state: dict[str, Any]) -> bool:
        if not self.scope.matches(tool_name):
            return False
        # 声明了前置 state 键的 guard，键为空时整条跳过——省掉每个 check 开头
        # 重复一遍 `if not state.get(...)`，也让「这条闸何时生效」能被读出来
        return all(state.get(key) for key in self.requires_state)


# ── 通用小工具 ────────────────────────────────────────────────────────────────


def call_fingerprint(tool_name: str, args: dict[str, Any]) -> str:
    """一次调用的身份。编排层记账与 guard 判定必须用同一个算法，否则两边永远对不上。

    sort_keys 是必需的：模型两次发出的 JSON 字段顺序可能不同，语义完全一样。
    """
    try:
        payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(sorted(args.items())) if isinstance(args, dict) else repr(args)
    return f"{tool_name}:{payload}"


def _blocked(tool_name: str, **payload: Any) -> dict[str, Any]:
    return {"status": "blocked_by_orchestrator_guard", "blocked_tool": tool_name, **payload}


def _check_credential_values_in_flow(
    tool_name: str,
    args: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    del state
    exposed = exposed_credential_values(args.get("input_variables"))
    if not exposed:
        return None
    return _blocked(
        tool_name,
        guard_id="credential_values_must_stay_out_of_ai_tools",
        required_action="use_empty_credential_variables",
        exposed_variables=exposed,
        message=(
            f"凭据变量 {exposed} 含非空值，已阻止写入。"
            "请把 value 清空，仅保留 category='credential'/sensitive 标记，"
            "并让用户在右侧输入变量面板配置秘密值。"
        ),
    )


def _check_acceptance_contract_change(
    tool_name: str,
    args: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    quote = str(args.get("requirement_change_quote") or "").strip()
    latest = str(state.get("latest_user_message") or "")
    if quote and quote in latest:
        return None
    return _blocked(
        tool_name,
        required_action="preserve_acceptance_contract",
        message=(
            "验收契约只能在用户本轮明确改变交付目标时修改。"
            "requirement_change_quote 必须是用户最新消息中的连续原文，不能用模型自己的需求复述。"
        ),
    )


def _check_acceptance_contract_sources(
    tool_name: str,
    args: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    contract = args.get("acceptance_contract")
    if contract is None:
        return None
    requirements = contract.get("requirements") if isinstance(contract, dict) else None
    user_text = " ".join(str(
        state.get("user_requirement_text") or state.get("latest_user_message") or ""
    ).split())
    violations: list[str] = []
    if not isinstance(requirements, list) or not requirements:
        violations.append("缺少 requirements")
    else:
        for requirement in requirements:
            if not isinstance(requirement, dict):
                violations.append("requirements 包含无效条目")
                continue
            requirement_id = str(requirement.get("id") or "?")
            try:
                confidence = float(requirement.get("confidence", 1))
            except (TypeError, ValueError):
                confidence = 0
            if requirement.get("confirmed", True) is not True or confidence < 0.75:
                violations.append(f"{requirement_id} 尚未可靠确认")
            source_kind = requirement.get("sourceKind", requirement.get("source_kind", "user"))
            if source_kind == "product_default" and requirement_id != "default-output-format":
                violations.append(f"{requirement_id} 不是允许的产品默认条款")
            if source_kind == "user":
                quote = " ".join(str(requirement.get("sourceQuote", requirement.get("source_quote", ""))).split())
                if not quote or quote not in user_text:
                    violations.append(f"{requirement_id} 的 sourceQuote 不在用户需求原文中")
    if not violations:
        return None
    return _blocked(
        tool_name,
        guard_id="acceptance_contract_sources_must_match_user",
        required_action="clarify_or_trace_requirements",
        violations=violations,
        message="验收契约必须逐条绑定用户原文；低置信度或未确认推断必须先向用户澄清。",
    )


# ── 各条 guard 的判定 ─────────────────────────────────────────────────────────


def _check_read_only_mode(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    return _blocked(
        tool_name,
        required_action="diagnose_only",
        message=(
            "当前为只读诊断模式（自动自愈诊断）：禁止修改流程或触发运行。"
            "流程结构与静态诊断已在状态块里，还需要现场时用 get_run_error / get_run_logs / "
            "get_run_output / inspect_page / inspect_screenshot，"
            "然后用文字给出根因分析和具体修复提案（写明节点 id、字段、建议值），由用户确认后执行。"
        ),
    )


def _check_model_no_vision(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    return _blocked(
        tool_name,
        required_tool="inspect_page",
        message=(
            "当前模型不支持图片输入，inspect_screenshot 无法使用。"
            "请改用 inspect_page 获取结构化 DOM 信息。"
        ),
    )


def _check_static_page_evidence_channel(
    tool_name: str,
    args: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    if state.get("page_evidence_source") != "scrapling_static":
        return None
    raw_nodes = [*(args.get("nodes") or []), *(args.get("add_nodes") or [])]
    raw_nodes.extend(
        item.get("patch")
        for item in args.get("update_nodes") or []
        if isinstance(item, dict) and isinstance(item.get("patch"), dict)
    )
    nodes = [item for item in raw_nodes if isinstance(item, dict)]
    node_types = {str(item.get("type") or "") for item in nodes}
    unsupported = sorted(
        node_type
        for node_type in node_types
        if node_type.startswith(("browser.", "ui.")) and node_type != "browser.fetch"
    )
    if unsupported:
        return _blocked(
            tool_name,
            guard_id="static_page_evidence_requires_fetch_flow",
            required_action="build_static_fetch_flow",
            unsupported_node_types=unsupported,
            message=(
                "当前页面证据来自 Scrapling 静态 HTML，只能证明 browser.fetch 可用，不能证明浏览器交互可用。"
                "请创建包含 browser.fetch 的静态抓取流程，并移除 browser.open/click/extract 或 ui.* 节点。"
            ),
        )
    # create_flow 必须自带 browser.fetch 主链路；update_flow 只改非浏览器节点时，
    # 主链路已在库里且本次不引入未经验证的交互，拦下来只会挡住正常修复。
    if tool_name == "create_flow" and "browser.fetch" not in node_types:
        return _blocked(
            tool_name,
            guard_id="static_page_evidence_requires_fetch_flow",
            required_action="build_static_fetch_flow",
            unsupported_node_types=[],
            message=(
                "当前页面证据来自 Scrapling 静态 HTML，抓取主链路必须是 browser.fetch 节点。"
            ),
        )
    # patch 可以只带 fetcher 不带 type，这种改写同样会把执行通道切回 Playwright。
    for node in nodes:
        node_type = str(node.get("type") or "")
        if node_type and node_type != "browser.fetch":
            continue
        if "fetcher" not in node:
            continue
        fetcher = str(node.get("fetcher") or "static").strip()
        if fetcher != "static":
            # 与上面同一条 guard_id：判据是同一句「证据通道必须等于执行通道」，
            # 只是躲开的方式不同。拆成两个 id 会多出一个不在 GUARDS 里的条目——
            # 它既没有 contract 进不了 system prompt，也躲过「每条 guard 都要有用例」的检查。
            return _blocked(
                tool_name,
                guard_id="static_page_evidence_requires_fetch_flow",
                required_action="set_fetcher_to_static",
                found_fetcher=fetcher,
                message=(
                    f"当前页面证据来自静态 HTTP 抓取，browser.fetch 必须使用 fetcher='static'，"
                    f"但发现 fetcher='{fetcher}'。dynamic/stealthy 会重新走刚失败的 Playwright 通道。"
                ),
            )
    return None


def _check_challenge_page_lock(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    locked = state["challenge_page_lock"]
    locked_url = str(locked.get("url") or "")
    # 探测类工具只挡同一个 URL：拦截页是这个站点这一刻的状态，不是模型的能力问题，
    # 换个地址去看仍然是正当动作，一律挡掉等于没收了它唯一还能用的眼睛。
    if tool_name in {"inspect_page", "inspect_screenshot"}:
        if not locked_url or str((args or {}).get("url") or "") != locked_url:
            return None
    return _blocked(
        tool_name,
        required_action="needs_human_verification",
        message=(
            f"{locked.get('label') or '人机验证拦截页'}：{locked_url or '目标站点'} 返回的是验证墙，不是真实页面。"
            "这不是流程或 selector 的缺陷，改流程、换 selector、重试探测都会撞上同一堵墙；"
            "加 control.human_takeover 节点在无头模式下同样过不去，因为那时没有人在场操作。"
            "请如实告诉用户：需要用有头模式或插件执行器打开一次并人工完成验证，"
            "验证 cookie 会留在持久化 profile 里，之后再继续。"
        ),
        challenge_page_lock=locked,
    )


def _check_failure_budget_lock(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    locked = state["failure_budget_lock"]
    return _blocked(
        tool_name,
        required_action="diagnose_before_structural_update",
        message=(
            "failure budget 已触发，说明最近失败高度重复。"
            "已阻止继续普通 update_flow/run_flow，避免模型在未定位根因时批量改流程。"
            "流程结构与静态诊断在状态块里，请再取运行/页面证据："
            "get_run_error / get_run_logs / inspect_page；"
            "若只需修复单个已确认节点，可使用 apply_node_fix。"
        ),
        failure_budget=locked,
    )


# ── 策略表：顺序即优先级 ──────────────────────────────────────────────────────
#
# 这里只剩「这次调用本身不该发生」的判定：凭据外泄、契约被改写、授权边界、能力缺失、
# 证据通道与执行通道不一致、整页人机验证。它们与失败历史无关，所以判据是当次参数。
#
# 「先做 X 再做 Y」和「别再原地打转」不在这张表里——那两件事已经收进
# [[ai_phases]] 的阶段机与收敛判据。原因是它们摊成十条独立闸门时有两个无症状的坑：
# 相对优先级只存在于本表的行号里，重排就失效；七个计数器各管一个维度，
# 模型换个维度就是一份新额度。
#
# 剩下这几条的排序规则：
# 1. 模式级禁令（只读模式）在最前——它是调用方的授权边界，不该被任何业务闸绕过；
# 2. 意图保全与能力缺失次之；
# 3. 运行侧 failure budget 最后：它挡的面最宽，放前面会把更具体的判定盖掉。

GUARDS: tuple[Guard, ...] = (
    Guard(
        id="credential_values_must_stay_out_of_ai_tools",
        summary="账号、密码和 Token 等秘密值不得进入 AI 工具参数",
        scope=ToolScope(include=frozenset({"create_flow"})),
        check=_check_credential_values_in_flow,
        contract="凭据变量只能声明名称和敏感属性，value 必须为空；秘密值由用户在输入变量面板配置。",
    ),
    Guard(
        id="acceptance_contract_sources_must_match_user",
        summary="验收契约的用户需求条款必须绑定真实用户原文",
        scope=ToolScope(include=frozenset({"create_flow", "set_acceptance_contract"})),
        check=_check_acceptance_contract_sources,
        contract="验收条款必须引用用户原文；未经确认或低置信度推断不得写入契约。",
    ),
    Guard(
        id="acceptance_contract_change_requires_user_quote",
        summary="修改验收契约必须引用用户本轮明确变更需求的原话",
        scope=ToolScope(include=frozenset({"set_acceptance_contract"})),
        check=_check_acceptance_contract_change,
        contract="验收契约只能因用户明确改变需求而修改，普通修复不得放宽交付条件。",
    ),
    Guard(
        id="read_only_mode",
        summary="只读诊断模式下禁止一切写入与运行",
        scope=ToolScope(include=WRITE_TOOLS),
        requires_state=("read_only_tools",),
        check=_check_read_only_mode,
        # 触发条件是调用方传 read_only=True（无人值守自愈），不是用户说了「审查」，
        # 所以没有 contract：写进提示词会让模型以为审查请求下工具会被拦，从而不敢动手。
    ),
    Guard(
        id="model_no_vision",
        summary="模型不支持图片输入时禁用 inspect_screenshot",
        scope=ToolScope(include=frozenset({"inspect_screenshot"})),
        requires_state=("model_no_vision",),
        check=_check_model_no_vision,
    ),
    Guard(
        id="static_page_evidence_requires_fetch_flow",
        summary="静态页面证据只能用于创建 browser.fetch 流程",
        scope=ToolScope(include=frozenset({"create_flow", "update_flow"})),
        requires_state=("page_evidence_source",),
        check=_check_static_page_evidence_channel,
        contract=(
            "inspect_page 若返回 inspection_source=scrapling_static，"
            "只能创建 browser.fetch + fetcher='static' 的静态抓取流程："
            "不得生成未经验证的浏览器/UI 交互，也不得把 fetcher 改成 dynamic/stealthy"
            "（那两个走的是刚刚失败的 Playwright 通道）。"
        ),
    ),
    Guard(
        id="challenge_page_lock",
        summary="探测到人机验证拦截页后，禁止改流程或重跑，只能转为向用户说明",
        scope=ToolScope(include=FLOW_WRITE_TOOLS | {"run_flow", "inspect_page", "inspect_screenshot"}),
        requires_state=("challenge_page_lock",),
        check=_check_challenge_page_lock,
        contract=(
            "探测到 Cloudflare / DataDome 这类整页人机验证时，改流程和重试都不会有任何效果，"
            "唯一出路是让用户用有头模式或插件执行器人工过一次验证。"
        ),
    ),
    Guard(
        id="failure_budget_lock",
        # 这道闸只该挡「未定位根因就批量改流程」。纯读工具挡掉等于没收了诊断手段，
        # 模型只能在剩下几个工具间空转（list_node_types 被挡就是这么来的）。
        summary="运行侧 failure budget 触发后，只放行诊断类工具与单节点修复",
        scope=ToolScope(
            exclude=PARALLEL_SAFE_TOOLS | {"get_run_error", "inspect_page", "inspect_screenshot", "apply_node_fix"},
        ),
        requires_state=("failure_budget_lock",),
        check=_check_failure_budget_lock,
    ),
)


# ── 入口 ──────────────────────────────────────────────────────────────────────


def apply_pre_tool_guards(
    tool_name: str,
    args: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """按策略表顺序求值；第一条命中的 guard 即拦截结果，返回 None 表示放行。"""
    for guard in GUARDS:
        if not guard.applies(tool_name, state):
            continue
        blocked = guard.check(tool_name, args, state)
        if blocked is not None:
            blocked.setdefault("guard_id", guard.id)
            return blocked
    return None


def guard_contract_lines() -> list[str]:
    """写进 system prompt 的护栏契约。

    只收录带 `contract` 的条目：模型需要**预先知道**才能改变行为的那些。
    纯计数类熔断不进——提前告诉它"三次会被锁"，等于把上限当额度用。
    """
    return [f"- {guard.contract}" for guard in GUARDS if guard.contract]


def describe_guards() -> list[dict[str, Any]]:
    """给文档/调试面板用的护栏清单。"""
    return [
        {
            "id": guard.id,
            "precedence": index,
            "summary": guard.summary,
            "tools": guard.scope.describe(),
            "requires_state": list(guard.requires_state),
            "in_prompt": guard.contract is not None,
        }
        for index, guard in enumerate(GUARDS)
    ]
