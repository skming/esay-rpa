"""每轮重建的权威流程状态。

旧设计在一轮对话开始时把流程定义注入一次，并用 `protect_prefix` 把它保护成最不可
丢弃的内容。模型每写一次流程，这份注入就过期一次，而真正的新事实躺在随时会被压缩
的工具返回里。于是模型只有两条路：翻自己的编辑历史，或者重新调 `get_flow` /
`lint_flow` 确认——后者在实测里占掉了全部工具调用的 18%。

那不是模型不听话，是它拿不到当前状态。用提示词、schema 摘除、护栏去禁止复检，
只是把它的眼睛也一起蒙上。

这里换成：每轮重算一份状态，放在消息尾部替换掉上一轮那份。三个后果——
- `get_flow` / `lint_flow` / `validate_flow` / `get_run_status` 不必再作为工具暴露给
  模型：它们回答的问题在每轮开头就已经答完了；
- 「不要重复检查」这条规则不必存在：没有可调的工具，也没有悬着的问题；
- 状态块位于缓存锚点之后，本来每轮都要重发，放这里不额外增加缓存开销。

能力一件不减：这些读取仍然是 executor 的方法，只是唯一的调用方从模型换成了本模块。
`get_run_error` 是例外，仍然留给模型——状态块给的是运行结论，失败现场（截图、
导航轨迹、失败节点配置）体积大且不是每轮都要看，按需下钻。

运行产物的审计（`audit_run`）走同一条路：它不是模型可以选择做或不做的一步，而是运行
结束后平台自己得出的结论，每轮随状态块刷新。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field as dc_field
from typing import Any

from app.models.schemas import FlowAcceptanceContract
from app.services.acceptance_contract import contract_validation_errors
from app.services.ai_tools.variables import _collect_defined_vars
from app.services.execution_evidence import definition_digest

logger = logging.getLogger(__name__)

# 画布字段，模型改不到也不该看：省掉它们能腾出的 token 全部让给 config
_STRIP_NODE_FIELDS = frozenset({"position", "status", "kind"})

# 判定「当前流程已有浏览器采集主链路」的节点类型
BROWSER_MAIN_CHAIN_TYPES = frozenset({
    "browser.open", "browser.extract", "ui.extract", "browser.fetch",
})

# 画布骨架节点：只有这两种节点的流程等同于空流程
SCAFFOLD_NODE_TYPES = frozenset({"start", "end"})

_STATE_SENTINEL = "<flow-state"


def is_local_draft_flow_id(flow_id: str | None) -> bool:
    """`local-*` 只是前端在首次保存前的草稿标识，后端没有对应记录。"""
    return bool(flow_id and flow_id.startswith("local-"))


@dataclass
class FlowState:
    """当前流程的权威快照：既渲染给模型，也供 guard 判断结构。"""

    flow_id: str | None = None
    name: str | None = None
    revision: int | None = None
    definition_digest: str | None = None

    nodes: list[dict[str, Any]] = dc_field(default_factory=list)
    edges: list[dict[str, Any]] = dc_field(default_factory=list)
    input_variables: list[dict[str, Any]] = dc_field(default_factory=list)
    readiness_note: str | None = None

    # 诊断集：lint 与变量引用校验合流，模型只需要面对一个「当前还剩什么问题」的集合
    findings: list[dict[str, Any]] = dc_field(default_factory=list)

    # 本轮最近一次 run_flow / get_run_status 的结果，由编排层在工具返回后传进来。
    # 放进状态块是为了取消 run_flow 超时后那句「可用 get_run_status 查询」——
    # 状态会自己更新，模型不必再花一轮去问。
    last_run: dict[str, Any] | None = None

    # 已有浏览器主链路 → 写入期差分检查需要保住这些节点（见 ai_tools/lint_diff.py）
    browser_chain_node_ids: set[str] = dc_field(default_factory=set)
    # Studio 里「新建流程」一落地就带 flow_id 存库、画布只有 start→end，需与存量流程区分
    is_blank: bool = True

    # 读取失败时置位：状态块会明说这一轮没拿到流程，避免模型把空状态当成空流程
    load_failed: bool = False

    @property
    def has_browser_chain(self) -> bool:
        return bool(self.browser_chain_node_ids)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.get("severity") == "error")

    @property
    def warn_count(self) -> int:
        return sum(1 for f in self.findings if f.get("severity") != "error")

    def diagnostic_keys(self) -> set[str]:
        """诊断集的可比较形态，用于判断「这一轮有没有把问题变少」。"""
        return {
            f"{f.get('severity')}:{f.get('issue')}:{f.get('node_id') or ''}"
            for f in self.findings
        }


async def build_flow_state(
    executor: Any,
    flow_id: str | None,
    last_run: dict[str, Any] | None = None,
) -> FlowState:
    """读一次流程，跑一遍静态检查，合成本轮状态。

    走 `executor.execute` 而不是 flow_service：这几个读取里已经含了脱敏（凭据值换成
    `has_value`）、体积裁剪（剔除 snapshots）和诊断标注，绕过去等于把它们重写一遍。
    """
    state = FlowState(flow_id=flow_id)
    if not flow_id or is_local_draft_flow_id(flow_id):
        return state

    try:
        flow = await executor.execute("get_flow", {"flow_id": flow_id})
    except Exception:
        # 注入失败不阻断对话，但必须留痕——否则「AI 看不到当前流程」完全无法排查
        logger.warning("流程状态读取失败（flow_id=%s）", flow_id, exc_info=True)
        state.load_failed = True
        return state
    if not isinstance(flow, dict) or flow.get("error"):
        state.load_failed = True
        return state

    state.name = flow.get("name")
    if isinstance(flow.get("revision"), int):
        state.revision = flow["revision"]

    raw_nodes: list[dict[str, Any]] = []
    definition = flow.get("definition")
    if isinstance(definition, dict):
        state.definition_digest = definition_digest(definition)
        raw_nodes = [n for n in definition.get("nodes", []) if isinstance(n, dict)]
        raw_edges = [e for e in definition.get("edges", []) if isinstance(e, dict)]
        state.nodes = [
            {k: v for k, v in n.items() if k not in _STRIP_NODE_FIELDS} for n in raw_nodes
        ]
        state.edges = raw_edges
        state.browser_chain_node_ids = {
            str(n["id"]) for n in raw_nodes
            if n.get("type") in BROWSER_MAIN_CHAIN_TYPES and "id" in n
        }
        state.is_blank = not any(n.get("type") not in SCAFFOLD_NODE_TYPES for n in raw_nodes)

    variables = flow.get("input_variables")
    if isinstance(variables, list):
        state.input_variables = [v for v in variables if isinstance(v, dict)]
    readiness = flow.get("run_readiness")
    if isinstance(readiness, dict) and readiness.get("message"):
        state.readiness_note = str(readiness["message"])

    state.findings = await _collect_findings(executor, flow_id)
    state.findings.extend(_contract_findings(flow, raw_nodes, state))
    state.last_run = await _refresh_run(executor, last_run)
    return state


def _contract_findings(
    flow: dict[str, Any], nodes: list[dict[str, Any]], state: FlowState
) -> list[dict[str, Any]]:
    """验收契约不完整 —— run_flow 会在启动浏览器之前直接拒掉。

    这个拒绝一直都在，但模型只能靠真去跑一次才知道，然后花一轮读懂返回、再花一轮补契约。
    判据用的是 run_flow 调的同一个 `contract_validation_errors`：两处结论不可能分岔，
    状态块说能跑就真能跑。

    空画布不报：契约要引用节点产出的变量，还没有节点的时候它必然不完整，此时报出来只是噪声。
    """
    if state.is_blank:
        return []
    raw = flow.get("acceptance_contract")
    try:
        contract = FlowAcceptanceContract.model_validate(raw or {})
    except Exception:
        logger.warning("验收契约解析失败（flow_id=%s）", flow.get("flow_id"), exc_info=True)
        return []
    defined = set(_collect_defined_vars(
        list(nodes), [str(v.get("name")) for v in state.input_variables if v.get("name")]
    ))
    errors = contract_validation_errors(contract, defined_variables=defined)
    if not errors:
        return []
    return [{
        "severity": "error",
        "issue": "acceptance_contract_incomplete",
        "message": "验收契约不完整，run_flow 会在启动浏览器前被拒：" + "；".join(errors),
        "fix": (
            "用 set_acceptance_contract 补齐：requirements 逐条引用用户原话，"
            "deliverables 至少一条 required=true，且每个交付变量都由某个节点的 "
            "outputVariable 或输入变量产出。"
        ),
    }]


# 这些状态下任务还在往前走，状态块里那份就是过期的
_NON_TERMINAL_RUN_STATUSES = frozenset({"running", "pending", "queued", "timeout", ""})


async def _refresh_run(
    executor: Any, last_run: dict[str, Any] | None
) -> dict[str, Any] | None:
    """任务还没跑完就刷一次状态，跑完了就补上验收结论。

    `run_flow` 轮询到 90s 就返回 `status="timeout"`，旧设计在返回里写「可用
    get_run_status 查询当前状态」——那是让模型花一整轮去问一件平台自己就能答的事。
    这里每轮替它问掉，超时不再是一个需要模型处理的事件。

    审计同理，且更要紧：上一轮返回 timeout 的那次运行，它的 run_flow 返回里不可能带
    审计结论（那时候还没跑完）。模型手上又没有审计工具，这里不补就永远没有人补。
    """
    if not isinstance(last_run, dict):
        return None
    task_id = last_run.get("task_id")
    if not task_id:
        return last_run
    run = last_run
    if str(run.get("status") or "") in _NON_TERMINAL_RUN_STATUSES:
        try:
            fresh = await executor.execute("get_run_status", {"task_id": task_id})
        except Exception:
            logger.warning("运行状态刷新失败（task_id=%s）", task_id, exc_info=True)
            fresh = None
        if isinstance(fresh, dict) and not fresh.get("error"):
            # 合并而不是替换：get_run_status 只回状态与进度，产物与审计只有另外两处有
            run = dict(last_run)
            run.update({k: v for k, v in fresh.items() if v is not None})

    if str(run.get("status") or "") == "success":
        try:
            audit = await executor.execute("audit_run", {"task_id": task_id})
        except Exception:
            logger.warning("运行审计失败（task_id=%s）", task_id, exc_info=True)
            audit = None
        if isinstance(audit, dict) and not audit.get("error"):
            # 每轮重算而不是只算一次：流程一旦被改，同一份产物就变成了过期证据
            # （审计会给出 stale_run_evidence），这个转变必须让模型看见。
            run = {**run, "acceptance_audit": audit}
    return run


async def _collect_findings(executor: Any, flow_id: str) -> list[dict[str, Any]]:
    """把静态检查与变量引用校验并成一个诊断集。

    两者原本是两个工具、两种返回形状，模型得自己记住「查完 lint 还要查 validate」。
    合流之后「当前还剩什么问题」只有一个答案，也就没有漏查一半的可能。
    """
    findings: list[dict[str, Any]] = []
    try:
        lint = await executor.execute("lint_flow", {"flow_id": flow_id})
    except Exception:
        logger.warning("静态检查失败（flow_id=%s）", flow_id, exc_info=True)
        lint = None
    if isinstance(lint, dict):
        for item in lint.get("findings") or []:
            if isinstance(item, dict):
                findings.append(item)

    try:
        validation = await executor.execute("validate_flow", {"flow_id": flow_id})
    except Exception:
        logger.warning("变量引用校验失败（flow_id=%s）", flow_id, exc_info=True)
        validation = None
    if isinstance(validation, dict):
        for issue in validation.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            findings.append({
                "severity": "error",
                "issue": "undefined_variable_reference",
                "node_id": issue.get("node_id"),
                "message": issue.get("message") or json.dumps(issue, ensure_ascii=False),
                "fix": (
                    "在应当产出该变量的上游节点填 outputVariable / variableName，"
                    "或在引用点之前加 variable.set 节点定义它。"
                ),
            })
    return findings


def _render_variables(state: FlowState) -> list[str]:
    if not state.input_variables:
        return []
    parts: list[str] = []
    for var in state.input_variables:
        name = var.get("name")
        if not name:
            continue
        if var.get("category") == "credential" or var.get("sensitive"):
            filled = bool(var.get("has_value"))
        else:
            filled = bool(str(var.get("value") or "").strip())
        parts.append(f"{name}={'已填' if filled else '未填'}")
    if not parts:
        return []
    lines = ["输入变量：" + " ".join(parts)]
    if state.readiness_note:
        lines.append("  " + state.readiness_note)
    return lines


def _render_edges(state: FlowState) -> str:
    if not state.edges:
        return "连线：无"
    parts: list[str] = []
    for edge in state.edges:
        source, target = edge.get("source"), edge.get("target")
        if not source or not target:
            continue
        handle = edge.get("sourceHandle") or edge.get("label")
        # 分支句柄决定了 true/false 走哪条路，漏掉它模型会把分支接反
        parts.append(f"{source}-[{handle}]->{target}" if handle else f"{source}->{target}")
    return "连线：" + " ".join(parts)


def _render_findings(state: FlowState) -> list[str]:
    if not state.findings:
        return ["诊断：静态检查通过，当前没有待修问题。"]
    lines = [f"诊断（{state.error_count} 个阻断 / {state.warn_count} 个提示）："]
    for finding in state.findings:
        tag = "阻断" if finding.get("severity") == "error" else "提示"
        issue = finding.get("issue") or "unknown"
        node = finding.get("node_id")
        where = f" @{node}" if node else ""
        lines.append(f"  [{tag}] {issue}{where} — {finding.get('message') or ''}")
        fix = finding.get("fix") or finding.get("suggestion")
        if fix:
            lines.append(f"         改法：{fix}")
    return lines


def _render_run(state: FlowState) -> list[str]:
    run = state.last_run
    if not isinstance(run, dict):
        return ["最近运行：本轮还没有运行过，尚无执行证据。"]
    task_id = run.get("task_id") or "?"
    status = run.get("status") or "?"
    lines = [f"最近运行：{task_id} status={status}"]
    if run.get("revision") is not None:
        lines[0] += f"（对应 revision {run['revision']}）"
    progress = run.get("progress")
    if isinstance(progress, dict) and status in _NON_TERMINAL_RUN_STATUSES:
        lines.append(f"  进度：{json.dumps(progress, ensure_ascii=False)[:300]}")
    error = run.get("error") or run.get("run_error")
    if error:
        lines.append(f"  失败原因：{error}")
    if run.get("failed_node_id"):
        lines.append(f"  失败节点：{run['failed_node_id']}")
    if error or run.get("failed_node_id"):
        # 状态块只给结论。现场（失败截图、导航轨迹、节点配置）体积大且不是每次都要看，
        # 留给 get_run_error 按需下钻。
        lines.append("  需要失败现场（截图 / 导航轨迹 / 节点配置）时调 get_run_error。")
    audit = run.get("acceptance_audit")
    if isinstance(audit, dict):
        passed = audit.get("passed")
        lines.append(f"  验收：{'通过' if passed else '未通过'}")
        for issue in audit.get("issues") or []:
            if isinstance(issue, dict):
                lines.append(f"    - {issue.get('issue')}：{issue.get('message') or ''}")
            else:
                lines.append(f"    - {issue}")
        for warning in audit.get("warnings") or []:
            if isinstance(warning, dict):
                lines.append(f"    - [提示] {warning.get('issue')}：{warning.get('message') or ''}")
        if not passed:
            # 审计由平台跑，模型无从"再审一次"；它唯一能做的是改流程重跑
            lines.append("  验收未通过不是可以解释掉的事：改流程节点后重跑，不要放宽契约。")
    outputs = run.get("outputs") or run.get("output_summary")
    if outputs:
        lines.append(f"  产物：{json.dumps(outputs, ensure_ascii=False)[:600]}")
    return lines


def render_flow_state(state: FlowState) -> str | None:
    """渲染成注入模型的状态块。返回 None 表示这一轮没有可讲的状态。"""
    if not state.flow_id or is_local_draft_flow_id(state.flow_id):
        return None
    if state.load_failed:
        return (
            f'{_STATE_SENTINEL}>\n读取流程 {state.flow_id} 失败，这一轮拿不到它的当前状态。'
            "不要凭记忆断言流程现在是什么样子，先把这件事告诉用户。\n</flow-state>"
        )

    head = (
        f'{_STATE_SENTINEL} revision="{state.revision}">\n'
        "以下是当前流程的权威状态，每轮开头由平台重新读取后替换。"
        "它比你记忆中的、以及历史工具返回里的任何版本都新——按它判断，不必再去查证。"
    )
    lines = [head, ""]
    label = f"{state.name or '未命名'}（{state.flow_id}）"
    lines.append(f"流程：{label} — {len(state.nodes)} 节点 / {len(state.edges)} 连线")
    if state.is_blank:
        lines.append("画布目前是空的（只有 start/end 骨架），还没有任何业务节点。")
    lines.extend(_render_variables(state))
    if state.nodes:
        lines.append("节点（每行一个，position/status 等画布字段已省略）：")
        lines.extend(
            "  " + json.dumps(node, ensure_ascii=False, separators=(",", ":"))
            for node in state.nodes
        )
    lines.append(_render_edges(state))
    lines.extend(_render_findings(state))
    lines.extend(_render_run(state))
    lines.append("</flow-state>")
    return "\n".join(lines)


def sync_state_message(messages: list[dict[str, Any]], rendered: str | None) -> None:
    """把状态块换成最新一份，位置固定在消息尾部。

    先按哨兵扫掉旧的再追加，而不是记住下标：中途的历史压缩会让任何缓存的下标失效，
    一旦错位就会同时存在两份自相矛盾的状态，比没有状态更糟。
    """
    for i in range(len(messages) - 1, -1, -1):
        content = messages[i].get("content")
        if isinstance(content, str) and content.lstrip().startswith(_STATE_SENTINEL):
            del messages[i]
    if rendered:
        messages.append({"role": "system", "content": rendered})
