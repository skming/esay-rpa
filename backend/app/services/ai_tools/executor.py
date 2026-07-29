"""RpaToolExecutor：把模型的工具调用落到 flow_service / task_manager 上。

各类分析逻辑（lint、诊断、归一化）已拆到同包的其他模块，这里只留调度与副作用。
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core import storage
from app.services import browser_profile_lock
from app.services.browser_action_runner import detect_blocking_interstitial, persistent_browser_context
from app.services.ai_tools.catalog import NODE_TYPE_CATALOG
from app.services.ai_tools.diagnostics import (
    SELECTOR_MATCH_HIDDEN_OR_NOT_VISIBLE,
    SELECTOR_MATCH_NOT_VISIBLE,
    SELECTOR_MULTI_MATCH_FIRST_NOT_ACTIONABLE,
    SELECTOR_ZERO_MATCH,
    _assert_allowed_values,
    _assert_date_range,
    _build_input_variable_defaults,
    _build_quality_repair_plan,
    _build_run_root_cause_hints,
    _audit_binary_document,
    _audit_document_provenance,
    _BINARY_DOCUMENT_FORMATS,
    _check_requirement_alignment,
    build_navigation_trace,
    build_navigation_verdict,
    _check_structured_rows,
    DOCUMENT_CONTENT_MISMATCH,
    OUTPUT_CONTENT_MISMATCH,
    _describe_output_variables,
    _find_document_output,
    _MIN_DOCUMENT_CHARS,
    _find_header_variable,
    _find_incomplete_sweeps,
    _find_swallowed_critical_failures,
    _find_table_candidates,
    _requirement_wants_document,
    _guess_date_field,
    _guess_enum_field,
    _extract_requirement_targets,
    _infer_constraints_from_requirement,
    _parse_runtime_value,
)
from app.services.ai_tools.graph import _unreachable_node_ids
from app.services.ai_tools.lint import _lint_flow
from app.services.ai_tools.normalize import (
    _choose_layout_lane,
    _next_layout_lane,
    _node_ref,
    _nodes_visually_overlap,
    _normalize_generated_edges,
    _normalize_generated_nodes,
    _read_node_x,
    _read_node_y,
)
from app.services.ai_tools.page_probe_js import PAGE_PROBE_JS
from app.services.ai_tools.variables import _RUNTIME_BUILTINS, _collect_defined_vars, _validate_variable_refs

if TYPE_CHECKING:
    from app.services.flow_service import FlowService
    from app.services.scheduler_service import ScheduleService
    from app.services.task_manager import TaskManager


_LOGIN_URL_TOKENS = ("login", "signin", "sign-in", "auth", "sso", "passport")


_CONDITION_NODE_TYPES = ("control.condition",)

# run_flow 的调用参数名。塞进 variables 既不报错也不生效，是最难自查的一类静默失效
_RUN_CALL_PARAM_NAMES = frozenset({"browser_executor", "flow_id", "task_id"})

# category/sensitive 没标全时的兜底：凭据字段的命名相当稳定
_CREDENTIAL_NAME_TOKENS = (
    "password", "passwd", "pwd", "username", "user_name", "account",
    "token", "secret", "apikey", "api_key", "credential",
)


def _splice_branch_placeholder_noops(
    nodes: list[Any],
    edges: list[Any],
) -> tuple[list[Any], list[Any], list[str]]:
    """摘掉条件分支上纯占位的 control.noop，把入边直接接到它的下游。

    运行器按 label 选边，false 边指向汇合节点本来就合法；占位节点是「分支必须有落点」
    这个误解的产物，每个条件分支都会多出一个空节点。只处理被条件分支边指向、且
    单出边的 noop——用户自己在画布上放的注释性 noop 不在此列。
    """
    node_map = {
        str(node["id"]): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    condition_ids = {
        nid for nid, node in node_map.items() if node.get("type") in _CONDITION_NODE_TYPES
    }
    valid_edges = [e for e in edges if isinstance(e, dict) and e.get("source") and e.get("target")]

    spliced: list[str] = []
    for nid, node in node_map.items():
        if node.get("type") != "control.noop":
            continue
        incoming = [e for e in valid_edges if str(e["target"]) == nid]
        outgoing = [e for e in valid_edges if str(e["source"]) == nid]
        if len(outgoing) != 1 or not incoming:
            continue
        if not any(str(e["source"]) in condition_ids for e in incoming):
            continue
        successor = str(outgoing[0]["target"])
        if successor == nid:
            continue
        for edge in incoming:
            edge["target"] = successor
        valid_edges = [e for e in valid_edges if e is not outgoing[0]]
        spliced.append(nid)

    if not spliced:
        return nodes, edges, []

    # 改接后可能出现自环或重边（两条分支各自接到同一个汇合点）
    kept: list[Any] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in valid_edges:
        source, target = str(edge["source"]), str(edge["target"])
        if source == target:
            continue
        key = (source, target, str(edge.get("label") or ""))
        if key in seen:
            continue
        seen.add(key)
        kept.append(edge)

    remaining_nodes = [
        node for node in nodes
        if not (isinstance(node, dict) and str(node.get("id")) in set(spliced))
    ]
    return remaining_nodes, kept, spliced


def _profile_busy_block(tool_name: str) -> dict[str, Any] | None:
    """浏览器 profile 被别的运行占着时，返回一份「别修流程、去找用户」的阻断结果。

    按任务状态自查（原来只看 status == "running"）会漏掉 paused_for_human：等人工接管的运行
    照样开着浏览器窗口，此时 inspect_page 会拿到一屏 Chrome 启动参数当报错，模型接着去改
    selector——错的方向。占用登记表是唯一知道「谁开着浏览器」的地方。
    """
    held = browser_profile_lock.holder(str(storage.resolve_browser_profile_dir()))
    if held is None:
        return None
    return {
        "status": "blocked_browser_profile_busy",
        "holder": held,
        "error": f"{tool_name} 需要打开浏览器，但{browser_profile_lock.busy_message(held)}",
        "message": (
            "这是浏览器被占用，不是流程配置有问题：不要改流程、不要重试其他工具，"
            "把上面这句话转告用户，等他处理完再继续。"
        ),
    }


def _annotate_login_redirect(result: dict[str, Any], requested_url: str) -> None:
    """标注 inspect_page 请求的是目标页、实际落到了登录页。

    带 redirect 参数的 SPA 会保留原路径，光看 url 像是到了目标页；这时返回的 DOM
    是登录表单而非目标页结构，据此写出来的 selector 必然对不上。
    """
    landed = str(result.get("url") or "")
    if not landed or landed.rstrip("/") == requested_url.rstrip("/"):
        return
    landed_lower = landed.lower()
    has_password_input = any(
        isinstance(item, dict) and item.get("type") == "password"
        for item in result.get("inputs") or []
    )
    if not (has_password_input and any(token in landed_lower for token in _LOGIN_URL_TOKENS)):
        return
    result["redirected_to_login"] = True
    result["warning"] = (
        f"⚠️ 请求的是 {requested_url}，实际停在登录页 {landed}——"
        "本次返回的是登录表单 DOM，不是目标页结构。\n"
        "禁止据此修改目标页的 browser.wait / browser.extract selector，也不能据此断定"
        "目标页「不是表格」或「结构不对」——你根本没看到目标页。\n"
        "要拿到目标页 DOM，需先在浏览器 profile 里完成登录：运行一次含登录链路的流程，"
        "或用 inspect_screenshot 确认登录态后重试。"
    )


def _alignment_pass_message(alignment: dict[str, Any] | None, confirmed: bool) -> str:
    """审计通过时说清「内容是否对得上需求」这一项到底有没有校验过。

    一句笼统的"审计通过"会被当成需求已满足的结论，而结构校验根本不看这个维度。
    """
    if alignment is None:
        return (
            "结构与约束校验通过。未能从需求文本中提取出业务目标词，"
            "本次没有校验内容相关性——请自行比对 sample_rows 与用户需求后再汇报。"
        )
    if alignment["aligned"]:
        return f"运行质量审计通过，需求关键词 {alignment['matched']} 在输出中命中。"
    return (
        f"结构与约束校验通过，但需求关键词 {alignment['targets']} 未在输出中出现；"
        "内容相关性是你通过 content_match_confirmed 显式确认的，责任在你。"
    )


class RpaToolExecutor:
    def __init__(
        self,
        flow_service: "FlowService",
        task_manager: "TaskManager",
        schedule_service: "ScheduleService | None" = None,
    ) -> None:
        self._flow_service = flow_service
        self._task_manager = task_manager
        self._schedule_service = schedule_service
        # (flow_id, node_id, patch) 哈希去重，拦截模型重复调用相同 apply_node_fix
        self._applied_patch_hashes: set[str] = set()
        # 运行"成功"但业务断言失败的证据，供下次 run_flow 使用，防止 成功→审计失败→盲目重跑 死循环
        self._quality_failures_by_flow: dict[str, list[dict[str, Any]]] = {}

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        progress_sink: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """progress_sink：调用方传入的可变字典，长耗时工具在执行途中往里写进度。

        执行器是全进程单例，进度不能挂在 self 上——两个会话同时跑会互相覆盖。
        """
        match name:
            case "lint_flow":
                return await self._lint_flow_tool(**args)
            case "list_node_types":
                return await self._list_node_types()
            case "get_flow":
                return await self._get_flow(**args)
            case "validate_flow":
                return await self._validate_flow(**args)
            case "list_flows":
                return await self._list_flows()
            case "create_flow":
                return await self._create_flow(**args)
            case "update_flow":
                return await self._update_flow(**args)
            case "run_flow":
                return await self._run_flow(**args, progress_sink=progress_sink)
            case "check_extension_connection":
                return await self._check_extension_connection()
            case "stop_run":
                return await self._stop_run(**args)
            case "list_schedules":
                return await self._list_schedules()
            case "create_schedule":
                return await self._create_schedule(**args)
            case "toggle_schedule":
                return await self._toggle_schedule(**args)
            case "get_run_status":
                return await self._get_run_status(**args)
            case "get_run_error":
                return await self._get_run_error(**args)
            case "apply_node_fix":
                return await self._apply_node_fix(**args)
            case "publish_flow":
                return await self._publish_flow(**args)
            case "get_run_output":
                return await self._get_run_output(**args)
            case "assert_run_output":
                return await self._assert_run_output(**args)
            case "get_run_logs":
                return await self._get_run_logs(**args)
            case "inspect_page":
                return await self._inspect_page(**args)
            case "inspect_screenshot":
                return await self._inspect_screenshot(**args)
            case _:
                return {"error": f"未知工具: {name}"}

    async def _lint_flow_tool(self, flow_id: str) -> dict[str, Any]:
        flow = await self._flow_service.get_flow(flow_id)
        if flow is None:
            return {"error": f"流程 {flow_id} 不存在"}
        nodes: list[Any] = flow.definition.get("nodes", [])
        edges: list[Any] = flow.definition.get("edges", [])
        input_var_names = [iv.name for iv in flow.input_variables]
        findings = _lint_flow(nodes, edges, input_variable_names=input_var_names)
        errors = [f for f in findings if f["severity"] == "error"]
        warns = [f for f in findings if f["severity"] == "warn"]
        return {
            "flow_id": flow_id,
            "flow_name": flow.name,
            "findings": findings,
            "error_count": len(errors),
            "warn_count": len(warns),
            "is_clean": len(findings) == 0,
            "summary": (
                f"发现 {len(errors)} 个错误、{len(warns)} 个警告，请逐项修复后再运行。"
                if findings else "未发现任何问题。"
            ),
        }

    async def _list_node_types(self) -> dict[str, Any]:
        return {"node_types": NODE_TYPE_CATALOG}

    @staticmethod
    def _credential_readiness(flow: Any, supplied: set[str] | None = None) -> dict[str, Any]:
        """判定「凭据填好了没」。

        这件事本该由工具算：字段是不是凭据、值算不算空、节点到底引没引用它，
        三个条件缺一不可，交给模型逐字段目测 `value` 只会得到时对时错的结论
        （少一个字段就当没填、面板里填过的又看不见）。
        """
        supplied = supplied or set()
        definition_text = json.dumps(flow.definition or {}, ensure_ascii=False)
        empty: list[str] = []
        for var in flow.input_variables:
            is_credential = var.category == "credential" or var.sensitive or any(
                token in var.name.lower() for token in _CREDENTIAL_NAME_TOKENS
            )
            if not is_credential or var.name in supplied:
                continue
            if (var.value or "").strip():
                continue
            # 声明了却没人引用的空凭据不影响运行，报出来只会引出一次无谓的追问
            if f"${{var.{var.name}}}" not in definition_text:
                continue
            empty.append(var.name)
        return {
            "ready": not empty,
            "empty_credential_fields": empty,
            "message": (
                f"凭据变量 {empty} 有引用但没有值，运行必然失败。"
                "请告知用户先在右侧「输入变量」面板填写后再运行，不要自动 run_flow。"
            ) if empty else None,
        }

    async def _get_flow(self, flow_id: str) -> dict[str, Any]:
        flow = await self._flow_service.get_flow(flow_id)
        if flow is None:
            return {"error": f"流程 {flow_id} 不存在"}
        data = flow.model_dump(mode="json")
        # snapshots 可达 60k-120k 字符，剔除以免拖慢 UI 和浪费 token
        data.pop("snapshots", None)
        existing_var_names = [iv.name for iv in flow.input_variables]
        if existing_var_names:
            data["existing_variable_names"] = existing_var_names
            data["variable_reuse_hint"] = (
                f"新增节点必须优先引用已有变量 {existing_var_names}（如 ${{var.{existing_var_names[0]}}}），"
                "禁止为同一概念重复声明不同名称的变量。"
            )
            data["run_readiness"] = self._credential_readiness(flow)
        return data

    async def _validate_flow(self, flow_id: str) -> dict[str, Any]:
        """Scan a flow for undefined variable references."""
        flow = await self._flow_service.get_flow(flow_id)
        if flow is None:
            return {"error": f"流程 {flow_id} 不存在"}

        nodes: list[Any] = flow.definition.get("nodes", [])
        input_var_names = [iv.name for iv in flow.input_variables]
        defined = _collect_defined_vars(nodes, input_var_names)
        issues = _validate_variable_refs(nodes, input_var_names)

        return {
            "flow_id": flow_id,
            "flow_name": flow.name,
            "input_variables": input_var_names,
            "defined_variables": sorted(defined - _RUNTIME_BUILTINS),
            "runtime_builtins": sorted(_RUNTIME_BUILTINS),
            "issues": issues,
            "is_valid": len(issues) == 0,
            "fix_hint": (
                "对每个 issue：\n"
                "1. 找到应当产出该变量的上游节点，在其 outputVariable / variableName 字段填入缺失变量名。\n"
                "2. 或者新增 variable.set 节点在引用点之前定义该变量。\n"
                "使用 apply_node_fix 直接修复单个节点，或 update_flow 批量修改。"
            ) if issues else None,
        }

    async def _list_flows(self) -> dict[str, Any]:
        flows = await self._flow_service.list_flows()
        return {
            "flows": [
                {"flow_id": f.flow_id, "name": f.name, "status": f.status, "description": f.description}
                for f in flows
            ]
        }

    @staticmethod
    def _normalize_layout(nodes: list[dict], edges: list[Any] | None = None) -> None:
        """按图拓扑重新布局画布；AI 给出的坐标只当作不可信提示，忽略重算。"""
        from collections import defaultdict, deque

        MAIN_X, LANE_STEP_X, ROW_STEP_Y = 560, 360, 130
        START_Y = 20
        NODE_H, GAP = 84, 24

        node_map: dict[str, dict] = {
            str(node["id"]): node
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("id"), str) and node.get("id")
        }
        if not node_map:
            return

        out_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        in_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        incoming_count: dict[str, int] = {node_id: 0 for node_id in node_map}
        for edge in edges or []:
            if not isinstance(edge, dict):
                continue
            source = edge.get("source")
            target = edge.get("target")
            if source in node_map and target in node_map:
                out_edges[str(source)].append(edge)
                in_edges[str(target)].append(edge)
                incoming_count[str(target)] = incoming_count.get(str(target), 0) + 1

        if not out_edges:
            ordered = sorted(node_map.values(), key=lambda node: (_read_node_y(node), _read_node_x(node), str(node.get("id"))))
            for index, node in enumerate(ordered):
                node["position"] = {"x": MAIN_X, "y": START_Y + index * ROW_STEP_Y}
            return

        def _edge_sort_key(edge: dict[str, Any]) -> tuple[int, str]:
            label = str(edge.get("label") or "").strip().lower()
            branch_order = {
                "true": 0, "是": 0, "body": 0,
                "": 1,
                "false": 2, "否": 2, "exit": 2,
            }
            return (branch_order.get(label, 1), str(edge.get("target") or ""))

        for source in list(out_edges):
            out_edges[source].sort(key=_edge_sort_key)

        roots = ["start"] if "start" in node_map else [node_id for node_id, count in incoming_count.items() if count == 0]
        if not roots:
            roots = [next(iter(node_map))]

        reachable: set[str] = set()
        queue: deque[str] = deque(roots)
        while queue:
            node_id = queue.popleft()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            for edge in out_edges.get(node_id, []):
                target = str(edge.get("target"))
                if target not in reachable:
                    queue.append(target)

        reachable_in_degree: dict[str, int] = {
            node_id: sum(
                1
                for edge in in_edges.get(node_id, [])
                if str(edge.get("source")) in reachable
            )
            for node_id in reachable
        }
        topo_queue: deque[str] = deque(sorted(
            (node_id for node_id in reachable if reachable_in_degree.get(node_id, 0) == 0),
            key=lambda node_id: (0 if node_id == "start" else 1, _read_node_y(node_map[node_id]), _read_node_x(node_map[node_id]), node_id),
        ))

        level_by_id: dict[str, int] = {root: 0 for root in roots if root in reachable}
        lane_by_id: dict[str, int] = {root: 0 for root in roots if root in reachable}
        lane_candidates: dict[str, list[int]] = defaultdict(list)
        processed: list[str] = []

        while topo_queue:
            node_id = topo_queue.popleft()
            if node_id in processed:
                continue
            if node_id not in level_by_id:
                predecessor_levels = [
                    level_by_id[str(edge.get("source"))] + 1
                    for edge in in_edges.get(node_id, [])
                    if str(edge.get("source")) in level_by_id
                ]
                level_by_id[node_id] = max(predecessor_levels, default=0)
            candidates = lane_candidates.get(node_id)
            if candidates:
                lane = candidates[0]
                for candidate in candidates[1:]:
                    lane = _choose_layout_lane(lane, candidate)
                lane_by_id[node_id] = lane
            else:
                lane_by_id.setdefault(node_id, 0)

            processed.append(node_id)
            for edge in out_edges.get(node_id, []):
                target = str(edge.get("target"))
                if target not in reachable:
                    continue
                candidate_level = level_by_id[node_id] + 1
                level_by_id[target] = max(level_by_id.get(target, candidate_level), candidate_level)
                lane_candidates[target].append(_next_layout_lane(lane_by_id[node_id], edge.get("label")))
                reachable_in_degree[target] = reachable_in_degree.get(target, 0) - 1
                if reachable_in_degree[target] == 0:
                    topo_queue.append(target)

        # 环或畸形图可能导致部分可达节点未被处理，仍需给出确定性坐标
        if len(processed) < len(reachable):
            tail_level = max(level_by_id.values(), default=0) + 1
            for node_id in sorted(reachable - set(processed), key=lambda item: (_read_node_y(node_map[item]), _read_node_x(node_map[item]), item)):
                level_by_id.setdefault(node_id, tail_level)
                lane_by_id.setdefault(node_id, 0)
                tail_level += 1

        # 孤立节点保留但推入独立车道，画布可查且 lint 仍能报告不可达
        next_level = (max(level_by_id.values()) + 1) if level_by_id else 0
        for node_id in sorted(node_map):
            if node_id not in level_by_id:
                level_by_id[node_id] = next_level
                lane_by_id[node_id] = 2
                next_level += 1

        rows: dict[tuple[int, int], list[str]] = defaultdict(list)
        for node_id, level in level_by_id.items():
            rows[(level, lane_by_id.get(node_id, 0))].append(node_id)

        for (level, lane), ids in rows.items():
            ids.sort(key=lambda node_id: (_read_node_y(node_map[node_id]), _read_node_x(node_map[node_id]), node_id))
            for offset, node_id in enumerate(ids):
                node_map[node_id]["position"] = {
                    "x": MAIN_X + lane * LANE_STEP_X,
                    "y": START_Y + (level + offset) * ROW_STEP_Y,
                }

        layout_nodes = [node for node in node_map.values() if isinstance(node.get("position"), dict)]
        changed = True
        passes = 0
        while changed and passes < len(layout_nodes) * 2:
            changed = False
            passes += 1
            layout_nodes.sort(key=lambda node: (_read_node_y(node), _read_node_x(node), str(node.get("id"))))
            for index, left in enumerate(layout_nodes):
                for right in layout_nodes[index + 1:]:
                    if not _nodes_visually_overlap(left, right):
                        continue
                    target = right if _read_node_y(right) >= _read_node_y(left) else left
                    target["position"] = {
                        "x": _read_node_x(target),
                        "y": _read_node_y(target) + NODE_H + GAP,
                    }
                    changed = True
                    break
                if changed:
                    break

        if "start" in node_map:
            node_map["start"]["position"] = {"x": MAIN_X, "y": START_Y}
        if "end" in node_map:
            end_lane = lane_by_id.get("end", 0)
            max_y = max(
                (_read_node_y(node) for node_id, node in node_map.items() if node_id != "end"),
                default=START_Y,
            )
            node_map["end"]["position"] = {"x": MAIN_X + end_lane * LANE_STEP_X, "y": max_y + ROW_STEP_Y}

    async def _create_flow(
        self,
        name: str,
        nodes: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
        description: str | None = None,
        input_variables: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from app.models.schemas import FlowCreateRequest

        nodes = list(nodes or [])
        edges = list(edges or [])
        nodes = _normalize_generated_nodes(nodes)
        edges = _normalize_generated_edges(edges)

        node_ids = {n.get("id") for n in nodes if isinstance(n, dict)}
        biz_nodes = [n for n in nodes if isinstance(n, dict) and n.get("id") not in ("start", "end")]

        if "start" not in node_ids:
            if biz_nodes:
                ys = [n.get("position", {}).get("y", 100) for n in biz_nodes]
                xs = [n.get("position", {}).get("x", 560) for n in biz_nodes]
                cx = sum(xs) // len(xs)
                start_y = min(ys) - 120
            else:
                cx, start_y = 560, 20
            nodes.insert(0, {
                "id": "start", "type": "start", "title": "开始",
                "kind": "control", "status": "pending",
                "position": {"x": cx, "y": start_y},
            })
            if biz_nodes:
                first_id = biz_nodes[0]["id"]
                edges.insert(0, {"id": f"e-start-{first_id}", "source": "start", "target": first_id})

        if "end" not in node_ids:
            biz_nodes2 = [n for n in nodes if isinstance(n, dict) and n.get("id") not in ("start", "end")]
            if biz_nodes2:
                ys = [n.get("position", {}).get("y", 100) for n in biz_nodes2]
                xs = [n.get("position", {}).get("x", 560) for n in biz_nodes2]
                cx = sum(xs) // len(xs)
                end_y = max(ys) + 120
            else:
                cx, end_y = 560, 160
            nodes.append({
                "id": "end", "type": "end", "title": "结束",
                "kind": "control", "status": "pending",
                "position": {"x": cx, "y": end_y},
            })
            if biz_nodes2:
                last_id = biz_nodes2[-1]["id"]
                edges.append({"id": f"e-{last_id}-end", "source": last_id, "target": "end"})

        self._normalize_layout(nodes, edges)

        definition: dict[str, Any] = {"nodes": nodes, "edges": edges}

        iv_names: list[str] = []
        iv_snapshots: list[dict[str, Any]] = []
        for iv in (input_variables or []):
            iv_name = iv.get("name", "")
            if iv_name:
                iv_names.append(iv_name)
                raw_type = iv.get("type", "String")
                _type_map = {"string": "String", "integer": "Integer", "int": "Integer",
                             "boolean": "Boolean", "bool": "Boolean", "list": "List",
                             "array": "List", "dict": "Dict", "object": "Dict"}
                norm_type = _type_map.get(str(raw_type).lower(), raw_type)
                raw_scope = iv.get("scope", "全局")
                norm_scope = raw_scope if raw_scope in ("全局", "循环", "局部") else "全局"
                iv_snapshots.append({
                    "name": iv_name,
                    "type": norm_type,
                    # 存储字段叫 value；defaultValue 只是模型的旧写法，留作输入别名
                    "value": str(iv.get("value") or iv.get("defaultValue") or ""),
                    "scope": norm_scope,
                    "category": iv.get("category", "credential") if any(
                        kw in iv_name.lower() for kw in ("password", "passwd", "secret", "token", "key", "pwd")
                    ) else iv.get("category", "flow"),
                    "sensitive": bool(iv.get("sensitive", any(
                        kw in iv_name.lower() for kw in ("password", "passwd", "secret", "token", "key", "pwd")
                    ))),
                })

        req = FlowCreateRequest(
            name=name,
            description=description,
            definition=definition,
            input_variables=iv_snapshots,
        )
        flow = await self._flow_service.create_flow(req)

        issues = _validate_variable_refs(nodes, iv_names)
        lint_findings = _lint_flow(nodes, edges, input_variable_names=iv_names)
        result: dict[str, Any] = {
            "flow_id": flow.flow_id,
            "name": flow.name,
            "status": flow.status,
        }
        if issues:
            result["validation_issues"] = issues
            result["validation_warning"] = (
                "流程已创建，但存在未定义变量引用（见 validation_issues）。"
                "请调用 validate_flow 查看详情，再用 apply_node_fix 或 update_flow 修复后运行。"
            )
        if lint_findings:
            result["lint_findings"] = lint_findings
            result["lint_warning"] = (
                f"静态检查发现 {sum(1 for f in lint_findings if f['severity']=='error')} 个错误、"
                f"{sum(1 for f in lint_findings if f['severity']=='warn')} 个警告（见 lint_findings），"
                "请逐项修复后再运行。"
            )
        else:
            # 干净时不给信号，模型分不清「查过没问题」和「压根没查」，只能再补一次 lint_flow
            result["lint_clean"] = True
        return result

    async def _update_flow(
        self,
        flow_id: str,
        name: str | None = None,
        add_nodes: list[dict[str, Any]] | None = None,
        update_nodes: list[dict[str, Any]] | None = None,
        remove_node_ids: list[str] | None = None,
        add_edges: list[dict[str, Any]] | None = None,
        remove_edge_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Apply structural changes to a flow immediately — no user confirmation required."""
        import copy as _copy
        from app.models.schemas import FlowUpdateRequest

        flow = await self._flow_service.get_flow(flow_id)
        if flow is None:
            return {"error": f"流程 {flow_id} 不存在"}

        existing_nodes: list[Any] = list(flow.definition.get("nodes", []))
        existing_edges: list[Any] = list(flow.definition.get("edges", []))
        existing_node_refs = {
            ref["id"]: ref
            for ref in (_node_ref(node) for node in existing_nodes)
            if ref is not None
        }
        add_nodes = _normalize_generated_nodes(list(add_nodes or []))
        add_edges = _normalize_generated_edges(list(add_edges or []))

        remove_set = set(remove_node_ids or [])
        explicit_remove_edge_ids = set(remove_edge_ids or [])

        # Guard: protect structural anchors
        protected_ids = {"start", "end"} & remove_set
        if protected_ids:
            return {
                "error": f"禁止删除流程锚节点：{', '.join(sorted(protected_ids))}。start/end 是流程入口/出口，删除会使整个流程无法运行。",
                "fix_hint": "如需重构流程，只移动或重连 start/end 的出入边，不要删除节点本身。",
            }

        # 部分模型会输出大写 Body/Exit/True/False，执行器分支路由只精确匹配小写，需先归一化
        _BRANCH_LABELS = {"body", "exit", "true", "false", "是", "否"}
        normalized_add_edges: list[dict[str, Any]] = []
        for e in (add_edges or []):
            if not isinstance(e, dict):
                normalized_add_edges.append(e)
                continue
            lbl = e.get("label")
            if isinstance(lbl, str) and lbl.lower() in _BRANCH_LABELS:
                e = {**e, "label": lbl.lower().strip()}
            normalized_add_edges.append(e)
        add_edges = normalized_add_edges

        # Pre-mutation structural validation
        # Reject references to nodes that won't exist *before* touching the flow,
        # so a hallucinated node id (e.g. an edge to a node never created) surfaces
        # as an actionable error instead of being silently swallowed by the
        # dangling-edge prune below — which would otherwise report "applied" while
        # leaving the flow broken.
        existing_ids = {n.get("id") for n in existing_nodes if isinstance(n, dict) and n.get("id")}
        final_ids = (existing_ids - remove_set) | {
            n.get("id") for n in (add_nodes or []) if isinstance(n, dict) and n.get("id")
        }
        struct_errors: list[str] = []
        seen_add: set[str] = set()
        for n in (add_nodes or []):
            nid = n.get("id") if isinstance(n, dict) else None
            if not nid:
                struct_errors.append("add_nodes 中存在缺少 id 的节点")
            elif nid in existing_ids and nid not in remove_set:
                struct_errors.append(f"新增节点 {nid} 与已有节点 id 冲突；如需修改请改用 update_nodes")
            elif nid in seen_add:
                struct_errors.append(f"add_nodes 中节点 id {nid} 重复")
            else:
                seen_add.add(nid)
        for e in (add_edges or []):
            if not isinstance(e, dict):
                continue
            src, tgt = e.get("source"), e.get("target")
            if src not in final_ids:
                struct_errors.append(
                    f"新增连线 {src}→{tgt} 的起点节点 {src!r} 不存在；请先用 add_nodes 创建该节点，或修正起点 id"
                )
            if tgt not in final_ids:
                struct_errors.append(
                    f"新增连线 {src}→{tgt} 的终点节点 {tgt!r} 不存在；请先用 add_nodes 创建该节点，或修正终点 id"
                )
        for u in (update_nodes or []):
            uid = u.get("id") if isinstance(u, dict) else None
            if uid not in final_ids:
                struct_errors.append(f"要修改的节点 {uid!r} 不存在")
        if struct_errors:
            return {
                "error": "结构校验失败，变更未应用",
                "validation_errors": struct_errors,
                "fix_hint": "连线/修改只能引用已存在或本次 add_nodes 新建的节点。请先创建被引用的节点或修正 id，可调用 validate_flow 查看当前节点 id 后重试。",
            }

        # Detect bypassed edges (A→C when A→B + B→C are newly added)
        new_edge_pairs: set[tuple[str, str]] = set()
        for e in (add_edges or []):
            if isinstance(e, dict) and e.get("source") and e.get("target"):
                new_edge_pairs.add((e["source"], e["target"]))

        new_node_ids = {n.get("id") for n in (add_nodes or []) if isinstance(n, dict) and n.get("id")}

        auto_remove_edge_ids: set[str] = set()
        for e in existing_edges:
            if not isinstance(e, dict):
                continue
            eid = e.get("id", "")
            src, tgt = e.get("source", ""), e.get("target", "")
            if src in remove_set or tgt in remove_set:
                auto_remove_edge_ids.add(eid)
                continue
            for mid in new_node_ids:
                if (src, mid) in new_edge_pairs and (mid, tgt) in new_edge_pairs:
                    auto_remove_edge_ids.add(eid)
                    break

        final_remove_edge_ids = explicit_remove_edge_ids | auto_remove_edge_ids

        definition = _copy.deepcopy(dict(flow.definition))
        nodes: list = [n for n in definition.get("nodes", []) if isinstance(n, dict) and n.get("id") not in remove_set]
        edges: list = [e for e in definition.get("edges", []) if isinstance(e, dict) and e.get("id") not in final_remove_edge_ids]

        # Auto-prune dangling edges left by removed nodes
        surviving_ids = {n["id"] for n in nodes if isinstance(n, dict) and n.get("id")} | new_node_ids
        edges = [e for e in edges if isinstance(e, dict) and e.get("source") in surviving_ids and e.get("target") in surviving_ids]

        patch_map = {u["id"]: u["patch"] for u in (update_nodes or []) if "id" in u and "patch" in u}
        for node in nodes:
            if isinstance(node, dict) and node.get("id") in patch_map:
                node.update(patch_map[node["id"]])

        # 按 x 列分组新节点，把插入点及以下的已有节点整体下移，避免与新节点 y 轴重叠
        if add_nodes:
            _COLUMN_TOL = 200   # px — nodes within this x-distance share a column
            _NODE_STEP  = 100   # px — standard gap between adjacent nodes
            patched_positions = {u["id"] for u in (update_nodes or []) if "position" in u.get("patch", {})}
            col_new_ys: dict[int, list[int]] = {}
            for _n in (add_nodes or []):
                if not isinstance(_n, dict):
                    continue
                _pos = _n.get("position")
                if not isinstance(_pos, dict):
                    continue
                _col = round(_pos.get("x", 560) / _COLUMN_TOL) * _COLUMN_TOL
                col_new_ys.setdefault(_col, []).append(int(_pos.get("y", 0)))
            for _node in nodes:
                if not isinstance(_node, dict):
                    continue
                _nid = _node.get("id")
                if _nid in patched_positions:
                    continue
                _pos = _node.get("position")
                if not isinstance(_pos, dict):
                    continue
                _nx, _ny = _pos.get("x", 560), _pos.get("y", 0)
                _col = round(_nx / _COLUMN_TOL) * _COLUMN_TOL
                if _col not in col_new_ys:
                    continue
                _ys = col_new_ys[_col]
                _min_new_y = min(_ys)
                if _ny < _min_new_y:
                    continue
                _shift = max(_ys) - _min_new_y + _NODE_STEP
                _node["position"] = {"x": _nx, "y": _ny + _shift}

        # 从 action type 前缀自动补全 kind，即使 AI 遗漏该字段前端也能显示正确节点颜色
        _VALID_KINDS = frozenset({
            "browser", "excel", "file", "http", "variable", "control",
            "python", "notify", "data", "json", "wait",
        })
        normalized_add: list = []
        for _n in (add_nodes or []):
            if not isinstance(_n, dict):
                normalized_add.append(_n)
                continue
            if not _n.get("kind"):
                _action_type: str = ""
                _action = _n.get("action")
                if isinstance(_action, dict):
                    _action_type = str(_action.get("type", ""))
                elif isinstance(_n.get("config"), dict):
                    _action_type = str(_n.get("type", ""))
                _prefix = _action_type.split(".")[0] if _action_type else ""
                if _prefix in _VALID_KINDS:
                    _n = {**_n, "kind": _prefix}
            normalized_add.append(_n)
        nodes.extend(normalized_add)
        new_edges_list: list = list(add_edges or [])

        # Drop bypassed existing edges
        new_pair_set: set[tuple[str, str]] = {(e.get("source", ""), e.get("target", "")) for e in new_edges_list if isinstance(e, dict)}
        all_ids = {n.get("id", "") for n in nodes if isinstance(n, dict)}
        def _is_bypassed(e: dict) -> bool:
            src, tgt = e.get("source", ""), e.get("target", "")
            for mid in all_ids:
                if (src, mid) in new_pair_set and (mid, tgt) in new_pair_set:
                    return True
            return False
        edges = [e for e in edges if not (isinstance(e, dict) and _is_bypassed(e))]
        edges.extend(new_edges_list)

        seen: set[tuple[str, str]] = set()
        deduped: list = []
        for e in edges:
            if not isinstance(e, dict):
                continue
            pair = (e.get("source", ""), e.get("target", ""))
            if pair not in seen:
                seen.add(pair)
                deduped.append(e)
        edges = deduped

        # 清理空流程自带的 start→end 骨架边：上面的 bypass 检测只认「新增单个中间节点」
        # （A→M 且 M→C），一次插入整条链时匹配不上，边会残留。
        _start_ids = {n.get("id") for n in nodes if isinstance(n, dict) and n.get("type") == "start"}
        _end_ids = {n.get("id") for n in nodes if isinstance(n, dict) and n.get("type") == "end"}
        if _start_ids and _end_ids:
            _skeleton = [
                e for e in edges
                if isinstance(e, dict) and e.get("source") in _start_ids and e.get("target") in _end_ids
            ]
            _other_start_out = [
                e for e in edges
                if isinstance(e, dict) and e.get("source") in _start_ids and e.get("target") not in _end_ids
            ]
            if _skeleton and _other_start_out:
                edges = [e for e in edges if e not in _skeleton]

        nodes, edges, spliced_noop_ids = _splice_branch_placeholder_noops(nodes, edges)

        self._normalize_layout(nodes, edges)

        # 若本次变更会使原本可达的节点变孤立，阻止写入并报错，防止 AI 修复时误切断流程
        currently_unreachable = set(_unreachable_node_ids(existing_nodes, existing_edges))
        proposed_unreachable = set(_unreachable_node_ids(nodes, edges))
        newly_orphaned = proposed_unreachable - currently_unreachable
        # Exclude nodes that were explicitly removed (they're expected to disappear)
        newly_orphaned -= remove_set
        if newly_orphaned:
            return {
                "error": (
                    f"变更被阻止：以下节点在修改后将失去连通性（孤立）：{', '.join(sorted(newly_orphaned))}。"
                    "通常是漏接了入边，或删除了某个节点但未重连其上下游。"
                ),
                "newly_orphaned": sorted(newly_orphaned),
                "fix_hint": (
                    "请同时在 add_edges 中补全受影响节点的入边，"
                    "或先用 update_flow 只添加新节点+连线，确认连通后再删除旧节点。"
                ),
            }

        definition["nodes"] = nodes
        definition["edges"] = edges

        # 只在流程仍是占位名时接受 AI 给出的标题，已经有正式名称就不允许覆盖，
        # 避免模型在无关的结构性修改里顺手把用户自己起的名字改掉。
        _PLACEHOLDER_FLOW_NAMES = {"新建 RPA 流程", "未命名流程"}
        new_name = name.strip() if isinstance(name, str) and name.strip() else None
        if new_name is not None and flow.name not in _PLACEHOLDER_FLOW_NAMES:
            new_name = None

        req = FlowUpdateRequest(definition=definition, name=new_name)
        updated = await self._flow_service.update_flow(flow_id, req)
        if updated is None:
            return {"error": "流程更新失败，未找到对应流程"}

        # Post-change validation (informational only — changes are already applied)
        input_var_names = [iv.name for iv in flow.input_variables]
        issues = _validate_variable_refs(nodes, input_var_names)
        lint_findings = _lint_flow(nodes, edges, input_variable_names=input_var_names)
        final_node_refs = {
            ref["id"]: ref
            for ref in (_node_ref(node) for node in nodes)
            if ref is not None
        }
        changed_nodes: list[dict[str, str]] = []
        for node in add_nodes or []:
            ref = _node_ref(node)
            if ref:
                changed_nodes.append({**ref, "change": "added"})
        updated_node_snapshots: list[dict[str, Any]] = []
        for item in update_nodes or []:
            uid = item.get("id") if isinstance(item, dict) else None
            if isinstance(uid, str):
                ref = final_node_refs.get(uid) or existing_node_refs.get(uid)
                if ref:
                    changed_nodes.append({**ref, "change": "updated"})
                # Emit a snapshot of the patched fields so the AI can verify
                # the change actually landed (prevents "AI said fixed but same
                # error repeated" loops where update_nodes silently had no effect).
                patch_keys = list((item.get("patch") or {}).keys())
                actual_node = next((n for n in nodes if isinstance(n, dict) and n.get("id") == uid), None)
                if actual_node and patch_keys:
                    updated_node_snapshots.append({
                        "node_id": uid,
                        "patched_fields": {k: actual_node.get(k) for k in patch_keys},
                    })
        for node_id in remove_node_ids or []:
            ref = existing_node_refs.get(node_id)
            if ref:
                changed_nodes.append({**ref, "change": "removed"})

        result: dict[str, Any] = {
            "status": "applied",
            "flow_id": flow_id,
            "flow_name": updated.name,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }
        if changed_nodes:
            result["changed_nodes"] = changed_nodes
            result["changed_node_labels"] = [node["label"] for node in changed_nodes]
        if spliced_noop_ids:
            result["spliced_placeholder_nodes"] = spliced_noop_ids
            result["spliced_placeholder_note"] = (
                f"已摘除直通的 control.noop 占位节点 {spliced_noop_ids} 并把入边直接连到其下游："
                "条件节点的分支边可以直接指向汇合节点，不需要占位。"
            )
        if updated_node_snapshots:
            result["updated_node_snapshots"] = updated_node_snapshots
            result["verify_hint"] = (
                "请核查 updated_node_snapshots 中每个节点的 patched_fields，"
                "确认字段值与你的修改意图一致后再运行流程。"
            )
        if issues:
            result["validation_issues"] = issues
            result["validation_warning"] = "变更已应用，但仍存在未定义变量引用，建议继续修复。"

        if lint_findings:
            result["lint_findings"] = lint_findings
            result["lint_warning"] = (
                f"静态检查发现 {sum(1 for f in lint_findings if f['severity']=='error')} 个错误、"
                f"{sum(1 for f in lint_findings if f['severity']=='warn')} 个警告（见 lint_findings），"
                "请逐项修复后再运行。"
            )
        else:
            # 干净时不给信号，模型分不清「查过没问题」和「压根没查」，只能再补一次 lint_flow
            result["lint_clean"] = True

        # 删除入边后忘记重连下游会静默孤立整条分支，需要提醒 AI/用户补连
        unreachable = _unreachable_node_ids(nodes, edges)
        if unreachable:
            result["connectivity_warning"] = (
                f"以下节点无法从流程起点到达，运行时会被跳过：{', '.join(unreachable)}。"
                "通常是连线缺失或被误删，请补连后再确认完成。"
            )
            result["unreachable_nodes"] = unreachable
        return result

    async def _check_extension_connection(self) -> dict[str, Any]:
        connected = self._task_manager.is_extension_connected()
        return {
            "connected": connected,
            "message": (
                "扩展已连接，可以使用 browser_executor='extension' 运行。"
                if connected
                else "扩展未连接。请提示用户：打开 Chrome 扩展、确认有已登录的目标网站标签页，再重试。"
            ),
        }

    async def _stop_run(self, task_id: str) -> dict[str, Any]:
        snapshot = await self._task_manager.stop_task(task_id)
        if snapshot is None:
            return {"error": f"任务 {task_id} 不存在"}
        return {
            "task_id": task_id,
            "status": snapshot.status,
            "message": (
                "任务已停止。"
                if snapshot.status == "stopped"
                else f"任务当前状态为 {snapshot.status}（已结束的任务无法停止）。"
            ),
        }

    @staticmethod
    def _schedule_brief(schedule: Any) -> dict[str, Any]:
        return {
            "schedule_id": schedule.schedule_id,
            "name": schedule.name,
            "cron_expression": schedule.cron_expression,
            "timezone": schedule.timezone,
            "status": schedule.status,
            "flow_id": schedule.task.flow_id,
            "browser_executor": schedule.task.browser_executor,
            "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
            "last_run_at": schedule.last_run_at.isoformat() if schedule.last_run_at else None,
        }

    async def _list_schedules(self) -> dict[str, Any]:
        if self._schedule_service is None:
            return {"error": "定时任务服务不可用"}
        schedules = await self._schedule_service.list_schedules()
        return {
            "schedules": [self._schedule_brief(s) for s in schedules],
            "count": len(schedules),
        }

    # 无人值守适配检查：定时任务运行时没人补输入/接管浏览器。
    _UNATTENDED_INCOMPATIBLE_NODE_TYPES = frozenset({"variable.input", "control.human_takeover"})

    async def _create_schedule(
        self,
        flow_id: str,
        cron_expression: str,
        name: str | None = None,
        timezone: str = "Asia/Shanghai",
        enabled: bool = True,
        variables: dict[str, Any] | None = None,
        browser_executor: str | None = None,
    ) -> dict[str, Any]:
        from pydantic import ValidationError

        from app.models.schemas import RunTaskRequest, ScheduleCreateRequest

        if self._schedule_service is None:
            return {"error": "定时任务服务不可用"}
        flow = await self._flow_service.get_flow(flow_id)
        if flow is None:
            return {"error": f"流程 {flow_id} 不存在"}

        nodes: list[Any] = flow.definition.get("nodes", [])
        pause_nodes = [
            {"id": n.get("id"), "type": n.get("type"), "title": n.get("title")}
            for n in nodes
            if isinstance(n, dict) and n.get("type") in self._UNATTENDED_INCOMPATIBLE_NODE_TYPES
        ]
        if pause_nodes:
            return {
                "error": "流程含需要人工参与的节点，不适合定时无人值守运行",
                "pause_nodes": pause_nodes,
                "hint": (
                    "定时触发时没有人补输入或接管浏览器，这些节点会让任务一直挂起直到超时。"
                    "请先改造流程（如改用 input_variables 静态凭据、依靠 browser.ensureLogin 复用登录态），"
                    "或改为手动运行。"
                ),
            }

        run_variables = variables or {}
        missing_vars = [
            {"name": iv.name, "category": getattr(iv, "category", "credential")}
            for iv in flow.input_variables
            if not (iv.value or "").strip() and iv.name not in run_variables
        ]
        if missing_vars:
            return {
                "error": f"输入变量 {[v['name'] for v in missing_vars]} 无默认值，定时无人值守运行时无法补输入",
                "missing_variables": missing_vars,
                "hint": "请先让用户在输入变量面板填写默认值，或在 create_schedule 的 variables 参数中提供。",
            }

        effective_executor = browser_executor or getattr(flow, "default_browser_executor", None) or "playwright"
        try:
            request = ScheduleCreateRequest(
                name=name or flow.name,
                cron_expression=cron_expression,
                timezone=timezone,
                enabled=enabled,
                task=RunTaskRequest(
                    flow_id=flow_id,
                    flow_name=flow.name,
                    variables=run_variables,
                    browser_executor=effective_executor,
                ),
            )
        except ValidationError as exc:
            return {"error": f"定时任务参数无效：{exc.errors()[0].get('msg', str(exc))}"}
        snapshot = await self._schedule_service.create_schedule(request)
        result = self._schedule_brief(snapshot)
        result["message"] = (
            f"定时任务已创建（{'已启用' if enabled else '未启用'}），"
            f"下次运行时间：{result['next_run_at'] or '待调度'}。"
        )
        if effective_executor == "extension":
            result["warning"] = (
                "该定时任务使用浏览器扩展执行器：触发时必须有已打开且已连接扩展的浏览器窗口，"
                "否则该次执行会失败。请确认用户知晓此限制。"
            )
        return result

    async def _toggle_schedule(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        from app.models.schemas import ScheduleUpdateRequest

        if self._schedule_service is None:
            return {"error": "定时任务服务不可用"}
        snapshot = await self._schedule_service.update_schedule(
            schedule_id, ScheduleUpdateRequest(enabled=enabled)
        )
        if snapshot is None:
            return {"error": f"定时任务 {schedule_id} 不存在"}
        result = self._schedule_brief(snapshot)
        result["message"] = f"定时任务已{'启用' if enabled else '停用'}。"
        return result

    async def _run_flow(
        self,
        flow_id: str,
        variables: dict[str, Any] | None = None,
        browser_executor: str = "playwright",
        progress_sink: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import asyncio
        from app.models.schemas import RunTaskRequest

        flow = await self._flow_service.get_flow(flow_id)
        if flow is None:
            return {"error": f"流程 {flow_id} 不存在"}

        if browser_executor == "extension" and not self._task_manager.is_extension_connected():
            return {
                "status": "extension_not_connected",
                "message": (
                    "已阻止运行：请求使用 browser_executor='extension'，但当前没有已连接的浏览器扩展。"
                    "请提示用户打开 Chrome 扩展并确认目标网站标签页已登录，不要静默改用 Playwright 执行器。"
                ),
            }

        # 起跑前判：profile 被占时浏览器根本起不来，失败现场是一屏 Chrome 启动参数，
        # 模型会当成 selector 问题一路改流程。插件执行器借用用户自己的浏览器，不受此限。
        if browser_executor != "extension":
            busy = _profile_busy_block("run_flow")
            if busy is not None:
                return busy

        failure_gate = await self._recent_failure_gate(flow_id)
        if failure_gate is not None:
            return failure_gate

        # 调用参数塞进 variables 会被当成普通运行时变量吞掉：不报错、不生效、照常跑完。
        # 用户以为切了执行器、实际没切，事后只能从产物反推，必须在起跑前判错。
        misplaced = sorted(set(variables or {}) & _RUN_CALL_PARAM_NAMES)
        if misplaced:
            return {
                "status": "misplaced_call_parameters",
                "misplaced_variables": misplaced,
                "message": (
                    f"{misplaced} 是 run_flow 的调用参数，不是流程变量。"
                    "写在 variables 里不会生效，运行会照常使用默认执行器。"
                    "请把它们作为 run_flow 的顶层参数重新调用，例如 browser_executor=\"extension\"。"
                ),
            }

        # 凭据为空时运行必然失败，且失败现场看起来像选择器问题，助手会一路查错方向。
        # 注意判据必须基于「调用方真的传了什么」：_build_input_variable_defaults 会把每个
        # 声明过的变量名都塞进 merged_variables（值可能是空串），拿合并结果判空永远判不出来。
        readiness = self._credential_readiness(flow, supplied=set(variables or {}))
        if not readiness["ready"]:
            return {
                "status": "empty_credential_variables",
                "empty_credential_fields": readiness["empty_credential_fields"],
                "message": (
                    f"凭据变量 {readiness['empty_credential_fields']} 有引用但没有值，运行必然失败。"
                    "**不要自行编造或猜测凭据值**，也不要改用 variable.input 绕开——"
                    "请告知用户在右侧「输入变量」面板填写后再运行。"
                ),
            }

        # 模型常在 input_variables 无默认值时也不传 variables，导致运行中途报"变量未定义"，这里提前拦截
        input_defaults = _build_input_variable_defaults(list(flow.input_variables))
        merged_variables: dict[str, Any] = {**input_defaults, **(variables or {})}
        supplied = set(merged_variables.keys())
        missing_vars = [
            {"name": iv.name, "category": getattr(iv, "category", "credential")}
            for iv in flow.input_variables
            if not (iv.value or "").strip() and iv.name not in supplied
        ]
        if missing_vars:
            return {
                "status": "missing_run_variables",
                "missing_variables": [v["name"] for v in missing_vars],
                "all_input_variables": [
                    {
                        "name": iv.name,
                        "category": getattr(iv, "category", "credential"),
                        "has_default": bool((iv.value or "").strip()),
                    }
                    for iv in flow.input_variables
                ],
                "message": (
                    f"run_flow 缺少必填变量：{[v['name'] for v in missing_vars]}。"
                    "这些 input_variables 无默认值，必须通过 variables 参数传入。"
                    "示例：variables={\"username\": \"demo_user\", \"password\": \"demo_pass\", \"captcha\": \"8888\"}"
                ),
            }

        nodes: list[Any] = flow.definition.get("nodes", [])
        edges: list[Any] = flow.definition.get("edges", [])
        input_var_names = [iv.name for iv in flow.input_variables]
        all_var_names = input_var_names + [k for k in merged_variables]
        lint_findings = _lint_flow(nodes, edges, input_variable_names=all_var_names)
        lint_errors = [finding for finding in lint_findings if finding.get("severity") == "error"]
        if lint_errors:
            return {
                "status": "blocking_lint_findings",
                "lint_findings": lint_errors[:12],
                "message": (
                    "流程存在阻断级静态检查错误，已阻止运行。"
                    "请按 lint_findings 修复变量字段、条件表达式、分支连线或节点配置后重试。"
                ),
            }
        issues = _validate_variable_refs(nodes, all_var_names)
        if issues:
            return {
                "status": "undefined_variable_refs",
                "undefined_refs": issues,
                "message": (
                    "流程存在节点引用了未定义变量，已阻止运行。"
                    "请用 validate_flow 或 lint_flow 查看详情，再用 apply_node_fix 或 update_flow 修复后重试。"
                ),
            }

        req = RunTaskRequest(
            flow_id=flow_id,
            flow_name=flow.name,
            flow_definition=flow.definition,
            variables={k: str(v) for k, v in merged_variables.items()},
            browser_executor=browser_executor,
        )
        task = await self._task_manager.start_task(req)

        def _publish_progress() -> None:
            # 运行要几分钟，AI 面板在这期间只能靠这份进度证明自己没死
            if progress_sink is None:
                return
            progress_sink.update({
                "task_id": task.task_id,
                "status": task.status,
                "current_step": task.progress.current_step,
                "total_steps": task.progress.total_steps,
                "percent": task.progress.percent,
            })

        _publish_progress()

        _TERMINAL = {"success", "error", "stopped"}
        _MAX_WAIT_S = 90
        _POLL_INTERVAL_S = 2
        elapsed = 0
        while task.status not in _TERMINAL and elapsed < _MAX_WAIT_S:
            await asyncio.sleep(_POLL_INTERVAL_S)
            elapsed += _POLL_INTERVAL_S
            refreshed = await self._task_manager.get_task(task.task_id)
            if refreshed is None:
                break
            task = refreshed
            _publish_progress()

        if task.status == "success":
            try:
                from app.services.site_knowledge import get_site_knowledge_store
                get_site_knowledge_store().record_flow_success(flow.definition, flow_name=flow.name)
            except Exception:
                pass  # 经验记录失败不能影响 run_flow 本身

        has_input_nodes = any(n.get("type") == "variable.input" for n in nodes)
        has_takeover_nodes = any(n.get("type") == "control.human_takeover" for n in nodes)
        result: dict[str, Any] = {
            "task_id": task.task_id,
            "status": task.status if task.status in _TERMINAL else "timeout",
            "flow_id": flow_id,
            "progress": task.progress.model_dump(mode="json") if task.progress else {},
        }
        if task.status not in _TERMINAL:
            # 「跑得慢」和「停下来等人」都表现为非终态，但处理方式相反：前者继续等，
            # 后者重跑只会再起一个任务、把旧的留在后台继续等。这个判断由本工具给出，
            # 不能丢给模型自己翻流程定义猜——它猜错的代价是一个孤儿任务。
            if task.status == "paused_for_human" or has_takeover_nodes:
                result["status"] = "paused_for_human"
                result["waiting_for_user_action"] = True
                result["message"] = (
                    "流程已暂停等待人工接管，浏览器窗口已在桌面打开。"
                    "请提示用户完成操作后在界面顶部卡片点击【已完成，继续】，不要重新运行流程。"
                )
            elif has_input_nodes:
                result["status"] = "waiting_for_user_input"
                result["message"] = (
                    "流程含 variable.input 节点，正在等待用户在界面输入变量后继续。"
                    "请提示用户到 RPA 界面底部填写输入后点击【继续】，不要重新运行流程。"
                )
                result["waiting_for_user_input"] = True
            else:
                result["message"] = f"流程已启动但 {_MAX_WAIT_S}s 内未完成，可用 get_run_status 查询当前状态。"
        if task.error:
            result["error_summary"] = task.error
        return result

    async def _recent_failure_gate(self, flow_id: str) -> dict[str, Any] | None:
        """近期证据显示无进展时阻断 AI 继续盲目重跑；只影响 AI 工具循环，不影响手动 UI 运行。"""
        recent_tasks = await self._task_manager.list_tasks(flow_id=flow_id, limit=5)
        evidence: list[dict[str, Any]] = []
        quality_failures = self._quality_failures_by_flow.get(flow_id, [])
        quality_by_task = {
            str(item.get("task_id")): item
            for item in quality_failures
            if item.get("task_id")
        }
        for task in recent_tasks:
            if task.status == "error":
                evidence.append({
                    "task_id": task.task_id,
                    "kind": "runtime_error",
                    "error": task.error or "",
                    "updated_at": task.updated_at,
                })
                continue
            if task.task_id in quality_by_task:
                audit = quality_by_task[task.task_id]
                evidence.append({
                    "task_id": task.task_id,
                    "kind": "quality_failure",
                    "error": "|".join(audit.get("issues", [])),
                    "updated_at": audit.get("created_at") or task.updated_at,
                })

        if len(evidence) < 3:
            return None
        recent_evidence = sorted(
            evidence,
            key=lambda item: item["updated_at"].timestamp()
            if isinstance(item.get("updated_at"), datetime)
            else 0,
            reverse=True,
        )[:3]
        if len(recent_evidence) < 3:
            return None

        failed_nodes: list[str] = []
        for item in recent_evidence:
            logs = await self._task_manager.get_logs(str(item["task_id"])) or []
            node_id = next((log.node_id for log in reversed(logs) if log.level == "error" and log.node_id), None)
            if node_id:
                failed_nodes.append(node_id)

        repeated_node = len(failed_nodes) >= 2 and len(set(failed_nodes[:3])) <= 2
        kinds = {str(item.get("kind")) for item in recent_evidence}
        same_error = len({str(item.get("error") or "")[:160] for item in recent_evidence}) <= 2
        same_quality_loop = "quality_failure" in kinds and (
            "runtime_error" in kinds or len(kinds) == 1
        )
        if not repeated_node and not same_error and not same_quality_loop:
            return None

        return {
            "status": "blocked_by_failure_budget",
            "flow_id": flow_id,
            "recent_failed_task_ids": [str(item["task_id"]) for item in recent_evidence],
            "recent_failed_nodes": failed_nodes,
            "recent_failure_kinds": [str(item["kind"]) for item in recent_evidence],
            "message": (
                "最近 3 次运行/质量审计均未证明流程可信，且失败节点、错误或业务质量问题高度相似。"
                "已阻止 AI 继续盲目 run_flow。请先执行诊断："
                "1) 对最新失败 task 调用 get_run_error 或 get_run_logs；"
                "2) 调用 get_flow + lint_flow 检查拓扑、等待、输出结构；"
                "3) 若涉及页面元素或筛选提交，必须调用 inspect_page 读取真实 DOM；"
                "4) 换诊断策略修复后再运行，禁止继续只改 selector、delayMs 或重复同类节点。"
            ),
        }

    async def _get_run_status(self, task_id: str) -> dict[str, Any]:
        task = await self._task_manager.get_task(task_id)
        if task is None:
            return {"error": f"任务 {task_id} 不存在"}
        return {
            "task_id": task_id,
            "status": task.status,
            "progress": task.progress.model_dump(mode="json"),
            "error": task.error,
        }

    async def _get_run_error(self, task_id: str) -> dict[str, Any]:
        task = await self._task_manager.get_task(task_id)
        if task is None:
            return {"error": f"任务 {task_id} 不存在"}

        all_logs = await self._task_manager.get_logs(task_id) or []
        error_logs = [entry for entry in all_logs if entry.level == "error"]
        warn_logs  = [entry for entry in all_logs if entry.level == "warn"]

        # 成功任务里的节点级错误来自 continueOnError 节点，是预期行为，不应算作 failed_node_id
        is_success = task.status == "success"

        failed_node_id: str | None = None
        if not is_success:
            for log in reversed(error_logs):
                if log.node_id:
                    failed_node_id = log.node_id
                    break

        nodes: list[Any] = []
        if task.flow_id:
            flow = await self._flow_service.get_flow(task.flow_id)
            if flow:
                nodes = flow.definition.get("nodes", [])

        failed_node_config: dict[str, Any] | None = None
        if failed_node_id:
            failed_node_config = next(
                (n for n in nodes if isinstance(n, dict) and n.get("id") == failed_node_id),
                None,
            )

        navigation_trace = build_navigation_trace(all_logs, nodes)

        error_text = task.error or ""
        error_lower = error_text.lower()
        is_selector_error = (
            ("timeout" in error_lower or "locator" in error_lower)
            and ("wait_for_selector" in error_lower or "locator(" in error_lower
                 or "page.fill" in error_lower or "page.click" in error_lower
                 or "page.press" in error_lower or "page.wait" in error_lower)
        )

        last_browser_url: str | None = None
        for log in reversed(all_logs):
            detail = log.detail or ""
            if detail.startswith("http://") or detail.startswith("https://"):
                last_browser_url = detail
                break

        # 成功运行返回精简结果，不带 error_logs，避免诱使 AI 去"修复"本就按预期工作的 continueOnError 节点
        if is_success:
            tolerated = [entry.node_id for entry in error_logs if entry.node_id]
            # 质量审计不合格的任务 task.status 仍是 success：照直说"无需修复"会和
            # failure budget 「先 get_run_error 再修」的指令正面矛盾，模型只能瞎猜
            quality_issues = self._recorded_quality_issues(task_id)
            result: dict[str, Any] = {
                "task_id": task_id,
                "status": "success",
                "run_error": None,
                "failed_node_id": None,
                "failed_node_config": None,
                "message": (
                    (
                        "流程执行本身没有报错，但这次运行的质量审计未通过："
                        f"{'、'.join(quality_issues)}。"
                        "根因在输出结构或抽取范围，不在节点报错，请据此修复后重跑。"
                        if quality_issues
                        else "流程整体运行成功，无需修复。"
                    )
                    + (
                        f"节点 {list(dict.fromkeys(tolerated))} 启用了 continueOnError，"
                        "其局部失败已被容忍——这是预期行为，请勿修改这些节点。"
                        if tolerated else ""
                    )
                ),
            }
            if quality_issues:
                result["quality_audit"] = {"passed": False, "issues": quality_issues}
            if last_browser_url:
                result["last_browser_url"] = last_browser_url
            # 成功运行也可能一路停在错误页面上（取到的是别的页的数据），照样给出证据
            verdict = build_navigation_verdict(navigation_trace)
            if verdict:
                result["navigation_trace"] = navigation_trace
                result["navigation_verdict"] = verdict
            return result

        result: dict[str, Any] = {
            "task_id": task_id,
            "status": task.status,
            "run_error": task.error,
            "failed_node_id": failed_node_id,
            "failed_node_config": failed_node_config,
            "error_logs": [
                {"message": entry.message, "detail": entry.detail, "node_id": entry.node_id}
                for entry in error_logs[-10:]
            ],
            "warn_logs": [
                {"message": entry.message, "node_id": entry.node_id}
                for entry in warn_logs[-5:]
            ],
        }
        if navigation_trace:
            result["navigation_trace"] = navigation_trace
        navigation_verdict = build_navigation_verdict(navigation_trace)
        if navigation_verdict:
            result["navigation_verdict"] = navigation_verdict

        root_cause_hints = _build_run_root_cause_hints(failed_node_id, all_logs, failed_node_config)
        if root_cause_hints:
            result["root_cause_hints"] = root_cause_hints

        swallowed_failures = _find_swallowed_critical_failures(all_logs, failed_node_id, task.flow_id)
        if swallowed_failures:
            result["swallowed_critical_failures"] = swallowed_failures
            result["root_cause_hints"] = [
                *result.get("root_cause_hints", []),
                (
                    "本次运行在最终失败前已有关键业务动作失败但继续执行。"
                    "优先修复这些前置动作或移除它们的 continueOnError，"
                    "不要只修改最后失败节点的 selector/timeout。"
                ),
            ]

        if is_selector_error:
            selector_diagnostic: dict[str, Any] | None = None
            count_match = re.search(r"页面匹配\s*(\d+)\s*个元素", error_text)
            selector_count = int(count_match.group(1)) if count_match else None
            element_not_visible = "element is not visible" in error_lower or "not visible" in error_lower
            if selector_count == 0:
                selector_diagnostic = {
                    "kind": SELECTOR_ZERO_MATCH,
                    "matched_count": 0,
                    "message": "selector 在当前页面没有命中任何元素，需要修正 selector 或检查页面导航是否正确。",
                    "repair_directions": [
                        "调用 inspect_page 获取真实 DOM，从返回的 inputs/buttons/links.selector 取精确 selector",
                        "检查流程拓扑：失败节点前是否缺少 browser.open（目标页 URL）",
                    ],
                }
            elif selector_count is not None and selector_count >= 1 and element_not_visible:
                # Element(s) found but not clickable due to non-display CSS hiding.
                # Changing the selector is almost never the right fix here.
                selector_diagnostic = {
                    "kind": SELECTOR_MATCH_NOT_VISIBLE,
                    "matched_count": selector_count,
                    "message": (
                        f"selector 命中了 {selector_count} 个元素，但元素对 Playwright 仍不可见/不可点击。"
                        "这不是 selector 错误——改 selector 无法解决此问题。"
                        "元素不可见的常见原因：CSS visibility:hidden / opacity:0 / 尺寸为零 / 被其他元素遮挡 / 尚未滚动到视口。"
                    ),
                    "repair_directions": [
                        "① 若该操作可选，直接对该节点设 continueOnError:true——"
                        "不要再改 selector，继续改只会浪费运行次数",
                        "② 若该操作必须执行且元素确实被 CSS 隐藏（visibility:hidden/opacity:0 而非 display:none）："
                        "对该 browser.click 节点添加 force:true，Playwright 会绕过可见性检查直接触发点击",
                        "③ 若不确定该操作是否可选，向用户询问：「这个步骤是必须完成的还是可以跳过？」",
                        "④ 检查节点执行时机：元素可能在前序操作完成后才变为可见，可在前一节点加 delayMs 等待",
                    ],
                }
            elif selector_count is not None and selector_count > 1:
                selector_diagnostic = {
                    "kind": SELECTOR_MULTI_MATCH_FIRST_NOT_ACTIONABLE,
                    "matched_count": selector_count,
                    "message": (
                        f"selector 命中了 {selector_count} 个元素，Playwright 尝试第一个但其不可操作。"
                        "需要缩小 selector 精确度或过滤到真正可交互的元素。"
                    ),
                    "repair_directions": [
                        "调用 inspect_page(scope_selector=失败节点父容器) 查看 DOM 结构，找到可操作的精确元素",
                        "若操作可选，设 continueOnError:true",
                    ],
                }
            elif element_not_visible:
                selector_diagnostic = {
                    "kind": SELECTOR_MATCH_HIDDEN_OR_NOT_VISIBLE,
                    "matched_count": None,
                    "message": (
                        "selector 命中的元素不可见（Playwright 未能从错误信息中解析出匹配数量）。"
                        "若该操作可选，设 continueOnError:true；否则调用 inspect_page 查看元素状态。"
                    ),
                }
            if selector_diagnostic is not None:
                result["selector_diagnostic"] = selector_diagnostic

            url_part = f"，建议 URL：{last_browser_url}" if last_browser_url else ""
            result["inspect_hint"] = (
                "⚠️ 这是 selector 定位超时。"
                + (
                    "元素已找到但不可见——不要再改 selector，"
                    "正确方向见 selector_diagnostic.repair_directions。"
                    if (selector_count is not None and selector_count >= 1 and element_not_visible)
                    else
                    f"修复前必须先调用 inspect_page(url='<当前页 URL>'{url_part}) 检查真实 DOM——"
                    "直接猜测修改 selector 后重新运行大概率仍会失败。"
                )
                + "截图节点对非视觉模型无效，不要用截图取证。"
            )
            if last_browser_url:
                result["last_browser_url"] = last_browser_url
        if "document is not defined" in error_lower or "window is not defined" in error_lower:
            result["script_environment_hint"] = (
                "脚本节点运行在本地 Node/Python/Shell 环境，不在浏览器页面上下文。"
                "不要在 script.javascript 中使用 document/window/localStorage。"
                "请删除该脚本节点，改用 browser.fill/browser.click/browser.extract 等浏览器节点，"
                "或先实现专门的 browser.evaluate 节点后再使用页面内 JS。"
            )

        self._attach_failure_evidence(result, task, failed_node_id)

        return result

    def _attach_failure_evidence(
        self,
        result: dict[str, Any],
        task: Any,
        failed_node_id: str | None,
    ) -> None:
        """把运行器留存的失败瞬间截图作为视觉证据附到 get_run_error 结果。

        运行器在浏览器节点最终失败时会保存 failure_evidence 截图 artifact。
        这里读回 image_base64，orchestrator 会剥离它并以 vision block 注入，
        让模型直接看到失败那一刻的页面（弹窗/验证码/空白页一目了然）。
        """
        try:
            evidence = None
            for artifact in reversed(getattr(task, "artifacts", []) or []):
                metadata = getattr(artifact, "metadata", None) or {}
                if not metadata.get("failure_evidence"):
                    continue
                if failed_node_id and metadata.get("node_id") not in (None, failed_node_id):
                    continue
                evidence = artifact
                break
            if evidence is None:
                return

            storage_url = getattr(evidence, "storage_url", "") or ""
            if not storage_url.startswith("file://"):
                return
            from urllib.parse import unquote, urlparse

            path = Path(unquote(urlparse(storage_url).path))
            if not path.is_file() or path.stat().st_size > 400_000:
                return
            metadata = getattr(evidence, "metadata", None) or {}
            result["image_base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
            result["image_media_type"] = "image/jpeg"
            result["failure_screenshot_note"] = (
                "已附上节点失败瞬间的页面截图（失败现场证据）。"
                f"失败时页面: {metadata.get('page_url') or '未知'}。"
                "先根据截图判断真实原因（验证码/弹窗遮挡/页面未加载/导航错误），再决定修复方向。"
            )
        except Exception:
            pass

    async def _apply_node_fix(
        self,
        flow_id: str,
        node_id: str,
        config_patch: dict[str, Any],
    ) -> dict[str, Any]:
        from app.models.schemas import FlowUpdateRequest

        # 同一 patch 在本轮会话中已应用过则跳过写入，硬性拦截模型忽略"不要重复"提示后死循环
        patch_key = hashlib.sha256(
            json.dumps({"flow": flow_id, "node": node_id, "patch": config_patch}, sort_keys=True).encode()
        ).hexdigest()
        if patch_key in self._applied_patch_hashes:
            return {
                "status": "duplicate_patch",
                "flow_id": flow_id,
                "node_id": node_id,
                "duplicate_patch": config_patch,
                "warning": (
                    "⚠️ 此 patch 与本轮对话中之前的操作完全相同，跳过写入。"
                    "重复同一修复说明根因未解决。必须切换策略："
                    "1) 调用 inspect_page 确认浏览器当前真实页面和 URL；"
                    "2) 检查流程中是否缺少导航节点（browser.open 到目标页面）；"
                    "3) 若页面 spa_loading:true 或 page_layout:[]，先修复前置导航/等待节点，而非当前节点的 selector。"
                ),
            }
        self._applied_patch_hashes.add(patch_key)

        flow = await self._flow_service.get_flow(flow_id)
        if flow is None:
            return {"error": f"流程 {flow_id} 不存在"}

        definition = copy.deepcopy(dict(flow.definition))
        nodes: list[Any] = list(definition.get("nodes", []))
        patched = False
        patched_node_ref: dict[str, str] | None = None
        for node in nodes:
            if isinstance(node, dict) and node.get("id") == node_id:
                for k, v in config_patch.items():
                    if v is None:
                        node.pop(k, None)  # null → 删除该字段
                    else:
                        node[k] = v
                patched_node_ref = _node_ref(node)
                patched = True
                break

        if not patched:
            return {"error": f"节点 {node_id} 在流程 {flow_id} 中不存在"}

        definition["nodes"] = nodes
        req = FlowUpdateRequest(definition=definition)
        updated = await self._flow_service.update_flow(flow_id, req)

        # Re-validate after fix — include lint so navigation topology issues surface
        input_var_names = [iv.name for iv in flow.input_variables]
        remaining_issues = _validate_variable_refs(nodes, input_var_names)
        edges: list[Any] = list(definition.get("edges", []))
        lint_findings = _lint_flow(nodes, edges, input_variable_names=input_var_names)
        # Emit actual field values from patched node to let AI verify the fix landed
        patched_node_actual = next((n for n in nodes if isinstance(n, dict) and n.get("id") == node_id), None)
        patched_field_snapshot = (
            {k: patched_node_actual.get(k) for k in config_patch}
            if patched_node_actual else {}
        )

        result: dict[str, Any] = {
            "flow_id": flow_id,
            "node_id": node_id,
            "applied_patch": config_patch,
            "patched_field_snapshot": patched_field_snapshot,
            "verify_hint": "确认 patched_field_snapshot 中各字段值与修改意图一致，不一致则重新 apply_node_fix。",
            "status": "patched" if updated else "error",
            "remaining_issues": remaining_issues,
            "all_clear": len(remaining_issues) == 0 and not any(f["severity"] == "error" for f in lint_findings),
        }
        if patched_node_ref:
            result["node_ref"] = patched_node_ref
            result["node_title"] = patched_node_ref["title"]
            result["node_type"] = patched_node_ref["type"]
            result["node_label"] = patched_node_ref["label"]
        if lint_findings:
            result["lint_findings"] = lint_findings
            result["lint_warning"] = (
                f"修复后静态检查：{sum(1 for f in lint_findings if f['severity']=='error')} 个错误、"
                f"{sum(1 for f in lint_findings if f['severity']=='warn')} 个警告（见 lint_findings）。"
            )
        return result

    async def _publish_flow(self, flow_id: str) -> dict[str, Any]:
        result = await self._flow_service.set_flow_status(flow_id, "active")
        if result is None:
            return {"error": f"流程 {flow_id} 不存在"}
        return {"flow_id": flow_id, "status": result.status}

    async def _get_run_output(self, task_id: str) -> dict[str, Any]:
        task = await self._task_manager.get_task(task_id)
        if task is None:
            return {"error": f"任务 {task_id} 不存在"}

        if task.status == "running":
            return {"status": "running", "message": "任务仍在运行中，请等待完成后再查询输出。"}

        _SYSTEM_VARS = frozenset({"run_timestamp"})
        variables: dict[str, Any] = {}
        for snap in (task.variables or []):
            if snap.name not in _SYSTEM_VARS:
                val = snap.value
                if isinstance(val, str) and len(val) > 500:
                    val = val[:500] + "…（已截断）"
                variables[snap.name] = val

        artifacts = [
            {"filename": a.filename, "type": a.artifact_type}
            for a in (task.artifacts or [])
        ]

        if task.status == "success":
            summary = f"运行成功，共输出 {len(variables)} 个变量、{len(artifacts)} 个产物文件。"
        elif task.status == "error":
            summary = f"运行失败：{task.error or '未知错误'}。建议调用 get_run_error 获取详细诊断。"
        else:
            summary = f"任务状态：{task.status}。"

        return {
            "task_id": task_id,
            "status": task.status,
            "summary": summary,
            "variables": variables,
            "artifacts": artifacts,
        }

    async def _assert_run_output(
        self,
        task_id: str,
        requirement_text: str | None = None,
        min_rows: int | None = None,
        max_rows: int | None = None,
        date_field: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        enum_field: str | None = None,
        allowed_values: list[str] | None = None,
        content_match_confirmed: bool = False,
    ) -> dict[str, Any]:
        # 运行"成功"只代表流程没报错，不代表抓到的数据符合用户需求；本方法做
        # 业务层断言（行数/日期范围/枚举值等），失败结果会被记入 quality_failures
        # 供 _recent_failure_gate 识别"看似成功但业务不达标"的死循环。
        task = await self._task_manager.get_task(task_id)
        if task is None:
            return {"error": f"任务 {task_id} 不存在"}
        if task.status != "success":
            return {
                "task_id": task_id,
                "passed": False,
                "status": task.status,
                "issues": [{"issue": "task_not_success", "message": "任务尚未成功完成，不能做业务断言。"}],
            }

        flow = await self._flow_service.get_flow(task.flow_id) if task.flow_id else None
        lint_findings: list[dict[str, Any]] = []
        if flow is not None:
            nodes = flow.definition.get("nodes", [])
            edges = flow.definition.get("edges", [])
            iv_names = [iv.name for iv in flow.input_variables]
            lint_findings = _lint_flow(nodes, edges, input_variable_names=iv_names)

        inferred = _infer_constraints_from_requirement(requirement_text or "")
        if start_date is None:
            start_date = inferred.get("start_date")
        if end_date is None:
            end_date = inferred.get("end_date")
        if allowed_values is None:
            inferred_values = inferred.get("allowed_values")
            allowed_values = inferred_values if isinstance(inferred_values, list) else None

        variables = {snap.name: _parse_runtime_value(snap.value) for snap in (task.variables or [])}
        candidates = _find_table_candidates(variables)
        issues: list[dict[str, Any]] = []
        selected = candidates[0] if candidates else None
        quality_warnings = [
            finding for finding in lint_findings
            if finding.get("issue") in {
                "date_filter_missing_verification",
                "table_extract_without_table_mode",
                "table_extract_selector_targets_container",
                "table_extract_selector_not_table_like",
                "extract_selector_union_used_as_fallback",
                "table_extract_missing_count",
                "dropdown_escape_bound_to_unstable_input",
                "critical_action_continue_on_error",
                "fragile_text_menu_navigation",
                "excel_addrow_missing_row_data",
                "extract_no_mode",
            }
        ]
        for finding in quality_warnings:
            issues.append({
                "issue": f"flow_quality_{finding.get('issue')}",
                "node_id": finding.get("node_id"),
                "message": finding.get("message"),
                "fix": finding.get("fix"),
            })

        # 采集完整性与交付形态无关：文档和表格都可能只装着第一页，所以在分叉之前判
        if flow is not None:
            issues.extend(_find_incomplete_sweeps(flow.definition.get("nodes", []), variables))

        if selected is None:
            # 用户要的是一篇文档时，「没有表格」不是缺陷；按表格审只会逼助手
            # 凭空造一个 rows 变量来喂审计，真正要交付的那篇文档反而没人验
            document = (
                _find_document_output(variables)
                if _requirement_wants_document(requirement_text or "")
                else None
            )
            if document is not None:
                issues.extend(self._audit_document_output(
                    task, document, requirement_text or "", content_match_confirmed, variables
                ))
                blocking = [i for i in issues if i.get("severity") != "warning"]
                if flow is not None and blocking:
                    self._record_quality_failure(flow.flow_id, task_id, blocking)
                return {
                    "task_id": task_id,
                    "passed": not blocking,
                    "delivery_shape": "document",
                    "selected_variable": document["name"],
                    "issues": blocking,
                    "warnings": [i for i in issues if i.get("severity") == "warning"],
                    "lint_findings": lint_findings[:12],
                    "repair_plan": _build_quality_repair_plan(blocking),
                }
            issues.append({
                "issue": "no_table_like_output",
                "message": (
                    "未找到表格型输出变量。抓取流程应输出按行结构化的表格变量（list[dict]），"
                    "而不是只保存截图/文本/空产物。observed_variables 列出了本次实际产出的变量"
                    "以及各自不算表格的原因，请据此改抽取节点，不要换个写法重试同一个方案。"
                ),
            })
            if flow is not None:
                self._record_quality_failure(flow.flow_id, task_id, issues)
            return {
                "task_id": task_id,
                "passed": False,
                "issues": issues,
                "candidates": [],
                "observed_variables": _describe_output_variables(variables),
                "lint_findings": lint_findings[:12],
                "repair_plan": _build_quality_repair_plan(issues),
            }

        rows = selected["rows"]
        headers = selected.get("headers") or _find_header_variable(variables)
        row_count = len(rows)
        if min_rows is not None and row_count < min_rows:
            issues.append({"issue": "too_few_rows", "message": f"结果行数 {row_count} 小于最小期望 {min_rows}。"})
        if max_rows is not None and row_count > max_rows:
            issues.append({"issue": "too_many_rows", "message": f"结果行数 {row_count} 大于最大期望 {max_rows}。"})

        structure_issue = _check_structured_rows(rows, headers)
        if structure_issue is not None:
            issues.append(structure_issue)

        if date_field is None and (start_date or end_date):
            date_field = _guess_date_field(headers, rows)
        if enum_field is None and allowed_values:
            enum_field = _guess_enum_field(headers, rows, allowed_values)

        if date_field and (start_date or end_date):
            date_issues = _assert_date_range(rows, headers, date_field, start_date, end_date)
            issues.extend(date_issues)
        elif start_date or end_date:
            issues.append({
                "issue": "date_constraint_not_verifiable",
                "message": (
                    "需求中存在日期范围约束，但运行输出没有提供可自动定位的日期字段。"
                    "AI 必须检查表头/输出结构，确认日期字段是否被正确抽取并再做断言。"
                ),
                "inferred_start_date": start_date,
                "inferred_end_date": end_date,
            })

        # 结构与约束全过只证明抓到了「一张表」，不证明抓对了表
        alignment = _check_requirement_alignment(
            _extract_requirement_targets(requirement_text or ""), rows, headers
        )
        if alignment is not None and not alignment["aligned"] and not content_match_confirmed:
            issues.append({
                "issue": OUTPUT_CONTENT_MISMATCH,
                "message": (
                    f"需求指向 {alignment['targets']}，但输出的表头和数据里找不到任何一个。"
                    "结构校验只能证明抓到了「一张表」，不能证明抓对了表。"
                ),
                "fix": (
                    "把 sample_rows 与用户需求逐条比对：确实是用户要的数据，就重新调用本工具并传 "
                    "content_match_confirmed=true 明确确认；不是，则修复抽取节点的 selector 后重跑。"
                    "禁止在未做这一步判断时向用户汇报成功。"
                ),
                "requirement_targets": alignment["targets"],
            })

        if enum_field and allowed_values:
            enum_issues = _assert_allowed_values(rows, headers, enum_field, allowed_values)
            issues.extend(enum_issues)
        elif allowed_values:
            issues.append({
                "issue": "enum_constraint_not_verifiable",
                "message": (
                    "需求中存在枚举/状态类约束，但运行输出没有提供可自动定位的枚举字段。"
                    "AI 必须检查表头/输出结构，确认对应字段是否被正确抽取并再做断言。"
                ),
                "allowed_values": allowed_values,
            })

        # severity=warning 的检查项只提示不拦：零星噪声推翻整次抓取，只会让助手
        # 反复重改一个本来能交付的流程，用户看到的就是「一直在修」
        warnings = [issue for issue in issues if issue.get("severity") == "warning"]
        blocking = [issue for issue in issues if issue.get("severity") != "warning"]
        passed = not blocking
        if flow is not None and not passed:
            self._record_quality_failure(flow.flow_id, task_id, blocking)

        return {
            "task_id": task_id,
            "passed": passed,
            "warnings": warnings,
            "selected_variable": selected["name"],
            "row_count": row_count,
            "headers": headers,
            "inferred_constraints": inferred,
            "resolved_constraints": {
                "date_field": date_field,
                "start_date": start_date,
                "end_date": end_date,
                "enum_field": enum_field,
                "allowed_values": allowed_values,
            },
            "issues": blocking,
            "repair_plan": _build_quality_repair_plan(blocking),
            "lint_findings": lint_findings[:12],
            "sample_rows": rows[:3],
            "requirement_alignment": alignment,
            "content_match_confirmed": content_match_confirmed,
            "message": (
                (
                    _alignment_pass_message(alignment, content_match_confirmed)
                    # 提示项照常交付，但要在汇报里写明，别让用户以为结果是完全干净的
                    + ("" if not warnings else f" 另有 {len(warnings)} 条提示未构成不合格，请在汇报中如实说明。")
                )
                if passed
                else "运行质量审计失败，必须继续诊断并修复流程。"
            ),
        }

    def _audit_document_output(
        self,
        task: Any,
        document: dict[str, Any],
        requirement_text: str,
        content_match_confirmed: bool,
        variables: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """文档型交付物的审计：真落盘了、有正文、内容对得上需求。

        表格审计那套（行数/日期范围/枚举）对一篇 Markdown 全无意义，套上去只会
        让每次调用都返回同一条不可执行的结论。
        """
        issues: list[dict[str, Any]] = []
        body = document["value"]
        if document["kind"] == "file":
            path = Path(document["value"])
            if not path.is_absolute():
                path = storage.resolve_workspace_root() / path
            if not path.is_file():
                return [{
                    "issue": "document_file_missing",
                    "message": f"变量 `{document['name']}` 指向的产物文件不存在：{document['value']}。",
                    "fix": "检查写文件节点的路径与 outputVariable，确认文件真的落盘后再重跑。",
                }]
            if path.suffix.lower() in _BINARY_DOCUMENT_FORMATS:
                return _audit_binary_document(document, path)
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return [{
                    "issue": "document_file_unreadable",
                    "message": f"产物文件读取失败：{exc}",
                }]

        if len(body.strip()) < _MIN_DOCUMENT_CHARS:
            issues.append({
                "issue": "document_too_short",
                "message": (
                    f"文档只有 {len(body.strip())} 个字符，达不到一篇可交付内容的最低量。"
                    "多半是抽取节点只取到标题或首条，正文没进来。"
                ),
                "fix": "用 inspect_page 确认正文容器 selector，确保抽取的是整篇内容而不是单个元素。",
            })

        provenance = _audit_document_provenance(document, body, variables)
        if provenance is not None:
            issues.append(provenance)

        targets = _extract_requirement_targets(requirement_text)
        # rows 传单元素列表：对齐检查对非 dict/list 行按整段文本比对
        alignment = _check_requirement_alignment(targets, [body[:20_000]], None)
        if alignment is not None and not alignment["aligned"] and not content_match_confirmed:
            issues.append({
                "issue": DOCUMENT_CONTENT_MISMATCH,
                "message": (
                    f"需求指向 {alignment['targets']}，但文档正文里找不到任何一个。"
                    "文档非空只能证明写出了东西，不能证明写对了内容。"
                ),
                "fix": (
                    "读一遍文档开头与用户需求逐条比对：确实是用户要的内容，就重新调用本工具并传 "
                    "content_match_confirmed=true；不是，则修复抽取节点后重跑。"
                ),
            })
        return issues

    def _recorded_quality_issues(self, task_id: str) -> list[str]:
        """这个 task 是否有记录在案的质量审计失败（按 issue 名）。"""
        for records in self._quality_failures_by_flow.values():
            for record in records:
                if record.get("task_id") == task_id:
                    return [str(name) for name in record.get("issues") or []]
        return []

    def _record_quality_failure(self, flow_id: str, task_id: str, issues: list[dict[str, Any]]) -> None:
        issue_names = [
            str(issue.get("issue"))
            for issue in issues
            if issue.get("issue")
        ]
        records = self._quality_failures_by_flow.setdefault(flow_id, [])
        records.insert(0, {
            "task_id": task_id,
            "issues": issue_names,
            "created_at": datetime.now(UTC),
        })
        del records[8:]

    async def _get_run_logs(
        self,
        task_id: str,
        node_id: str | None = None,
        level: str | None = None,
    ) -> dict[str, Any]:
        logs = await self._task_manager.get_logs(task_id)
        if logs is None:
            return {"error": f"任务 {task_id} 不存在"}
        if node_id:
            logs = [entry for entry in logs if entry.node_id == node_id]
        if level:
            logs = [entry for entry in logs if entry.level == level]
        return {
            "task_id": task_id,
            "count": len(logs),
            "logs": [
                {"level": entry.level, "message": entry.message, "detail": entry.detail, "node_id": entry.node_id}
                for entry in logs[-50:]
            ],
        }

    async def _inspect_page(
        self,
        url: str,
        wait_selector: str | None = None,
        scope_selector: str | None = None,
    ) -> dict[str, Any]:
        """Navigate to a URL with the persistent browser profile and return structured DOM info."""
        busy = _profile_busy_block("inspect_page")
        if busy is not None:
            return busy

        if find_spec("playwright") is None:
            return {"error": "未安装 Playwright，请执行 uv pip install playwright"}

        browser_profile = str(storage.resolve_browser_profile_dir())
        # 自己也要登记：检查页面期间用户点了运行，那次运行才能拿到「被 inspect_page 占用」而不是天书
        owner = "AI 助手 · inspect_page"
        try:
            browser_profile_lock.acquire(browser_profile, owner)
        except browser_profile_lock.BrowserProfileBusyError:
            return _profile_busy_block("inspect_page") or {"error": "浏览器被占用"}

        try:
            async with persistent_browser_context(browser_profile, headless=True) as ctx:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto(url, wait_until="load", timeout=30_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=6_000)
                except Exception:
                    pass

                if wait_selector:
                    try:
                        await page.wait_for_selector(wait_selector, timeout=12_000)
                    except Exception:
                        pass  # best-effort; still extract what's there
                else:
                    await page.wait_for_timeout(3_000)

                # 拦截页要在探测结果之前判掉：它的元素数量通常是 0，落到下面就会被当成
                # 「SPA 没渲染完」，模型于是带着 wait_selector 一遍遍重试同一堵墙——
                # 用户看到的「第一次助手被 cloudflare 拦截、什么也没生成」就是这么来的。
                challenge = await detect_blocking_interstitial(page)
                if challenge is not None:
                    # 这里只陈述事实：「不要改流程、不要重试」交给 challenge_page_lock 护栏，
                    # 同一条规则写两处必然漂移，而只有护栏那份真拦得住。
                    return {
                        "status": "blocked_challenge_page",
                        "requested_url": url,
                        "challenge_label": challenge.label,
                        "challenge": challenge.summary,
                        "error": f"目标站点返回了{challenge.label}，无头浏览器拿不到真实页面内容。",
                    }

                result = await page.evaluate(PAGE_PROBE_JS, scope_selector)
                result["scope_selector"] = scope_selector
                result["requested_url"] = url
                result["note"] = (
                    "selector 字段为推荐选择器，可直接用于 browser.click / browser.fill 等节点。"
                    ":has-text() 为 Playwright 伪选择器，合法可用。"
                    "若 date_controls 字段存在，按 interaction_recipe.steps 构建节点（selector 直接用，"
                    "日期文本/目标年月/节点数量按本次任务改写）；主路线走不通时才看 fallback_steps，"
                    "notes 里是该框架与执行器的已知限制。"
                )

                # 必须在 total_elements 判空之前检测加载态类名，否则只有 logo 的页面
                # （total_elements=1）会漏报 SPA 仍在渲染中
                # page_classes 是给模型看的前 120 个；all_classes 是未截断的全量集合。
                # 加载态指示类名一般挂在靠前的容器上，模糊匹配（loading/skeleton 子串）
                # 在全量集合上误报率高，所以只有精确指示类名和组件识别用全量。
                page_classes: list[str] = result.get("page_classes", [])
                all_classes: list[str] = result.pop("all_classes", None) or page_classes
                spa_loading = (
                    "nprogress-busy" in all_classes
                    or any(
                        cls in all_classes
                        for cls in ("v-loading", "el-loading-mask", "ant-spin-spinning", "arco-spin")
                    )
                    or any(
                        "loading" in cls or "skeleton" in cls
                        for cls in page_classes
                        if cls not in ("el-loading-fade-enter", "el-loading-fade-leave")
                    )
                )
                # 识别已知组件库控件并注入 interaction_recipe，模型无需猜 selector
                try:
                    from app.services.skills.registry import match_skills as _match_skills
                    from app.services.skills.registry import build_skill_recipe as _build_skill_recipe
                    _matched = _match_skills(all_classes)
                    _controls = [
                        {
                            "type": f"{s.library}/{s.component}",
                            "library": s.library,
                            "component": s.component,
                            "description": s.description,
                            "interaction_recipe": _build_skill_recipe(s, result.get("inputs", [])),
                        }
                        for s in _matched
                    ]
                    # 页面用的是没写过配方的组件库（Arco/Vant/iView/自研）时，指纹匹配一无所获，
                    # 模型就只能凭空猜交互方式。退回到与组件库无关的日期特征识别，
                    # 至少保证任何框架下都拿得到真实 selector + 通用交互路线。
                    if not any("date" in c["component"] for c in _controls):
                        from app.services.skills.generic import build_generic_date_recipe
                        _generic = build_generic_date_recipe(result.get("inputs", []))
                        if _generic:
                            _controls.append(_generic)
                    if _controls:
                        result["date_controls"] = _controls
                except Exception:
                    pass  # skill matching is best-effort; never break inspect_page

                if spa_loading:
                    result["spa_loading"] = True
                    result["warning"] = (
                        "⚠️ SPA 页面正在加载（检测到 nprogress-busy 或加载指示器类名）。"
                        "页面内容尚未渲染，当前返回的元素列表不可靠。\n"
                        "必须执行以下诊断（按顺序，不可跳过）：\n"
                        "1. 检查流程拓扑：列出所有 browser.open 节点的 URL，确认是否有导航节点跳转到目标页面\n"
                        "2. 若只有一个 browser.open（登录页），先添加第二个 browser.open（目标页，delayMs:3000）再重试\n"
                        "3. 若导航节点存在，增加其 delayMs 到 3000-5000ms 等待 SPA 渲染\n"
                        "4. 修复前置节点后，再重新调用 inspect_page 获取真实 DOM\n"
                        "禁止在 spa_loading:true 时对 browser.wait/browser.extract 节点写 selector。"
                    )
                else:
                    result["spa_loading"] = False

                total_elements = (
                    len(result.get("inputs", []))
                    + len(result.get("buttons", []))
                    + len(result.get("links", []))
                    + len(result.get("tables", []))
                )
                if total_elements == 0 and not spa_loading:
                    result["warning"] = (
                        "⚠️ 页面元素为空——SPA 可能未渲染完毕。"
                        "请重新调用 inspect_page，并指定 wait_selector 参数等待页面核心元素出现，"
                        "例如 wait_selector='nav, table, [role=grid], [role=navigation], main'。"
                        "如果多次重试仍为空，请检查 url 是否正确、是否需要重新登录。"
                    )

                # 子 frame 对主文档抽取不可见，需单独统计并告知 AI
                try:
                    child_frames = [
                        fr for fr in page.frames
                        if fr is not page.main_frame
                        and fr.url and fr.url != "about:blank"
                    ][:3]
                    if child_frames:
                        frames_info: list[dict[str, Any]] = []
                        for fr in child_frames:
                            try:
                                census = await fr.evaluate(
                                    "() => ({"
                                    " inputs: document.querySelectorAll('input:not([type=hidden]), textarea').length,"
                                    " buttons: document.querySelectorAll('button, [role=button]').length,"
                                    " tables: document.querySelectorAll('table, [role=grid], [role=table]').length,"
                                    " title: document.title })"
                                )
                            except Exception:
                                census = {}
                            frames_info.append({"url": fr.url, "name": fr.name or None, **(census or {})})
                        result["frames"] = frames_info
                        result["frames_note"] = (
                            "⚠️ 页面包含 iframe。主文档提取结果不含 iframe 内部元素；"
                            "若目标元素（表单/表格）在 iframe 内，selector 使用穿透语法："
                            "`iframe选择器 >>> 内部选择器`（如 `iframe[name=\"main\"] >>> tbody tr`，"
                            "iframe 选择器按 frames[].name/url 构造，可多层链式）。"
                        )
                except Exception:
                    pass  # frame census is best-effort

                # 放在最后：登录重定向比 spa_loading / 空元素更能解释异常，warning 以它为准
                _annotate_login_redirect(result, url)
                return result
        except Exception as exc:
            translated = browser_profile_lock.translate_launch_error(browser_profile, exc)
            return {"error": translated or f"页面检查失败：{exc}"}
        finally:
            browser_profile_lock.release(browser_profile, owner)

    async def _inspect_screenshot(
        self,
        url: str,
        wait_selector: str | None = None,
        full_page: bool = False,
    ) -> dict[str, Any]:
        """Navigate with the persistent profile and return a JPEG screenshot.

        The orchestrator strips `image_base64` from the tool message and
        re-injects it as a vision content block, so the model actually *sees*
        the page instead of reading base64 noise.
        """
        busy = _profile_busy_block("inspect_screenshot")
        if busy is not None:
            return busy

        if find_spec("playwright") is None:
            return {"error": "未安装 Playwright，请执行 uv pip install playwright"}

        import base64

        browser_profile = str(storage.resolve_browser_profile_dir())
        owner = "AI 助手 · inspect_screenshot"
        try:
            browser_profile_lock.acquire(browser_profile, owner)
        except browser_profile_lock.BrowserProfileBusyError:
            return _profile_busy_block("inspect_screenshot") or {"error": "浏览器被占用"}

        try:
            async with persistent_browser_context(browser_profile, headless=True) as ctx:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                # 视口设在页面上而不是上下文上：隐身会话不接受上下文级 viewport，
                # 传进去会被静默忽略，截出来的是默认尺寸
                await page.set_viewport_size({"width": 1280, "height": 800})
                await page.goto(url, wait_until="load", timeout=30_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=6_000)
                except Exception:
                    pass
                if wait_selector:
                    try:
                        await page.wait_for_selector(wait_selector, timeout=12_000)
                    except Exception:
                        pass
                else:
                    await page.wait_for_timeout(2_000)

                raw = await page.screenshot(
                    type="jpeg", quality=60, full_page=bool(full_page)
                )

                saved_path: str | None = None
                try:
                    shots_dir = storage.resolve_cache_dir() / "inspect_shots"
                    shots_dir.mkdir(parents=True, exist_ok=True)
                    from datetime import datetime as _dt
                    fname = f"shot_{_dt.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                    (shots_dir / fname).write_bytes(raw)
                    saved_path = str(shots_dir / fname)

                    shots = sorted(shots_dir.glob("shot_*.jpg"))
                    for old in shots[:-20]:
                        old.unlink(missing_ok=True)
                except Exception:
                    pass

                return {
                    "url": page.url,
                    "title": await page.title(),
                    "image_base64": base64.b64encode(raw).decode("ascii"),
                    "image_media_type": "image/jpeg",
                    "image_saved_to": saved_path,
                    "note": "截图已作为图片提供给模型查看。",
                }
        except Exception as exc:
            translated = browser_profile_lock.translate_launch_error(browser_profile, exc)
            return {"error": translated or f"页面截图失败：{exc}"}
        finally:
            browser_profile_lock.release(browser_profile, owner)
