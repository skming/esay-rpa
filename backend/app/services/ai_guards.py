"""编排层硬护栏的策略表。

护栏挡的是「模型明知规则仍会犯」的那类错——所以它不能写在 prompt 里靠自觉，
必须在工具真正执行之前拦下来。这里把每一条拦截做成一个 `Guard` 条目而不是
一串 if：护栏之间存在优先级依赖（粗粒度的熔断必须排在细粒度的预算之前，
否则模型每轮换个节点改就一条都不触发），顺序即优先级这件事必须写成数据、
能被读出来，而不是藏在函数的行号里。

副产物有三个，都是「能枚举」直接换来的：
- `guard_contract_lines()` 把契约摘要注入 system prompt，prompt 不再手抄一遍规则；
- 每条 guard 有稳定 `id`，测试按 id 逐条断言，`tests/test_ai_guards.py` 还能反过来
  检查有没有新增了却没人测的 guard；
- 拦截结果里带 `guard_id`，前端和日志能区分是哪道闸。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.services.ai_tools.variables import (
    _SCRIPT_CHANNEL_NODE_TYPES,
    _find_script_http_fetch_marker,
)

# ── 阈值 ──────────────────────────────────────────────────────────────────────

MAX_CONSECUTIVE_INSPECT_PAGE = 3  # 连续调用超过此数视为卡死，guard 强制换策略
NODE_SELECTOR_FIX_BUDGET = 2  # 同一节点 selector 反复改仍失败超过此数，判定为方向性错误而非手误
NAV_FAILURE_BUDGET = 2  # 同一节点导航连续失败超过此数才升级为阻断，允许偶发网络抖动重试

# 「改流程 → 跑 → 又失败」的总次数上限。其余护栏都按节点/按问题类型计数，
# 模型每轮换个节点改就一条都不触发，能一路空转到 MAX_TOOL_ROUNDS；
# 这条不关心改的是哪里，只认「又跑了一次、又没成」。
MAX_REPAIR_CYCLES = 3

# ── 工具分组 ──────────────────────────────────────────────────────────────────

# 自愈诊断等只读场景禁用的写入类工具
WRITE_TOOLS = frozenset({
    "create_flow", "update_flow", "apply_node_fix", "set_acceptance_contract", "run_flow", "publish_flow",
    "stop_run", "create_schedule", "toggle_schedule",
})

FLOW_WRITE_TOOLS = frozenset({"create_flow", "update_flow", "apply_node_fix", "set_acceptance_contract"})

# 可并发起跑的工具：纯读、无副作用、不参与 guard 计数。
# 不含 inspect_page / inspect_screenshot / get_run_error——它们会改熔断计数与
# fresh_page_evidence，并发会让「连续 inspect 3 次」这类按顺序计数的护栏失效。
PARALLEL_SAFE_TOOLS = frozenset({
    "get_flow",
    "get_run_logs",
    "get_run_output",
    "get_run_status",
    "lint_flow",
    "list_flows",
    "list_node_types",
    "list_schedules",
    "validate_flow",
})

# 这些字段代表"用哪套方案抓"，改回旧值意味着在两个方案之间打转而不是在收敛
OSCILLATION_TRACKED_FIELDS = ("selector", "extractMode")

_DIAGNOSTIC_TOOLS = frozenset({
    "get_run_error", "get_run_logs", "get_flow", "lint_flow",
    "validate_flow", "inspect_page", "inspect_screenshot", "get_run_output",
})

_CREDENTIAL_NAME_TOKENS = (
    "password", "passwd", "pwd", "token", "secret", "api_key", "apikey", "credential",
)


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


def selector_change_node_ids(tool_name: str, args: dict[str, Any]) -> list[str]:
    """本次 update_flow/apply_node_fix 调用会修改 selector 的节点 id 列表。"""
    if not isinstance(args, dict):
        return []
    if tool_name == "apply_node_fix":
        patch = args.get("config_patch")
        if isinstance(patch, dict) and "selector" in patch:
            node_id = str(args.get("node_id") or "")
            return [node_id] if node_id else []
        return []
    if tool_name == "update_flow":
        node_ids: list[str] = []
        for item in args.get("update_nodes") or []:
            if not isinstance(item, dict):
                continue
            patch = item.get("patch")
            if isinstance(patch, dict) and "selector" in patch and item.get("id"):
                node_ids.append(str(item["id"]))
        return node_ids
    return []


def node_field_changes(tool_name: str, args: dict[str, Any]) -> list[tuple[str, str, str]]:
    """本次调用写入的 (节点 id, 字段名, 新值)，只覆盖 OSCILLATION_TRACKED_FIELDS。"""
    if not isinstance(args, dict):
        return []
    if tool_name == "apply_node_fix":
        patches = [(str(args.get("node_id") or ""), args.get("config_patch"))]
    elif tool_name == "update_flow":
        patches = [
            (str(item.get("id") or ""), item.get("patch"))
            for item in args.get("update_nodes") or []
            if isinstance(item, dict)
        ]
    else:
        return []
    changes: list[tuple[str, str, str]] = []
    for node_id, patch in patches:
        if not node_id or not isinstance(patch, dict):
            continue
        for field_name in OSCILLATION_TRACKED_FIELDS:
            if field_name in patch:
                changes.append((node_id, field_name, str(patch[field_name])))
    return changes


def _blocked(tool_name: str, **payload: Any) -> dict[str, Any]:
    return {"status": "blocked_by_orchestrator_guard", "blocked_tool": tool_name, **payload}


def _check_credential_values_in_flow(
    tool_name: str,
    args: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    del state
    exposed: list[str] = []
    for item in args.get("input_variables") or []:
        if not isinstance(item, dict) or not str(item.get("value") or "").strip():
            continue
        name = str(item.get("name") or "")
        lowered = name.lower()
        if (
            item.get("category") == "credential"
            or item.get("sensitive") is True
            or any(token in lowered for token in _CREDENTIAL_NAME_TOKENS)
        ):
            exposed.append(name or "<unnamed>")
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
            "请只使用诊断类工具（get_run_error / get_run_logs / get_flow / lint_flow / "
            "validate_flow / inspect_page / inspect_screenshot / get_run_output），"
            "然后用文字给出根因分析和具体修复提案（写明节点 id、字段、建议值），由用户确认后执行。"
        ),
    )


def _check_execution_channel(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    if state.get("repair_intent") != "preserve_execution_channel":
        return None

    # 只保护本轮开始时确实属于浏览器主链路的节点；删改无关辅助/控制节点属正常编辑
    browser_chain_node_ids: set[str] = state.get("browser_chain_node_ids") or set()

    violations: list[dict[str, Any]] = []
    if tool_name == "update_flow":
        remove_node_ids = [str(nid) for nid in (args.get("remove_node_ids") or [])]
        removed_chain_ids = [nid for nid in remove_node_ids if nid in browser_chain_node_ids]
        if removed_chain_ids:
            violations.append({
                "issue": "repair_removed_existing_nodes",
                "message": (
                    "用户报告的是原流程上的局部问题，不能删除已有的浏览器主链路节点。"
                    "请保留原网页打开/等待/提取主链路，只针对性追加或调整节点。"
                ),
                "remove_node_ids": removed_chain_ids,
            })

        for item in args.get("update_nodes") or []:
            if not isinstance(item, dict):
                continue
            patch = item.get("patch") if isinstance(item.get("patch"), dict) else {}
            item_id = str(item.get("id")) if item.get("id") is not None else None
            if patch.get("type") in _SCRIPT_CHANNEL_NODE_TYPES and item_id in browser_chain_node_ids:
                violations.append({
                    "issue": "repair_replaced_node_with_script",
                    "message": (
                        f"用户要求修复原流程问题，但补丁试图把已有的浏览器主链路节点改成 {patch.get('type')}。"
                        "这属于执行通道切换，必须先获得用户明确确认。"
                    ),
                    "node_id": item.get("id"),
                })
            marker = _find_script_http_fetch_marker(str(patch.get("code") or ""))
            if marker is not None and item_id in browser_chain_node_ids:
                violations.append({
                    "issue": "repair_uses_script_http_fetch",
                    "message": (
                        f"增量修复不能用 `{marker}` 这类脚本 HTTP 请求替代浏览器采集链路。"
                        "请在原 browser.* 流程上追加节点解决用户反馈的问题。"
                    ),
                    "node_id": item.get("id"),
                    "marker": marker,
                })

        for node in args.get("add_nodes") or []:
            if not isinstance(node, dict):
                continue
            marker = _find_script_http_fetch_marker(str(node.get("code") or ""))
            if node.get("type") in _SCRIPT_CHANNEL_NODE_TYPES and marker is not None:
                violations.append({
                    "issue": "repair_uses_script_http_fetch",
                    "message": (
                        f"增量修复不能新增使用 `{marker}` 抓网页的脚本节点来替代浏览器流程。"
                        "需要新增 browser.open/browser.click/control.foreach 等节点。"
                    ),
                    "node_id": node.get("id"),
                    "marker": marker,
                })

        # 改边绕过：受保护节点未出现在 remove_node_ids 中，但其全部连线被
        # remove_edge_ids 切断且无新连线接回——节点存活但功能上已被移除
        browser_chain_edges_by_id: dict[str, tuple[str, str]] = state.get("browser_chain_edges_by_id") or {}
        if browser_chain_edges_by_id:
            remove_edge_ids = {str(eid) for eid in (args.get("remove_edge_ids") or [])}
            added_pairs: set[tuple[str, str]] = set()
            for edge in args.get("add_edges") or []:
                if isinstance(edge, dict) and "source" in edge and "target" in edge:
                    added_pairs.add((str(edge["source"]), str(edge["target"])))

            orphaned_ids: list[str] = []
            for node_id in browser_chain_node_ids:
                if node_id in removed_chain_ids:
                    continue  # 已作为直接删除上报
                touching = {
                    eid: pair for eid, pair in browser_chain_edges_by_id.items()
                    if node_id in pair
                }
                if not touching:
                    continue  # 本轮开始时该节点本就无连线，不在此检查范围
                surviving = {eid: pair for eid, pair in touching.items() if eid not in remove_edge_ids}
                if surviving:
                    continue  # 仍有原连线未被动过
                reattached = any(node_id in pair for pair in added_pairs)
                if not reattached:
                    orphaned_ids.append(node_id)

            if orphaned_ids:
                violations.append({
                    "issue": "repair_orphaned_browser_chain_node_via_edges",
                    "message": (
                        "补丁没有删除浏览器主链路节点本身，但通过 remove_edge_ids 切断了它与流程的"
                        "全部连线，且没有新增连线接回——这等同于把该节点从执行路径中移除，"
                        "只是没有直接删除节点。请保留原有连线，或新增连线让该节点仍在执行路径上。"
                    ),
                    "node_ids": orphaned_ids,
                })

    if tool_name == "apply_node_fix":
        patch = args.get("config_patch") if isinstance(args.get("config_patch"), dict) else {}
        fix_node_id = str(args.get("node_id")) if args.get("node_id") is not None else None
        if patch.get("type") in _SCRIPT_CHANNEL_NODE_TYPES and fix_node_id in browser_chain_node_ids:
            violations.append({
                "issue": "repair_replaced_node_with_script",
                "message": (
                    f"用户要求修复原流程问题，但补丁试图把浏览器主链路节点改成 {patch.get('type')}。"
                    "这会改变原流程方案，必须先获得用户明确确认。"
                ),
                "node_id": args.get("node_id"),
            })
        marker = _find_script_http_fetch_marker(str(patch.get("code") or ""))
        if marker is not None and fix_node_id in browser_chain_node_ids:
            violations.append({
                "issue": "repair_uses_script_http_fetch",
                "message": (
                    f"增量修复不能用 `{marker}` 这类脚本 HTTP 请求替代原浏览器采集。"
                    "请追加节点解决问题，而不是重写成脚本抓取。"
                ),
                "node_id": args.get("node_id"),
                "marker": marker,
            })

    if not violations:
        return None

    return _blocked(
        tool_name,
        required_action="preserve_execution_channel",
        issue="user_intent_drift",
        message=(
            "用户是在原流程基础上补充约束或报告局部问题，含义是增量修复原流程。"
            "当前补丁会删除或替换原流程主链路，属于未经确认的方案切换。"
        ),
        violations=violations,
        allowed_changes=[
            "保留已有 browser.open/browser.wait/browser.extract 节点",
            "新增针对性节点解决用户反馈的具体问题",
            "新增 control.foreach/control.condition/control.retry 等循环或分支节点",
            "新增用于验证修复效果的证据变量",
            "必要时微调原提取 selector，但不能切换执行通道",
        ],
        needs_user_confirmation_for="切换到 Python/Scrapling/HTTP/API 抓取方案",
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


def _check_pre_create_inspect_gate(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    gate = state["pre_create_inspect_gate"]
    if gate.get("inspect_done"):
        return None
    suggested_url = gate.get("suggested_url", "")
    return _blocked(
        tool_name,
        required_tool="inspect_page",
        message=(
            "创建流程前必须先调用 inspect_page 检查目标页面 DOM，"
            "否则 selector 只能靠猜测，会导致大量运行失败。"
            + (f" 建议先检查：{suggested_url}" if suggested_url else "")
        ),
        required_action="call_inspect_page_first",
        suggested_args={
            "url": suggested_url,
            "wait_selector": "input[type='password'], input[type='text'], form, table, nav, main",
        },
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


def _check_consecutive_inspect(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    inspect_count = int(state.get("consecutive_inspect_page_count") or 0)
    if inspect_count < MAX_CONSECUTIVE_INSPECT_PAGE:
        return None
    return _blocked(
        tool_name,
        required_action="stop_repeating_inspect_page",
        message=(
            f"已连续调用 inspect_page {inspect_count} 次。继续探测页面不会推进任务，"
            "请基于已有 DOM 结果转入创建/修复流程，或调用 get_flow/lint_flow/get_run_error "
            "做拓扑诊断。若确实需要重新探测，请先完成一次 create_flow/update_flow/apply_node_fix。"
        ),
        allowed_next_tools=[
            "create_flow",
            "update_flow",
            "apply_node_fix",
            "get_flow",
            "lint_flow",
            "get_run_error",
            "get_run_logs",
        ],
    )


def _check_repair_cycle_lock(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    locked = state["repair_cycle_lock"]
    return _blocked(
        tool_name,
        required_action="report_to_user_and_stop",
        message=(
            f"本轮已经「修改流程 → 运行 → 仍失败」{locked.get('cycles')} 次，达到修复次数上限。"
            "继续改下去大概率还是同样的结果——问题多半不在流程定义里，"
            "而在页面状态、登录态、网络或需求本身的歧义。"
            "请立即停止修改与运行，改为用文字向用户说明：已经试过哪些方向、"
            "各自失败在哪一步、你判断的根因是什么、需要用户提供什么信息才能继续。"
        ),
        user_message=(
            f"我连续修了 {locked.get('cycles')} 次仍然没跑通，先停下来避免空转。"
            "下面是我已经试过的方向和判断，需要你确认或补充信息后再继续。"
        ),
        last_error=locked.get("last_error"),
        allowed_next_tools=[
            "get_run_error", "get_run_logs", "get_flow", "lint_flow",
            "inspect_page", "inspect_screenshot", "get_run_output",
        ],
    )


def _check_quality_budget_lock(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    locked = state["quality_budget_lock"]
    return _blocked(
        tool_name,
        required_action="fix_root_cause_before_retry",
        message=(
            f"质量 failure budget 已触发：同一问题 {locked.get('issue')} 已连续失败 {locked.get('count')} 次。"
            "说明当前修复方向未能解决根因，继续 update_flow/run_flow 只会循环。"
            "请先用 get_run_output 对比修复前后输出差异，再用 inspect_page 确认筛选控件实际触发了查询，"
            "或用 apply_node_fix 精准修复已确认的单个问题节点。"
        ),
        quality_budget_lock=locked,
    )


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


def _check_navigation_budget_lock(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    locked = state["navigation_budget_lock"]
    return _blocked(
        tool_name,
        required_action="needs_user_navigation_target",
        message=(
            f"导航 failure budget 已触发：节点 `{locked.get('node_id')}` 已连续导航失败 {locked.get('count')} 次。"
            "系统已停止继续猜测菜单 selector，避免反复无效运行。"
            "需要用户提供目标页面导航信息后再继续修复。"
        ),
        user_message=(
            "我已经连续无法稳定进入目标数据页，继续猜菜单选择器会浪费运行次数。"
            "请提供以下任意一种信息：1）手动打开目标列表页后的完整浏览器 URL；"
            "2）从首页到目标页的完整菜单路径（每一级菜单名称）；"
            "3）允许你先在浏览器里手动进入目标页，然后让我读取当前页面 URL/DOM 继续修复。"
        ),
        needed_from_user=[
            "目标页面完整 URL（path/query/hash/完整 URL 均可）",
            "或完整菜单路径（从首页开始，每一级菜单名称）",
            "或允许用户手动打开目标页后，由助手读取当前 URL 和 DOM",
        ],
        allowed_next_tools=["get_flow", "get_run_error", "get_run_logs", "inspect_page", "inspect_screenshot", "apply_node_fix"],
        navigation_budget_lock=locked,
    )


def _check_failure_budget_lock(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    locked = state["failure_budget_lock"]
    return _blocked(
        tool_name,
        required_action="diagnose_before_structural_update",
        message=(
            "failure budget 已触发，说明最近失败高度重复。"
            "已阻止继续普通 update_flow/run_flow，避免模型在未定位根因时批量改流程。"
            "请先调用 get_run_error/get_run_logs/get_flow/lint_flow/inspect_page 完成诊断；"
            "若只需修复单个已确认节点，可使用 apply_node_fix。"
        ),
        failure_budget=locked,
    )


def _check_requires_inspect_page(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    return _blocked(
        tool_name,
        required_tool="inspect_page",
        message=(
            "上一次运行错误包含 inspect_hint，说明 selector/页面状态必须先用真实 DOM 诊断。"
            "已阻止继续修节点或 run_flow。请先调用 inspect_page。"
        ),
        suggested_args=state["requires_inspect_page"],
    )


def _check_requires_quality_fix(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    required = state["requires_quality_fix"]
    return _blocked(
        tool_name,
        required_action="repair_quality_issues",
        message=(
            "上一次 assert_run_output 未通过。禁止在未修复 repair_plan 前继续 run_flow，"
            "否则只会重复得到技术成功但业务不可信的结果。"
        ),
        repair_plan=required.get("repair_plan", []),
        issues=required.get("issues", []),
    )


def _check_requires_lint_fix(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    return _blocked(
        tool_name,
        required_action="repair_lint_findings",
        message="静态检查仍存在会导致不可信运行的阻断级 warning/error，已阻止 run_flow。",
        lint_findings=state["requires_lint_fix"],
    )


def _check_field_oscillation(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    """拦截"把字段改回本会话用过的旧值"。

    history[-1] 是当前值，重复写入属幂等；命中更早的值才说明方案在来回翻，
    两个方案都失败过却没有新证据，再翻一次同样不会成功。
    """
    history: dict[str, list[str]] = state.get("node_field_history") or {}
    warned: set[str] = state.setdefault("oscillation_warned", set())
    for node_id, field_name, value in node_field_changes(tool_name, args):
        key = f"{node_id}.{field_name}"
        past = history.get(key) or []
        if value not in past[:-1] or key in warned:
            continue
        warned.add(key)
        return _blocked(
            tool_name,
            required_action="stop_oscillating_between_known_failed_options",
            message=(
                f"节点 {node_id} 的 {field_name} 正被改回以前用过的旧值 {value!r}"
                f"（历史取值：{past}，跨会话累计）。这两个方案都已试过并未解决问题，再翻一次同样不会。\n"
                "先说明哪一个是对的、依据是什么；若无法判断，"
                "改用 inspect_screenshot 看页面实际渲染，或 run_flow 后用 assert_run_output 比对两者的真实输出，"
                "不要凭推测继续切换。"
            ),
            field_history={key: past},
        )
    return None


def _check_node_selector_fix_budget(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    """同一节点的 selector 已盲改 2 次仍失败时，第 3 次修改必须先拿到新的页面证据。

    防止「换一种 selector 写法再试」绕过字段回摆熔断的死循环。
    """
    if state.get("fresh_page_evidence"):
        return None
    fix_counts: dict[str, int] = state.get("node_selector_fix_counts") or {}
    exhausted = [
        nid for nid in selector_change_node_ids(tool_name, args)
        if fix_counts.get(nid, 0) >= NODE_SELECTOR_FIX_BUDGET
    ]
    if not exhausted:
        return None
    return _blocked(
        tool_name,
        required_action="gather_page_evidence_before_selector_fix",
        message=(
            f"节点 {exhausted} 的 selector 已累计修改 {NODE_SELECTOR_FIX_BUDGET} 次仍未解决（含之前会话）——"
            "继续盲改写法只会浪费运行次数。历史事故表明这类循环的根因往往不是 selector 写错，"
            "而是页面出现了 DOM 看不见的状态（滑块验证/弹窗遮挡/页面未跳转）。"
            "请先调用 inspect_screenshot 查看页面实际状态（或 inspect_page 复核 DOM、"
            "get_run_error 获取失败现场截图），确认真实原因后再修改；"
            "若确认是验证码/滑块，改为插入 control.human_takeover 节点而不是修 selector。"
        ),
        blocked_node_ids=exhausted,
    )


def _check_repair_autorun_lock(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    # 同一条规则提示词里也写着，但模型该跑还是跑——这里不是重复保险，是唯一拦得住的那道。
    # 两边判错的代价差得很远：拦错了，用户补一句「跑一下」；放行错了，就是在用户没点运行的
    # 情况下拉起浏览器去操作真实站点。所以宁可偏向拦。
    return _blocked(
        tool_name,
        required_action="ask_user",
        message=(
            "本轮用户只要求修复，没有要求运行。改完请说明改了什么、为什么，"
            "然后问用户要不要重新运行——运行会真的打开浏览器操作目标站点，这个决定归用户。"
            "用户下一句表示要跑时，本限制自动解除。"
        ),
    )


def _check_pending_repair_gate(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    gate = state["pending_repair_gate"]
    missing = []
    if not gate.get("lint_done"):
        missing.append("lint_flow")
    if not gate.get("inspect_done"):
        missing.append("inspect_page")
    if not missing:
        return None
    return _blocked(
        tool_name,
        required_tools=missing,
        message=(
            f"修复节点前必须先完成诊断。缺少：{' → '.join(missing)}。"
            "调用后即可解锁 apply_node_fix / update_flow。"
        ),
    )


# ── 策略表：顺序即优先级 ──────────────────────────────────────────────────────
#
# 排序规则：
# 1. 模式级禁令（只读模式）在最前——它是调用方的授权边界，不该被任何业务闸绕过；
# 2. 意图保全与能力缺失次之——它们判定的是「这次调用本身不该发生」，与失败历史无关；
# 3. 熔断类按粗到细：repair_cycle 不看改的是哪里，只认「又跑了一次又没成」，
#    排在按节点/按问题计数的预算之前，否则模型每轮换个节点改就一条都触发不了；
# 4. 前置诊断门（requires_*、pending_repair_gate）最后——它们是「先做 X 再做 Y」，
#    在已经熔断的局面下报出来只会把模型引向一个同样被挡住的动作。

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
        id="execution_channel_preservation",
        summary="增量修复不得删除或改写已有浏览器主链路（切换到脚本/HTTP 抓取需用户明确确认）",
        scope=ToolScope(include=frozenset({"update_flow", "apply_node_fix"})),
        check=_check_execution_channel,
        contract=(
            "用户报局部问题时只能在原流程上追加/微调节点；"
            "删除浏览器主链路节点、或改写成 script.*/HTTP 抓取，会被直接阻断。"
        ),
    ),
    Guard(
        id="model_no_vision",
        summary="模型不支持图片输入时禁用 inspect_screenshot",
        scope=ToolScope(include=frozenset({"inspect_screenshot"})),
        requires_state=("model_no_vision",),
        check=_check_model_no_vision,
    ),
    Guard(
        id="pre_create_inspect_gate",
        summary="建流程前必须先 inspect_page，未探测就写入一律阻断",
        scope=ToolScope(include=FLOW_WRITE_TOOLS),
        requires_state=("pre_create_inspect_gate",),
        check=_check_pre_create_inspect_gate,
        contract="用户给了 URL 时，create_flow/update_flow 之前必须先 inspect_page，selector 只能来自检查结果。",
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
        id="consecutive_inspect_limit",
        summary=f"连续 inspect_page/inspect_screenshot 达 {MAX_CONSECUTIVE_INSPECT_PAGE} 次后强制转入构建或诊断",
        scope=ToolScope(include=frozenset({"inspect_page", "inspect_screenshot"})),
        check=_check_consecutive_inspect,
    ),
    Guard(
        id="repair_cycle_lock",
        summary=f"「改流程 → 运行 → 仍失败」累计 {MAX_REPAIR_CYCLES} 次后停止修改与运行，转为向用户说明",
        scope=ToolScope(include=FLOW_WRITE_TOOLS | {"run_flow"}),
        requires_state=("repair_cycle_lock",),
        check=_check_repair_cycle_lock,
        contract=(
            f"同一轮里「改了再跑」失败 {MAX_REPAIR_CYCLES} 次会被锁死，"
            "之后只能用文字汇报已试方向与根因判断——所以不要把运行次数当成试错额度。"
        ),
    ),
    Guard(
        id="quality_budget_lock",
        summary="同一质量问题连续两次审计不过后，禁止继续 update_flow/run_flow",
        scope=ToolScope(include=frozenset({"update_flow", "run_flow"})),
        requires_state=("quality_budget_lock",),
        check=_check_quality_budget_lock,
    ),
    Guard(
        id="navigation_budget_lock",
        summary=f"同一节点导航连续失败 {NAV_FAILURE_BUDGET} 次后停止猜菜单 selector，转为向用户要目标 URL",
        scope=ToolScope(include=frozenset({"update_flow", "run_flow"})),
        requires_state=("navigation_budget_lock",),
        check=_check_navigation_budget_lock,
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
    Guard(
        id="requires_inspect_page",
        summary="运行错误带 inspect_hint 时，必须先 inspect_page 才能继续修节点或运行",
        scope=ToolScope(
            exclude=frozenset({
                "inspect_page", "inspect_screenshot", "get_run_error",
                "get_run_logs", "get_flow", "lint_flow",
            }),
        ),
        requires_state=("requires_inspect_page",),
        check=_check_requires_inspect_page,
        contract="运行失败若带 inspect_hint，必须先 inspect_page 拿真实 DOM，才能再修节点或重跑。",
    ),
    Guard(
        id="requires_quality_fix",
        summary="assert_run_output 未通过时禁止重跑，必须先修 repair_plan",
        scope=ToolScope(include=frozenset({"run_flow"})),
        requires_state=("requires_quality_fix",),
        check=_check_requires_quality_fix,
        contract="assert_run_output 不通过就重跑，只会再得到一次「技术成功但业务不可信」的结果，会被阻断。",
    ),
    Guard(
        id="requires_lint_fix",
        summary="存在阻断级 lint finding 时禁止运行",
        scope=ToolScope(include=frozenset({"run_flow"})),
        requires_state=("requires_lint_fix",),
        check=_check_requires_lint_fix,
        contract="lint_flow 报出的阻断级 finding 未修完之前，run_flow 会被阻断。",
    ),
    Guard(
        id="field_oscillation",
        summary="禁止把 selector/extractMode 改回本流程用过的旧值（跨会话累计）",
        scope=ToolScope(include=frozenset({"update_flow", "apply_node_fix"})),
        check=_check_field_oscillation,
        contract="selector/extractMode 改回以前试过的旧值会被阻断——两个方案都失败过，再翻一次不会有新结果。",
    ),
    Guard(
        id="node_selector_fix_budget",
        summary=f"同一节点 selector 改满 {NODE_SELECTOR_FIX_BUDGET} 次后，须先取得新页面证据才能再改",
        scope=ToolScope(include=frozenset({"update_flow", "apply_node_fix"})),
        check=_check_node_selector_fix_budget,
        contract=(
            f"同一节点的 selector 累计改过 {NODE_SELECTOR_FIX_BUDGET} 次（含历史会话）后，"
            "必须先 inspect_page/inspect_screenshot 取得新证据才能再改。"
        ),
    ),
    Guard(
        id="repair_autorun_lock",
        summary="用户只要求修复时，改完不许顺手 run_flow",
        scope=ToolScope(include=frozenset({"run_flow"})),
        requires_state=("repair_autorun_lock",),
        check=_check_repair_autorun_lock,
        contract="用户只说「修一下 / 报错了」时，改完交回用户，不要顺手 run_flow；要不要重跑由用户决定。",
    ),
    Guard(
        id="pending_repair_gate",
        summary="修复意图下必须先 lint_flow（必要时加 inspect_page）才能改节点",
        scope=ToolScope(include=frozenset({"apply_node_fix", "update_flow"})),
        requires_state=("pending_repair_gate",),
        check=_check_pending_repair_gate,
        contract="用户说「修一下」时，先 lint_flow 定位问题再动手；跳过诊断直接改节点会被阻断。",
    ),
)


# ── 参数改写：不是拦截，但同样必须在工具执行前发生 ────────────────────────────


def _mutate_requirement_provenance(tool_name: str, args: dict[str, Any], state: dict[str, Any]) -> None:
    """assert_run_output 的两个自证入口收归系统。

    requirement_text 和 content_match_confirmed 都由被审计方自己填：模型可以把需求
    复述成本轮的修复任务，再顺手把确认位置 true，对齐检查就永远命中不了真实需求。
    确认位只在工具真报过内容不匹配问题之后才作数（表格与文档两条路径的问题名见
    ai_orchestrator._CONTENT_MISMATCH_ISSUES）。
    """
    session_requirement = str(state.get("user_requirement_text") or "").strip()
    if session_requirement and args.get("requirement_text") != session_requirement:
        args["requirement_text"] = session_requirement
        state["requirement_text_overridden"] = True
    if args.get("content_match_confirmed") and not state.get("content_mismatch_reported"):
        args["content_match_confirmed"] = False
        state["content_match_confirm_stripped"] = True


@dataclass(frozen=True)
class ArgMutator:
    id: str
    summary: str
    scope: ToolScope
    apply: Callable[[str, dict[str, Any], dict[str, Any]], None]


ARG_MUTATORS: tuple[ArgMutator, ...] = (
    ArgMutator(
        id="requirement_provenance",
        summary="assert_run_output 的 requirement_text/content_match_confirmed 由系统改写，不采信模型自填",
        scope=ToolScope(include=frozenset({"assert_run_output"})),
        apply=_mutate_requirement_provenance,
    ),
)


# ── 入口 ──────────────────────────────────────────────────────────────────────


def apply_pre_tool_guards(
    tool_name: str,
    args: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """按策略表顺序求值；第一条命中的 guard 即拦截结果，返回 None 表示放行。"""
    for mutator in ARG_MUTATORS:
        if mutator.scope.matches(tool_name):
            mutator.apply(tool_name, args, state)

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
