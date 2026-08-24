"""写入期差分检查：判据是「这次改动相对上一版做了什么」。

原来有三条 pre-tool 护栏在做这件事——execution_channel_preservation、
field_oscillation、node_selector_fix_budget。它们判的都是变化，手上却只有工具参数，
于是只能靠参数形状去推变化：`update_nodes` 的 patch 里没有 type，「剪断全部入边让
节点静默孤立」得另写一套边模型去还原，同一件事在 update_flow 与 apply_node_fix 两个
分支各写一遍还得保证两边结论一致。参数也不等于事实——记下来的是模型「想改成什么」，
不是最终落盘的那一版。

搬到写入前的那一刻，before/after 两份定义都在手上：一次图比较替掉三段参数解析，
判的是真正会落盘的内容；以后新增改流程的工具或新的改法，都不必再补一遍判定。

这里的每一条命中都拒绝写入（`ChangeReport.rejected`），不存在"提示级"结论：
放行了再提醒，等于让模型先把坏改动写进去，下一轮再去撤。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from typing import Any

from app.services.ai_tools.graph import _unreachable_node_ids
from app.services.ai_tools.variables import (
    _SCRIPT_CHANNEL_NODE_TYPES,
    _find_script_http_fetch_marker,
)

# 这些字段代表「用哪套方案抓」，改回旧值意味着在两个方案之间打转而不是在收敛
OSCILLATION_TRACKED_FIELDS = ("selector", "extractMode")

# 同一节点 selector 反复改仍失败超过此数，判定为方向性错误而非手误
NODE_SELECTOR_FIX_BUDGET = 2

# 画布字段：每次写入都会重排坐标、运行还会回写状态，算进差分会让任何一次
# 空写入都看起来"改了东西"，no_effective_change 就永远不触发
_LAYOUT_ONLY_NODE_FIELDS = frozenset({"position", "status"})


@dataclass(frozen=True)
class ChangeContext:
    """只有调用方（编排层）才知道的两件事，随调用传进来。

    执行器是全进程单例，这些值不能挂在 self 上——两个会话同时写会互相覆盖。

    - protected_node_ids：本轮开始时属于浏览器采集主链路、必须留在执行路径上的节点。
      只在「保留执行通道」意图下非空：删改无关辅助/控制节点属于正常编辑。
    - fresh_page_evidence：本轮是否拿到过新的页面证据（inspect_page / inspect_screenshot /
      失败截图）。它是回摆与 selector 预算这两道闸唯一的解锁条件——凭据是新证据，
      不是模型再坚持一次。
    """

    protected_node_ids: frozenset[str] = frozenset()
    fresh_page_evidence: bool = False


@dataclass(frozen=True)
class ChangeReport:
    """一次写入的差分结论。"""

    findings: tuple[dict[str, Any], ...] = ()
    # 落台账用：(node_id, field, 新值)。取自实际 before/after 而不是调用参数——
    # 台账记的必须是"真的写进去了什么"，否则回摆判定会拿一份从未落盘的历史去比。
    tracked_field_changes: tuple[dict[str, str], ...] = dc_field(default=())

    @property
    def rejected(self) -> bool:
        return bool(self.findings)

    def refusal(self) -> dict[str, Any]:
        """拒绝写入时交回模型的返回。

        status 用 `blocked_` 前缀：前端据此渲染成"已阻断"而不是绿色成功；
        同时带 error，编排层所有「写入成功」分支（清修复门、记台账、作废运行证据）
        都会因此跳过——什么都没落盘，那些记账一条都不该发生。
        """
        return {
            "status": "blocked_by_change_lint",
            "error": "变更未写入：" + " ".join(
                f"[{f.get('issue')}] {f.get('message')}" for f in self.findings
            ),
            "change_findings": [dict(f) for f in self.findings],
        }


def _nodes(definition: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(definition, dict):
        return []
    return [n for n in definition.get("nodes") or [] if isinstance(n, dict)]


def _edges(definition: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(definition, dict):
        return []
    return [e for e in definition.get("edges") or [] if isinstance(e, dict)]


def _by_id(definition: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {str(n["id"]): n for n in _nodes(definition) if n.get("id")}


def _signature(definition: dict[str, Any] | None) -> str:
    """语义指纹：只认会影响执行的内容，画布坐标与运行状态不算。

    sort_keys 让「同样的内容、不同的键顺序」得出同一个指纹；列表顺序保留不排序——
    节点顺序在没有 start 节点时决定入口，排掉会把一次真实的重排看成没改动。
    """
    payload = {
        "nodes": [
            {k: v for k, v in node.items() if k not in _LAYOUT_ONLY_NODE_FIELDS}
            for node in _nodes(definition)
        ],
        "edges": _edges(definition),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _newly_unreachable(before: dict[str, Any] | None, after: dict[str, Any] | None) -> set[str]:
    was = set(_unreachable_node_ids(_nodes(before), _edges(before)))
    now = set(_unreachable_node_ids(_nodes(after), _edges(after)))
    return now - was


def _channel_findings(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    protected_node_ids: frozenset[str],
) -> list[dict[str, Any]]:
    """保留执行通道：受保护的浏览器主链路节点不能被移出执行路径或改写成脚本抓取。

    「删除」与「剪断全部连线」在这里是同一个判定——节点还在但从起点走不到，
    执行时会被整段跳过，与删掉没有区别。原来后者要靠一套边的推演，这里就是可达性比较。
    """
    if not protected_node_ids:
        return []

    after_nodes = _by_id(after)
    orphaned_now = _newly_unreachable(before, after)

    findings: list[dict[str, Any]] = []
    removed = sorted(nid for nid in protected_node_ids if nid not in after_nodes)
    if removed:
        findings.append({
            "issue": "repair_removed_existing_nodes",
            "message": (
                f"用户报告的是原流程上的局部问题，这次改动删掉了浏览器主链路节点 {removed}。"
                "请保留原网页打开/等待/提取主链路，只针对性追加或调整节点；"
                "确实要换成另一套抓取方案，必须先向用户说明并取得确认。"
            ),
            "node_ids": removed,
        })

    orphaned = sorted(nid for nid in protected_node_ids if nid in orphaned_now and nid in after_nodes)
    if orphaned:
        findings.append({
            "issue": "repair_orphaned_browser_chain_node_via_edges",
            "message": (
                f"浏览器主链路节点 {orphaned} 本身没被删除，但改动之后从流程起点已经走不到它——"
                "运行时会被整段跳过，等同于把它从执行路径里移除。"
                "请保留原有连线，或补上新连线让它仍在执行路径上。"
            ),
            "node_ids": orphaned,
        })

    for node_id in sorted(protected_node_ids & set(after_nodes)):
        node_type = str(after_nodes[node_id].get("type") or "")
        if node_type in _SCRIPT_CHANNEL_NODE_TYPES:
            findings.append({
                "issue": "repair_replaced_node_with_script",
                "message": (
                    f"这次改动把浏览器主链路节点 {node_id} 改成了 {node_type}。"
                    "这属于切换执行通道（浏览器采集 → 脚本抓取），必须先获得用户明确确认；"
                    "修局部问题请在原 browser.* 链路上追加或微调节点。"
                ),
                "node_id": node_id,
            })

    # 新出现的脚本 HTTP 抓取：既覆盖"把主链路改写成 requests"，也覆盖"另起一个脚本节点
    # 绕过浏览器抓同一个页面"。判据是 after 有、before 没有，所以早就存在的脚本节点不受影响。
    before_nodes = _by_id(before)
    for node_id, node in sorted(after_nodes.items()):
        marker = _find_script_http_fetch_marker(str(node.get("code") or ""))
        if marker is None:
            continue
        if _find_script_http_fetch_marker(str((before_nodes.get(node_id) or {}).get("code") or "")):
            continue  # 这个节点本来就是这么写的，不是本次改动引入的
        findings.append({
            "issue": "repair_uses_script_http_fetch",
            "message": (
                f"节点 {node_id} 用 `{marker}` 这类脚本 HTTP 请求抓取页面，"
                "不能用它替代已有的浏览器采集链路。请在原 browser.* 流程上追加节点解决用户反馈的问题"
                "（browser.open / browser.click / control.foreach 等），"
                "确实需要换成脚本/API 抓取时先向用户说明并取得确认。"
            ),
            "node_id": node_id,
            "marker": marker,
        })
    return findings


def _tracked_changes(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> list[dict[str, str]]:
    """本次真正改掉的受跟踪字段。

    只看两版都存在的节点：新增节点没有历史，谈不上回摆。
    字段被删掉（patch 里写 null）也不记——那不是"换一个取值再试"，
    没有可回摆的对象。
    """
    before_nodes = _by_id(before)
    changes: list[dict[str, str]] = []
    for node_id, node in _by_id(after).items():
        old_node = before_nodes.get(node_id)
        if old_node is None:
            continue
        for field_name in OSCILLATION_TRACKED_FIELDS:
            if field_name not in node:
                continue
            new_value = str(node.get(field_name))
            if node_id in before_nodes and field_name in old_node and str(old_node.get(field_name)) == new_value:
                continue
            if field_name not in old_node and node.get(field_name) is None:
                continue
            changes.append({"node_id": node_id, "field": field_name, "value": new_value})
    return changes


def _oscillation_findings(
    changes: list[dict[str, str]],
    ledger: dict[str, Any],
    fresh_page_evidence: bool,
) -> list[dict[str, Any]]:
    """拦截「把字段改回这个流程用过的旧值」。

    有了真实的 before 值，判据就是「新值出现在历史里」——不再需要旧实现里
    `history[:-1]` 那个"最后一个是当前值"的位置技巧（它只是因为参数里读不到旧值）。

    解锁条件是新页面证据，与 selector 预算同一个：两个方案都失败过，再翻一次同样
    不会成功；但如果刚看过页面、确实判断出该用哪一个，就该放行。只靠"再提交一次"
    解锁会让这道闸变成一句提醒，而永久拒绝又会在旧值确实正确时把人锁死。
    """
    if fresh_page_evidence:
        return []
    history: dict[str, list[str]] = ledger.get("node_field_history") or {}
    findings: list[dict[str, Any]] = []
    for change in changes:
        key = f"{change['node_id']}.{change['field']}"
        past = [str(v) for v in history.get(key) or []]
        if change["value"] not in past:
            continue
        findings.append({
            "issue": "field_oscillation",
            "message": (
                f"节点 {change['node_id']} 的 {change['field']} 正被改回这个流程用过的旧值 "
                f"{change['value']!r}（历史取值：{past}，跨会话累计）。"
                "两个方案都已经试过并且都没解决问题，再翻回去同样不会。\n"
                "先说明哪一个是对的、依据是什么；判断不了就去拿新证据："
                "inspect_page 复核 DOM、inspect_screenshot 看实际渲染，"
                "或先运行一次用 get_run_output 比对两者的真实产物——拿到新证据后本闸自动放行。"
            ),
            "node_id": change["node_id"],
            "field": change["field"],
            "field_history": {key: past},
        })
    return findings


def _selector_budget_findings(
    changes: list[dict[str, str]],
    ledger: dict[str, Any],
    fresh_page_evidence: bool,
) -> list[dict[str, Any]]:
    """同一节点的 selector 已盲改够次数后，第 N+1 次修改必须先拿到新的页面证据。

    没有这道闸，「换一种 selector 写法再试」就能绕开回摆判定无限循环下去。
    """
    if fresh_page_evidence:
        return []
    counts: dict[str, int] = ledger.get("node_selector_fix_counts") or {}
    exhausted = sorted({
        change["node_id"] for change in changes
        if change["field"] == "selector"
        and int(counts.get(change["node_id"], 0)) >= NODE_SELECTOR_FIX_BUDGET
    })
    if not exhausted:
        return []
    return [{
        "issue": "selector_fix_budget_exhausted",
        "message": (
            f"节点 {exhausted} 的 selector 已累计修改 {NODE_SELECTOR_FIX_BUDGET} 次仍未解决"
            "（含之前会话）——继续盲改写法只会浪费运行次数。历史事故表明这类循环的根因往往"
            "不是 selector 写错，而是页面出现了 DOM 看不见的状态（滑块验证 / 弹窗遮挡 / 页面未跳转）。"
            "请先 inspect_screenshot 查看页面实际状态（或 inspect_page 复核 DOM、"
            "get_run_error 取失败现场截图），确认真实原因后再改；"
            "若确认是验证码/滑块，改为插入 control.human_takeover 节点而不是修 selector。"
        ),
        "node_ids": exhausted,
    }]


_NO_EFFECTIVE_CHANGE_MESSAGE = (
    "这次写入不会改变流程：写入前后的节点与连线完全一致（画布坐标不计）。"
    "重复提交同一份内容说明根因还没找到，再提交一次也不会有任何变化。必须换做法：\n"
    "1) 调用 inspect_page 确认浏览器当前真实页面与 URL；\n"
    "2) 检查流程里是否缺少前置导航节点（browser.open 到目标页面）；\n"
    "3) 若页面 spa_loading=true 或 page_layout 为空，先修前置的导航/等待节点，"
    "而不是当前节点的 selector。"
)


def inspect_change(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    context: ChangeContext | None = None,
    ledger: dict[str, Any] | None = None,
    allow_no_effective_change: bool = False,
) -> ChangeReport:
    """写入落盘前的最后一道判定。

    `allow_no_effective_change` 给「这次调用还改了定义之外的东西」的场合用
    （update_flow 同时改流程名）：那种调用不是空转。
    """
    context = context or ChangeContext()
    ledger = ledger or {}

    if _signature(before) == _signature(after):
        if allow_no_effective_change:
            return ChangeReport()
        return ChangeReport(findings=({
            "issue": "no_effective_change",
            "message": _NO_EFFECTIVE_CHANGE_MESSAGE,
        },))

    changes = _tracked_changes(before, after)
    findings = [
        *_channel_findings(before, after, context.protected_node_ids),
        *_oscillation_findings(changes, ledger, context.fresh_page_evidence),
        *_selector_budget_findings(changes, ledger, context.fresh_page_evidence),
    ]
    return ChangeReport(
        findings=tuple(findings),
        tracked_field_changes=tuple(changes),
    )


def change_lint_contract_lines() -> list[str]:
    """写进 system prompt 的约束。

    与 `ai_guards.guard_contract_lines()` 同一个用途：模型必须**预先知道**才能改变
    行为的那几条，写在这里而不是提示词正文里，改了判定不会漏改提示词。
    纯计数类的上限（selector 预算）同样收录——它带着明确的解锁条件（拿新证据），
    不是"三次额度"。
    """
    return [
        "- 用户报局部问题时只能在原流程上追加/微调节点；删除浏览器主链路节点、剪断它的全部连线、"
        "或把它改写成 script.*/HTTP 抓取，写入会被直接拒绝。",
        "- 提交一份与当前流程完全相同的改动（改完等于没改）会被拒绝：重复同一个修复说明根因没找到。",
        f"- selector/extractMode 改回这个流程以前试过的旧值、或同一节点的 selector 累计改过 "
        f"{NODE_SELECTOR_FIX_BUDGET} 次（含历史会话）后再改，都需要先有新的页面证据"
        "（inspect_page / inspect_screenshot / 失败截图）才能写入。",
    ]
