"""RPA tool definitions and executor for AI orchestration."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.core import storage

if TYPE_CHECKING:
    from app.services.flow_service import FlowService
    from app.services.task_manager import TaskManager

# ─── Variable reference helpers ───────────────────────────────────────────────

_VAR_REF_RE = re.compile(r'\$\{var\.([^}]+)\}')

# Fields that DEFINE (output) a new variable
_OUTPUT_FIELDS = ("outputVariable", "countVariable", "itemVariable", "errorVariable")
_VARIABLE_NAME_FIELDS = frozenset({
    "variableName",
    "name",
    "outputVariable",
    "responseVariable",
    "resultVariable",
    "saveAs",
    "countVariable",
    "firstValueVariable",
    "appendVariable",
    "appendOutputVariable",
    "itemsVariable",
    "itemVariable",
    "indexVariable",
    "inputVariable",
    "jsonVariable",
    "statusVariable",
    "errorVariable",
})
_CONDITION_EXPRESSION_FIELDS = ("condition", "expression", "inputValue")

# Runtime-injected builtins — always considered defined (injected by the executor before each run)
_RUNTIME_BUILTINS = frozenset(["run_timestamp", "flow_slug", "output_dir", "output_prefix"])


def _collect_defined_vars(nodes: list[Any], input_variable_names: list[str]) -> set[str]:
    """Return the set of all variable names that are defined by this flow."""
    defined: set[str] = set(_RUNTIME_BUILTINS)
    defined.update(input_variable_names)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for field in _OUTPUT_FIELDS:
            val = node.get(field)
            if isinstance(val, str) and val.strip():
                defined.add(val.strip())
        # variable.set and variable.input both define variableName
        if node.get("type") in ("variable.set", "variable.input"):
            vname = node.get("variableName")
            if isinstance(vname, str) and vname.strip():
                defined.add(vname.strip())
        # script.python / script.javascript may output via outputVariables list
        for item in node.get("outputVariables") or []:
            if isinstance(item, str) and item.strip():
                defined.add(item.strip())
    return defined


def _collect_refs_in_node(node: dict[str, Any]) -> set[str]:
    """Return all ${var.xxx} names referenced anywhere inside a node, recursively."""
    refs: set[str] = set()

    def _walk(obj: Any) -> None:
        if isinstance(obj, str):
            for m in _VAR_REF_RE.finditer(obj):
                refs.add(m.group(1))
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    for val in node.values():
        _walk(val)
    return refs


def _template_refs(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [match.group(1) for match in _VAR_REF_RE.finditer(value)]


def _collect_output_vars_in_node(node: dict[str, Any]) -> set[str]:
    """返回节点可能写入的变量名，用于判断容错节点失败后是否会留下未定义变量。"""
    vars_: set[str] = set()
    for field in _OUTPUT_FIELDS:
        val = node.get(field)
        if isinstance(val, str) and val.strip():
            vars_.add(val.strip())
    for item in node.get("outputVariables") or []:
        if isinstance(item, str) and item.strip():
            vars_.add(item.strip())
    return vars_


def _unreachable_node_ids(nodes: list[Any], edges: list[Any]) -> list[str]:
    """Return node ids unreachable from the flow entry node (sorted).

    Entry is the node with id/type "start", else the first node — matching the
    executor's _select_run_start_node_id. Nodes the executor can never reach are
    dead weight, almost always the symptom of a missing or wrongly-removed edge.
    """
    node_ids = [n.get("id") for n in nodes if isinstance(n, dict) and n.get("id")]
    if not node_ids:
        return []
    entry = next(
        (n.get("id") for n in nodes
         if isinstance(n, dict) and (n.get("id") == "start" or n.get("type") == "start")),
        node_ids[0],
    )
    adjacency: dict[str, list[str]] = {}
    for e in edges:
        if isinstance(e, dict) and e.get("source") and e.get("target"):
            adjacency.setdefault(e["source"], []).append(e["target"])
    reachable: set[str] = set()
    stack = [entry]
    while stack:
        cur = stack.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        stack.extend(t for t in adjacency.get(cur, []) if t not in reachable)
    return sorted(nid for nid in node_ids if nid not in reachable)


def _validate_variable_refs(
    nodes: list[Any],
    input_variable_names: list[str],
) -> list[dict[str, Any]]:
    """Return a list of issues for nodes that reference undefined variables."""
    defined = _collect_defined_vars(nodes, input_variable_names)
    issues = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("type", "")
        if node_type in ("start", "end"):
            continue
        refs = _collect_refs_in_node(node)
        undefined = refs - defined
        if undefined:
            issues.append({
                "node_id": node.get("id", "?"),
                "node_title": node.get("title", node.get("id", "?")),
                "node_type": node_type,
                "undefined_variables": sorted(undefined),
            })
    return issues


# ─── Credential-like variable name keywords ───────────────────────────────────
_CREDENTIAL_KEYWORDS = frozenset({
    "password", "passwd", "pwd", "username", "account", "user",
    "login", "secret", "token", "apikey", "api_key",
})


def _lint_flow(
    nodes: list[Any],
    edges: list[Any],
    input_variable_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Programmatic quality checks — model-agnostic, no prompt memory required.

    Returns a list of findings, each with:
      severity: "error" | "warn"
      node_id, node_title, issue (machine tag), message, fix
    """
    findings: list[dict[str, Any]] = []

    node_map: dict[str, dict] = {
        n["id"]: n for n in nodes if isinstance(n, dict) and n.get("id")
    }

    # Build per-node outgoing edges (with labels)
    out_edges: dict[str, list[dict]] = {}
    for e in edges:
        if isinstance(e, dict) and e.get("source"):
            out_edges.setdefault(e["source"], []).append(e)

    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id", "?")
        ntype = node.get("type", "")
        ntitle = node.get("title", nid)

        if ntype in ("start", "end"):
            continue

        # ── 1. foreach must have body + exit labeled edges ────────────────────
        if ntype == "control.foreach":
            labels = {e.get("label", "") for e in out_edges.get(nid, [])}
            if "body" not in labels:
                findings.append({
                    "severity": "error", "node_id": nid, "node_title": ntitle,
                    "issue": "foreach_missing_body_edge",
                    "message": f"foreach 节点 `{nid}` 缺少 label='body' 的循环体出边，循环体永远不会执行。",
                    "fix": "用 update_flow add_edges 添加 source=该节点、target=循环体首节点、label='body' 的边。",
                })
            if "exit" not in labels:
                findings.append({
                    "severity": "error", "node_id": nid, "node_title": ntitle,
                    "issue": "foreach_missing_exit_edge",
                    "message": f"foreach 节点 `{nid}` 缺少 label='exit' 的循环后出边，迭代完成后流程无法继续。",
                    "fix": "用 update_flow add_edges 添加 source=该节点、target=循环后首节点、label='exit' 的边。",
                })

        # ── 2. condition must have true + false labeled edges ─────────────────
        if ntype == "control.condition":
            labels = {e.get("label", "") for e in out_edges.get(nid, [])}
            for branch in ("true", "false"):
                if branch not in labels:
                    findings.append({
                        "severity": "error", "node_id": nid, "node_title": ntitle,
                        "issue": f"condition_missing_{branch}_branch",
                        "message": f"条件节点 `{nid}` 缺少 label='{branch}' 的分支出边，该分支永远不会执行。",
                        "fix": f"添加一条 label='{branch}' 的出边，指向条件{'成立' if branch == 'true' else '不成立'}时的下一个节点。",
                    })

        # ── 2b. foreach ambiguous outgoing edges ─────────────────────────────
        if ntype == "control.foreach":
            outgoing = out_edges.get(nid, [])
            labels = [str(e.get("label", "")).strip().lower() for e in outgoing]
            unlabeled_count = sum(1 for label in labels if not label)
            if len(outgoing) >= 2 and unlabeled_count:
                findings.append({
                    "severity": "error", "node_id": nid, "node_title": ntitle,
                    "issue": "foreach_ambiguous_unlabeled_edges",
                    "message": (
                        f"foreach 节点 `{nid}` 有 {len(outgoing)} 条出边，但至少一条缺少 label。"
                        "不同端（前端校验、后端执行、AI 修复）可能对未标注出边的循环体/退出分支理解不一致，"
                        "会造成循环迭代了但写入节点未执行。"
                    ),
                    "fix": (
                        "显式标注两条出边：循环体首节点 label='body'，循环完成后节点 label='exit'。"
                        "循环体内部节点用普通边串联，不要把保存/结束节点误接成第二条未标注出边。"
                    ),
                })

        # ── 3. browser.extract must have outputVariable or countVariable ───────
        if ntype == "browser.extract":
            if not node.get("outputVariable") and not node.get("countVariable"):
                findings.append({
                    "severity": "error", "node_id": nid, "node_title": ntitle,
                    "issue": "extract_no_output",
                    "message": f"browser.extract 节点 `{nid}` 未设置 outputVariable，提取结果丢失。",
                    "fix": "用 apply_node_fix 添加 outputVariable（如 'extracted_data'）。",
                })
            if not node.get("extractMode"):
                findings.append({
                    "severity": "warn", "node_id": nid, "node_title": ntitle,
                    "issue": "extract_no_mode",
                    "message": f"browser.extract 节点 `{nid}` 未设置 extractMode，行为依赖默认值，建议显式指定。",
                    "fix": "设置 extractMode 为 text / html / attribute / count / table 之一。",
                })

        # ── 4. http.request without outputVariable ────────────────────────────
        if ntype == "http.request" and not node.get("outputVariable"):
            findings.append({
                "severity": "warn", "node_id": nid, "node_title": ntitle,
                "issue": "http_no_output",
                "message": f"http.request 节点 `{nid}` 未设置 outputVariable，HTTP 响应无法被后续节点引用。",
                "fix": "用 apply_node_fix 添加 outputVariable 字段（如 'api_response'）。",
            })

        # ── 5. variable.input misused for credentials ─────────────────────────
        if ntype == "variable.input":
            vname = (node.get("variableName") or "").lower()
            if any(kw in vname for kw in _CREDENTIAL_KEYWORDS):
                findings.append({
                    "severity": "error", "node_id": nid, "node_title": ntitle,
                    "issue": "credential_in_variable_input",
                    "message": (
                        f"节点 `{nid}` 用 variable.input 收集凭据字段 '{node.get('variableName')}'，"
                        "每次运行都会暂停等待手动输入，破坏自动化。"
                    ),
                    "fix": (
                        "删除此节点，改在流程 input_variables 中声明"
                        "（category='credential'，密码加 sensitive=true），"
                        "节点中用 ${var.xxx} 直接引用。"
                    ),
                })

        # ── 6. output file path without timestamp ─────────────────────────────
        if ntype in ("file.write", "excel.save", "excel.addrow"):
            path = node.get("path") or ""
            if not path and any(node.get(key) for key in ("filePath", "targetPath", "targetUrl")):
                findings.append({
                    "severity": "warn", "node_id": nid, "node_title": ntitle,
                    "issue": "noncanonical_path_field",
                    "message": f"节点 `{nid}` 使用了 filePath/targetPath/targetUrl 作为文件路径兼容字段，前端校验与属性面板规范字段是 path。",
                    "fix": "把路径写入 path 字段；兼容字段可保留但不要作为主字段。",
                })
                path = node.get("filePath") or node.get("targetPath") or node.get("targetUrl") or ""
            if path and isinstance(path, str):
                has_ts = any(kw in path for kw in (
                    "${var.output_prefix}", "${var.run_timestamp}", "${var.output_dir}",
                ))
                if not has_ts:
                    findings.append({
                        "severity": "warn", "node_id": nid, "node_title": ntitle,
                        "issue": "hardcoded_output_path",
                        "message": f"节点 `{nid}` 输出路径 '{path}' 不含时间戳，每次运行会覆盖上次结果。",
                        "fix": "将 path 改为 '${var.output_prefix}.json'（或 .xlsx）。",
                    })

        if ntype == "excel.addrow" and not any(node.get(key) is not None for key in ("rowData", "row", "content")):
            findings.append({
                "severity": "error", "node_id": nid, "node_title": ntitle,
                "issue": "excel_addrow_missing_row_data",
                "message": f"excel.addrow 节点 `{nid}` 缺少 rowData，运行时会追加空行或没有实际数据。",
                "fix": "设置 rowData，例如 rowData='${var.current_row}' 或 rowData=[...]；循环内通常使用当前项变量。",
            })

        # ── 7. browser.* nodes missing selector ───────────────────────────────
        _NEED_SELECTOR = {
            "browser.click", "browser.fill", "browser.wait",
            "browser.extract", "browser.press", "browser.select",
            "browser.check", "browser.drag", "browser.hover",
        }
        if ntype in _NEED_SELECTOR and not node.get("selector"):
            findings.append({
                "severity": "error", "node_id": nid, "node_title": ntitle,
                "issue": "missing_selector",
                "message": f"节点 `{nid}`（{ntype}）缺少 selector 字段，运行时会报错。",
                "fix": "用 apply_node_fix 或 update_flow 添加 selector 字段。",
            })

        findings.extend(_lint_variable_contract_for_node(node, nid=nid, ntitle=ntitle, ntype=ntype))

    # ── 8. Unreachable (orphan) nodes ─────────────────────────────────────────
    for nid in _unreachable_node_ids(nodes, edges):
        node = node_map.get(nid, {})
        ntype = node.get("type", "")
        if ntype in ("start", "end"):
            continue
        findings.append({
            "severity": "error", "node_id": nid, "node_title": node.get("title", nid),
            "issue": "unreachable_node",
            "message": f"节点 `{nid}` 无法从流程起点到达（孤儿节点），运行时会被跳过。",
            "fix": "检查是否漏连了入边，用 update_flow add_edges 补连。",
        })

    findings.extend(_lint_visual_layout(nodes))
    findings.extend(_lint_flow_semantic_quality(nodes))
    findings.extend(_lint_critical_continue_on_error(nodes, edges))
    findings.extend(_lint_continue_on_error_output_defaults(nodes, edges))

    # ── 9. Navigation topology: extraction without navigation ─────────────────
    # Detect "login flow with only one browser.open" — the #1 cause of blank-page
    # failures where the AI keeps patching selectors on a table-wait node while
    # the real problem is a missing browser.open to the data page after login.
    _EXTRACTION_TYPES = {"browser.extract", "browser.wait"}
    _LOGIN_INDICATORS = {"input[type='password']", "password", "用户名", "账号", "登录"}

    open_nodes = [n for n in nodes if isinstance(n, dict) and n.get("type") == "browser.open"]
    extraction_nodes = [n for n in nodes if isinstance(n, dict) and n.get("type") in _EXTRACTION_TYPES]
    login_fill_nodes = [
        n for n in nodes
        if isinstance(n, dict) and n.get("type") == "browser.fill"
        and any(kw in str(n.get("selector", "") + n.get("inputValue", "")).lower() for kw in _LOGIN_INDICATORS)
    ]

    if len(open_nodes) == 1 and extraction_nodes and login_fill_nodes:
        only_open = open_nodes[0]
        findings.append({
            "severity": "error",
            "node_id": only_open.get("id", "?"),
            "node_title": only_open.get("title", "browser.open"),
            "issue": "single_navigation_node",
            "message": (
                f"流程只有一个 browser.open 节点（{only_open.get('targetUrl') or only_open.get('url','?')}），"
                "但同时包含登录节点和数据提取节点。"
                "登录后若目标数据在不同页面，必须添加第二个 browser.open（或菜单点击导航）跳转到数据页，"
                "否则 browser.wait / browser.extract 节点会在登录成功页（仪表盘/首页）等待，永远等不到目标元素。"
            ),
            "fix": (
                "在登录完成节点之后、数据提取节点之前，添加 browser.open 节点"
                "（targetUrl 填目标页面地址，delayMs: 3000）并连线：登录完成 → 导航节点 → 等待/提取节点。"
            ),
        })

    # ── Undefined variable references ─────────────────────────────────────────
    if input_variable_names is not None:
        for ref_issue in _validate_variable_refs(nodes, list(input_variable_names)):
            undefined = ref_issue.get("undefined_variables", [])
            nid = ref_issue.get("node_id", "?")
            ntitle = ref_issue.get("node_title", nid)
            findings.append({
                "severity": "error",
                "node_id": nid,
                "node_title": ntitle,
                "issue": "undefined_variable_ref",
                "message": (
                    f"节点 `{nid}` 引用了未定义变量：{undefined}。"
                    "这些变量既不在 input_variables 中，也不由任何上游节点产出，运行时将报「变量未定义」。"
                ),
                "fix": (
                    "在 input_variables 中声明该变量（category=flow/credential），"
                    "或在上游添加 variable.set 节点赋默认值，"
                    "或删除节点中的 ${var.xxx} 引用。"
                ),
                "undefined_variables": undefined,
            })

    return findings


def _lint_variable_contract_for_node(node: dict[str, Any], *, nid: str, ntitle: str, ntype: str) -> list[dict[str, Any]]:
    """Enforce the single variable syntax contract across all generated flows."""
    findings: list[dict[str, Any]] = []

    for field in sorted(_VARIABLE_NAME_FIELDS):
        value = node.get(field)
        refs = _template_refs(value)
        if not refs:
            continue
        expected = refs[0] if len(refs) == 1 and str(value).strip() == f"${{var.{refs[0]}}}" else refs[0]
        findings.append({
            "severity": "error",
            "node_id": nid,
            "node_title": ntitle,
            "issue": "variable_name_field_uses_template",
            "message": (
                f"节点 `{nid}` 的 `{field}` 是变量名字段，但写成了模板值 `{value}`。"
                "变量名字段只能写裸变量名。"
            ),
            "fix": f"把 `{field}` 改为 `{expected}`；`${{var.xxx}}` 只能用于 inputValue/value/path/message 等取值字段。",
            "field": field,
        })

    if ntype == "control.condition":
        for field in _CONDITION_EXPRESSION_FIELDS:
            value = node.get(field)
            refs = _template_refs(value)
            if not refs:
                continue
            expression = str(value)
            normalized = expression
            for ref in refs:
                normalized = normalized.replace(f"${{var.{ref}}}", ref)
            findings.append({
                "severity": "error",
                "node_id": nid,
                "node_title": ntitle,
                "issue": "condition_expression_uses_template",
                "message": (
                    f"条件节点 `{nid}` 的 `{field}` 使用了模板变量 `{expression}`。"
                    "条件表达式只允许裸变量名。"
                ),
                "fix": f"把 `{field}` 改为 `{normalized}`，例如 `login_count > 0`。",
                "field": field,
            })
    return findings


def _lint_continue_on_error_output_defaults(nodes: list[Any], edges: list[Any]) -> list[dict[str, Any]]:
    """阻断容错节点输出变量未初始化的结构缺陷。

    `continueOnError` 节点失败后执行器会继续向下走，但它原本应该产出的
    outputVariable/countVariable 可能根本不会写入。只要下游条件或节点引用该变量，
    就会出现“变量未定义”或错误分支判断。这里要求模型先用 variable.set 在上游
    初始化默认值，再允许该变量由容错节点覆盖。
    """
    node_map: dict[str, dict[str, Any]] = {
        str(node["id"]): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    parents_by_target: dict[str, list[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict) or not edge.get("source") or not edge.get("target"):
            continue
        parents_by_target.setdefault(str(edge["target"]), []).append(str(edge["source"]))

    refs_by_var: dict[str, list[dict[str, Any]]] = {}
    for node in node_map.values():
        for var_name in _collect_refs_in_node(node):
            refs_by_var.setdefault(var_name, []).append(node)

    findings: list[dict[str, Any]] = []
    for node in node_map.values():
        if not node.get("continueOnError"):
            continue
        output_vars = _collect_output_vars_in_node(node)
        if not output_vars:
            continue
        node_id = str(node.get("id", "?"))
        ancestor_ids = _collect_ancestor_node_ids(node_id, parents_by_target)
        for var_name in sorted(output_vars):
            consumers = [
                consumer for consumer in refs_by_var.get(var_name, [])
                if str(consumer.get("id")) != node_id
            ]
            if not consumers:
                continue
            if _has_upstream_variable_default(var_name, ancestor_ids, node_map):
                continue
            findings.append({
                "severity": "error",
                "node_id": node_id,
                "node_title": str(node.get("title") or node_id),
                "issue": "continue_on_error_output_without_default",
                "message": (
                    f"节点 `{node_id}` 启用了 continueOnError，并产出变量 `{var_name}`，"
                    "但该变量被后续节点引用，且在此节点之前没有 variable.set 默认值。"
                    "一旦该节点超时或失败，流程会继续执行，却会在下游触发变量未定义或错误分支。"
                ),
                "fix": (
                    f"在 `{node_id}` 之前新增或移动一个 variable.set 节点，"
                    f"设置 variableName='{var_name}' 和安全默认值（计数用 0，列表用 []，文本用空字符串）；"
                    "然后保留当前容错节点覆盖该变量。"
                ),
                "consumer_node_ids": [str(consumer.get("id", "?")) for consumer in consumers[:8]],
            })
    return findings


def _collect_ancestor_node_ids(node_id: str, parents_by_target: dict[str, list[str]]) -> set[str]:
    ancestors: set[str] = set()
    stack = list(parents_by_target.get(node_id, []))
    while stack:
        current = stack.pop()
        if current in ancestors:
            continue
        ancestors.add(current)
        stack.extend(parents_by_target.get(current, []))
    return ancestors


def _has_upstream_variable_default(
    var_name: str,
    ancestor_ids: set[str],
    node_map: dict[str, dict[str, Any]],
) -> bool:
    for ancestor_id in ancestor_ids:
        ancestor = node_map.get(ancestor_id)
        if not ancestor or ancestor.get("type") != "variable.set":
            continue
        if str(ancestor.get("variableName") or "").strip() == var_name:
            return True
    return False


def _lint_critical_continue_on_error(nodes: list[Any], edges: list[Any]) -> list[dict[str, Any]]:
    """发现关键业务动作失败被吞掉的链路风险。

    continueOnError 适合可选弹窗、Cookie 提示、登录态探测等“缺失也合理”的节点；
    不适合筛选条件、提交按钮、关键导航和结果等待。否则前置动作失败后流程继续执行，
    最后报错会落在等待/抽取节点上，AI 容易反复修改末端 selector。
    """
    node_map: dict[str, dict[str, Any]] = {
        str(node["id"]): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    downstream_by_source: dict[str, list[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict) or not edge.get("source") or not edge.get("target"):
            continue
        downstream_by_source.setdefault(str(edge["source"]), []).append(str(edge["target"]))

    findings: list[dict[str, Any]] = []
    for node in node_map.values():
        if not node.get("continueOnError"):
            continue
        if _is_allowed_continue_on_error(node):
            continue
        if not _is_critical_business_action(node):
            continue
        downstream = _collect_downstream_nodes(str(node.get("id")), downstream_by_source, node_map, limit=8)
        has_result_step = any(
            str(candidate.get("type")) in {"browser.wait", "browser.extract", "file.write", "excel.addrow", "excel.save"}
            for candidate in downstream
        )
        if not has_result_step:
            continue
        findings.append({
            "severity": "warn",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "critical_action_continue_on_error",
            "message": (
                f"关键业务动作 `{node.get('id', '?')}` 启用了 continueOnError。"
                "如果该筛选/提交/导航动作失败，流程仍会继续到后续等待或抽取节点，"
                "最终错误会被错误归因到末端 selector，导致 AI 反复修错位置。"
            ),
            "fix": (
                "关键筛选、提交、导航、结果等待节点默认不要 continueOnError；"
                "只有登录检测、可选弹窗关闭、Cookie 横幅等可缺失节点才允许。"
                "若确实需要容错，应在后面增加可验证的校验节点，并让校验失败中断流程。"
            ),
            "downstream_node_ids": [str(candidate.get("id", "?")) for candidate in downstream[:8]],
        })
    return findings


def _collect_downstream_nodes(
    node_id: str,
    downstream_by_source: dict[str, list[str]],
    node_map: dict[str, dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    seen = {node_id}
    queue = list(downstream_by_source.get(node_id, []))
    while queue and len(collected) < limit:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        node = node_map.get(current)
        if node is not None:
            collected.append(node)
        queue.extend(downstream_by_source.get(current, []))
    return collected


def _is_allowed_continue_on_error(node: dict[str, Any]) -> bool:
    marker = f"{node.get('id', '')} {node.get('title', '')} {node.get('description', '')} {node.get('selector', '')}".lower()
    optional_markers = (
        "cookie", "弹窗", "关闭", "close", "dismiss", "可选",
        "检测登录", "登录检测", "password", "input[type='password']",
    )
    return any(marker_text in marker for marker_text in optional_markers)


def _is_critical_business_action(node: dict[str, Any]) -> bool:
    ntype = str(node.get("type", ""))
    if ntype not in {
        "browser.click", "browser.fill", "browser.select", "browser.check",
        "browser.press", "browser.wait", "browser.open", "ui.click", "ui.fill",
        "ui.select", "ui.check",
    }:
        return False
    marker = (
        f"{node.get('title', '')} {node.get('description', '')} "
        f"{node.get('selector', '')} {node.get('inputValue', '')}"
    ).lower()
    critical_markers = (
        "筛选", "过滤", "查询", "搜索", "提交", "确认", "确定", "应用",
        "日期", "时间", "开始", "结束", "状态", "进度", "类型", "分类",
        "多选", "下拉", "select", "filter", "search", "submit", "apply",
        "confirm", "date", "time", "status", "category", "dropdown",
        "导航", "跳转", "列表页", "详情页", "结果", "表格", "列表",
    )
    return any(keyword in marker for keyword in critical_markers)


def _lint_flow_semantic_quality(nodes: list[Any]) -> list[dict[str, Any]]:
    """Detect semantic smells that make generated flows hard to repair."""
    findings: list[dict[str, Any]] = []
    business_nodes = [
        node for node in nodes
        if isinstance(node, dict) and node.get("type") not in ("start", "end")
    ]
    diagnostic_nodes = [
        node for node in business_nodes
        if _is_diagnostic_node(node)
    ]
    if len(diagnostic_nodes) > 2:
        first = diagnostic_nodes[0]
        findings.append({
            "severity": "warn",
            "node_id": str(first.get("id", "?")),
            "node_title": str(first.get("title") or first.get("id", "?")),
            "issue": "diagnostic_node_bloat",
            "message": (
                f"流程中存在 {len(diagnostic_nodes)} 个诊断/截图类节点。"
                "诊断节点用于定位问题，不应长期留在正式自动化链路中。"
            ),
            "fix": (
                "保留最多 1-2 个确有产物价值的截图/日志节点；"
                "删除 title/id 含“诊断/diag”的临时节点，避免流程越修越长。"
            ),
            "diagnostic_node_ids": [str(node.get("id", "?")) for node in diagnostic_nodes[:12]],
        })

    long_wait_nodes = [
        node for node in business_nodes
        if _is_long_fixed_wait(node)
    ]
    for node in long_wait_nodes[:8]:
        delay_ms = node.get("delayMs") if node.get("type") != "control.delay" else node.get("delayMs")
        findings.append({
            "severity": "warn",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "long_fixed_wait",
            "message": (
                f"节点 `{node.get('id', '?')}` 使用了 {delay_ms}ms 固定等待。"
                "长固定等待会拖慢运行，也会掩盖真实的导航或选择器问题。"
            ),
            "fix": (
                "优先改为 browser.wait 等待稳定 DOM 选择器；"
                "SPA 跳转只在 browser.open/click 后保留 2000-3000ms 的短 delayMs。"
            ),
        })

    findings.extend(_lint_login_detection_risks(business_nodes))
    findings.extend(_lint_login_challenge_risks(business_nodes))
    findings.extend(_lint_navigation_risks(business_nodes))
    findings.extend(_lint_filter_control_risks(business_nodes))
    findings.extend(_lint_table_output_risks(business_nodes))
    findings.extend(_lint_script_environment_risks(business_nodes))
    return findings


def _is_diagnostic_node(node: dict[str, Any]) -> bool:
    marker = f"{node.get('id', '')} {node.get('title', '')} {node.get('description', '')}".lower()
    return node.get("type") == "browser.screenshot" or "diag" in marker or "诊断" in marker


def _is_long_fixed_wait(node: dict[str, Any]) -> bool:
    delay_ms = node.get("delayMs")
    if isinstance(delay_ms, (int, float)) and not isinstance(delay_ms, bool) and delay_ms >= 8000:
        return True
    if node.get("type") == "control.delay":
        delay_ms = node.get("delayMs")
        return isinstance(delay_ms, (int, float)) and not isinstance(delay_ms, bool) and delay_ms >= 5000
    return False


def _lint_login_detection_risks(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    variable_defaults: dict[str, str] = {}
    for node in nodes:
        if node.get("type") == "variable.set" and node.get("variableName"):
            variable_defaults[str(node["variableName"])] = str(node.get("value", ""))

    for node in nodes:
        if node.get("type") != "browser.extract":
            continue
        selector = str(node.get("selector", "")).lower()
        count_variable = node.get("countVariable")
        if "password" not in selector or not count_variable or not node.get("continueOnError"):
            continue
        timeout_ms = node.get("timeoutMs")
        is_short_timeout = not isinstance(timeout_ms, (int, float)) or timeout_ms <= 5000
        default_zero = variable_defaults.get(str(count_variable)) in {"", "0", "false", "False"}
        if not is_short_timeout or not default_zero:
            continue
        findings.append({
            "severity": "warn",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "login_detection_timeout_may_skip_login",
            "message": (
                f"登录检测节点 `{node.get('id', '?')}` 用密码框 countVariable 判断是否需要登录，"
                "并启用了 continueOnError。页面空白、SPA 未渲染或登录页加载慢时，超时会保持 "
                f"{count_variable}=0，后续条件容易误判为“已登录”，直接跳过登录分支。"
            ),
            "fix": (
                "在检测前先等待登录页或导航栏任一稳定元素；默认保留 Cookies/localStorage 复用登录态；"
                "若目标数据页不同，登录完成后显式 browser.open 到目标 URL。"
            ),
        })
    return findings


def _lint_login_challenge_risks(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """检查登录二次验证建模是否自洽，而不是偏向某一种登录方式。"""
    findings: list[dict[str, Any]] = []
    has_password_fill = any(
        node.get("type") == "browser.fill"
        and ("password" in str(node.get("selector", "")).lower() or "密码" in str(node.get("title", "")))
        for node in nodes
    )
    if not has_password_fill:
        return findings

    flow_text = " ".join(
        f"{node.get('id', '')} {node.get('title', '')} {node.get('description', '')} "
        f"{node.get('selector', '')} {node.get('message', '')} {node.get('variableName', '')}"
        for node in nodes
    ).lower()
    challenge_keywords = (
        "captcha", "验证码", "动态验证码", "短信", "sms", "2fa", "mfa", "totp",
        "otp", "二次验证", "双因素", "扫码", "二维码", "授权登录", "oauth", "sso",
    )
    mentions_challenge = any(keyword in flow_text for keyword in challenge_keywords)
    has_runtime_input = any(
        node.get("type") == "variable.input"
        for node in nodes
    )
    has_challenge_fill = any(
        node.get("type") == "browser.fill"
        and any(keyword in f"{node.get('title', '')} {node.get('selector', '')} {node.get('inputValue', '')}".lower()
                for keyword in challenge_keywords)
        for node in nodes
    )
    has_qr_or_oauth_wait = any(
        node.get("type") == "browser.wait"
        and any(keyword in f"{node.get('title', '')} {node.get('selector', '')}".lower()
                for keyword in ("扫码", "二维码", "qr", "授权", "oauth", "sso", "nav", "menu", "首页"))
        for node in nodes
    )
    if mentions_challenge and not (has_runtime_input or has_challenge_fill or has_qr_or_oauth_wait):
        password_node = next(
            node for node in nodes
            if node.get("type") == "browser.fill"
            and ("password" in str(node.get("selector", "")).lower() or "密码" in str(node.get("title", "")))
        )
        findings.append({
            "severity": "warn",
            "node_id": str(password_node.get("id", "?")),
            "node_title": str(password_node.get("title") or password_node.get("id", "?")),
            "issue": "login_challenge_not_modeled",
            "message": (
                "流程文本提到了验证码/2FA/扫码/授权等登录挑战，但账号密码后没有对应的运行时输入、"
                "验证码填写、扫码等待或授权完成等待链路。"
            ),
            "fix": (
                "先根据 inspect_page/用户需求分类登录方式：图形验证码/短信/TOTP 用 variable.input；"
                "扫码/OAuth/SSO 用 browser.wait 等待授权后应用导航或目标页出现；不要把一种登录方式硬套到另一种。"
            ),
        })
    return findings


def _lint_navigation_risks(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    has_login_detection = any(
        node.get("type") == "browser.extract"
        and str(node.get("extractMode", "")).lower() == "count"
        and (
            "password" in str(node.get("selector", "")).lower()
            or "密码" in f"{node.get('title', '')} {node.get('description', '')}"
            or "登录" in f"{node.get('title', '')} {node.get('description', '')}"
        )
        for node in nodes
    )
    has_login_branch_or_fill = any(
        (
            node.get("type") == "control.condition"
            and any(keyword in f"{node.get('title', '')} {node.get('description', '')} {node.get('inputValue', '')}" for keyword in ("登录", "login_count", "password_count"))
        )
        or (
            node.get("type") == "browser.fill"
            and any(keyword in f"{node.get('title', '')} {node.get('selector', '')} {node.get('inputValue', '')}".lower() for keyword in ("password", "username", "captcha", "密码", "用户名", "验证码"))
        )
        for node in nodes
    )
    clear_storage_nodes = [
        node for node in nodes
        if node.get("type") == "browser.open" and node.get("clearStorage") is True
    ]
    if clear_storage_nodes and (has_login_detection or has_login_branch_or_fill):
        first = clear_storage_nodes[0]
        findings.append({
            "severity": "error",
            "node_id": str(first.get("id", "?")),
            "node_title": str(first.get("title") or first.get("id", "?")),
            "issue": "clear_storage_breaks_login_persistence",
            "message": (
                "流程包含登录态检测/登录分支，但 browser.open 设置了 clearStorage=true。"
                "这会清空 localStorage/sessionStorage，破坏持久登录态，导致每次运行都重新进入登录链路。"
            ),
            "fix": (
                "默认删除 clearStorage 或设为 false；只有用户明确要求重置登录态，"
                "或 inspect_page/运行日志证明过期 token 导致 SPA 卡死时，才临时启用 clearStorage。"
            ),
        })

    text_menu_clicks = [
        node for node in nodes
        if node.get("type") in {"browser.click", "browser.hover"}
        and str(node.get("selector", "")).strip().startswith("text=")
        and any(keyword in f"{node.get('title', '')} {node.get('selector', '')}"
                for keyword in ("菜单", "管理", "列表", "导航"))
    ]
    open_urls = [
        str(node.get("targetUrl") or node.get("url") or "")
        for node in nodes
        if node.get("type") == "browser.open"
    ]
    has_secondary_navigation_url = len({url for url in open_urls if url.strip()}) >= 2
    if text_menu_clicks and not has_secondary_navigation_url:
        first = text_menu_clicks[0]
        findings.append({
            "severity": "warn",
            "node_id": str(first.get("id", "?")),
            "node_title": str(first.get("title") or first.get("id", "?")),
            "issue": "fragile_text_menu_navigation",
            "message": (
                "流程依赖 text= 菜单文案导航，但没有登录后直接打开目标页面 URL 的兜底节点。"
                "菜单可能因权限、折叠、布局或登录后默认页变化而不可见。"
            ),
            "fix": (
                "优先在登录完成后 browser.open 到已验证可达的目标页面 URL（path/query/hash 均可），"
                "再等待目标表格/筛选控件；菜单点击只作为 direct route 失败后的诊断备选。"
            ),
        })
    return findings


def _lint_filter_control_risks(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    date_fill_nodes = [
        node for node in nodes
        if node.get("type") == "browser.fill"
        and any(keyword in f"{node.get('title', '')} {node.get('selector', '')}".lower()
                for keyword in ("日期", "时间", "date", "开始", "结束"))
    ]
    for node in date_fill_nodes[:4]:
        findings.append({
            "severity": "error",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "date_range_fill_may_not_update_model",
            "message": (
                "日期筛选使用 browser.fill 写输入框。Element UI/Ant Design 的日期范围组件"
                "经常不会因此更新内部筛选模型，导致 UI 看似有值但查询未生效。"
            ),
            "fix": (
                "调用 inspect_page(scope_selector=筛选区域)。"
                "若返回 date_controls[].interaction_recipe：直接按 steps 构建 browser.click 节点，使用 recipe 中的 selector，"
                "无需参考 n14-n17 的具体 selector。"
                "若 date_controls 为空：按 n14-n17 四段式（触发 → 开始日期 → 结束日期 → 确定）作为 Element UI 兜底，"
                "selector 取 inputs 字段精确值，不要用 first-of-type 或逗号候选。"
                "运行后调 assert_run_output(start_date/end_date) 确认筛选真实生效。"
            ),
        })

    date_click_nodes = [
        node for node in nodes
        if node.get("type") == "browser.click"
        and any(keyword in f"{node.get('title', '')} {node.get('selector', '')}".lower()
                for keyword in ("日期", "时间", "date", "开始", "结束"))
    ]
    broad_date_trigger_nodes = []
    for node in date_click_nodes:
        selector = str(node.get("selector", "")).strip()
        lowered = selector.lower()
        if (
            "," in selector
            or "placeholder*=" in lowered
            or ".el-date-editor:first-of-type" in lowered
            or ".ant-picker:first-of-type" in lowered
            or ":first-of-type input" in lowered
        ):
            broad_date_trigger_nodes.append(node)

    for node in broad_date_trigger_nodes[:4]:
        findings.append({
            "severity": "warn",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "date_trigger_selector_too_broad",
            "message": (
                "日期触发节点使用了逗号候选、模糊 placeholder 或 first-of-type selector。"
                "这类 selector 可能打开错误日期控件或错误月份面板，导致后续点击日期看似成功但筛选条件错误。"
            ),
            "fix": (
                "调用 inspect_page(scope_selector=筛选区域)。"
                "若返回 date_controls[].interaction_recipe：用 recipe.trigger 替换当前过宽的触发 selector，"
                "其余节点 selector 也按 recipe 更新，无需参考 n14-n17 的具体 selector。"
                "若 date_controls 为空：从 inputs 字段选取唯一精确输入框，不要使用逗号候选、placeholder*= 或 first-of-type。"
                "修复后运行并调 assert_run_output(start_date/end_date) 确认生效。"
            ),
        })

    bad_escape_nodes = [
        node for node in nodes
        if node.get("type") == "browser.press"
        and str(node.get("inputValue", "")).lower() == "escape"
        and str(node.get("selector", "")).strip().lower() not in {"body", "html", "document"}
    ]
    for node in bad_escape_nodes[:4]:
        findings.append({
            "severity": "warn",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "dropdown_escape_bound_to_unstable_input",
            "message": (
                "关闭组件库下拉时把 Escape 发送给业务输入框。多选组件在选项点击后可能重建输入框，"
                "导致 press 找不到元素或无法关闭弹层。"
            ),
            "fix": "将 selector 改为 body，让运行器执行 page.keyboard.press('Escape')。",
        })

    invalid_attr_extracts = [
        node for node in nodes
        if node.get("type") == "browser.extract"
        and str(node.get("selector", "")).endswith("::attr(value)")
        and str(node.get("extractMode", "")).lower() == "attribute"
        and not node.get("attribute")
    ]
    for node in invalid_attr_extracts[:4]:
        findings.append({
            "severity": "warn",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "redundant_attr_selector_and_mode",
            "message": (
                "节点同时使用 selector 的 ::attr(value) 语法和 extractMode=attribute，"
                "不同层的解析容易不一致，可能得到空数组。"
            ),
            "fix": "保留 selector='input[...]'，显式设置 extractMode='attribute'、attribute='value'。",
        })
    return findings


# ─── Structural selector analysis helpers ─────────────────────────────────────

# Known component-library class prefixes used in Chinese/international enterprise
# frontends. A class that starts with one of these is a "framework class" and
# does NOT provide a business-domain scope on its own.
_FRAMEWORK_CLASS_PREFIXES: tuple[str, ...] = (
    "el-",        # Element UI / Element Plus
    "ant-",       # Ant Design (React / Vue)
    "arco-",      # Arco Design (ByteDance)
    "vxe-",       # Vxe Table / Vxe UI
    "n-",         # Naive UI
    "van-",       # Vant (mobile)
    "ivu-",       # iView / View UI
    "layui-",     # LayUI
    "semi-",      # Semi Design (ByteDance)
    "tdesign-",   # TDesign (Tencent)
    "varlet-",    # Varlet (mobile)
    "vc-",        # Vue Component generic
    "v-",         # Generic Vue directive/component prefix
)

# HTML structural elements that cannot serve as a business-domain scope.
_HTML_STRUCTURAL_TAGS: frozenset[str] = frozenset(
    ["html", "body", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
     "div", "span", "ul", "ol", "li", "section", "article", "main", "aside",
     "header", "footer", "nav", "form", "fieldset"]
)

# Generic layout/structural vocabulary.  A CSS class whose every hyphen/underscore-
# separated word belongs to this set has no business-domain meaning and CANNOT act
# as a scoping ancestor for table selectors (e.g. app-main-container, layout-wrapper).
_LAYOUT_WORDS: frozenset[str] = frozenset([
    "app", "page", "main", "layout", "content", "wrapper", "container",
    "inner", "outer", "shell", "frame", "view", "root", "section",
    "area", "panel", "box", "wrap", "base", "center", "body",
    "fluid", "fixed", "scroll",
])


def _is_layout_only_class(cls: str) -> bool:
    """Return True when a CSS class name is composed entirely of generic layout words."""
    words = [w for w in re.split(r"[-_]", cls.lower()) if w]
    return bool(words) and all(w in _LAYOUT_WORDS for w in words)


def _extract_classes_from_token(token: str) -> list[str]:
    """Return all CSS class names from a compound selector token (e.g. 'tr.foo.bar' → ['foo','bar'])."""
    return re.findall(r'\.([a-zA-Z][a-zA-Z0-9_-]*)', token)


def _is_framework_class(cls: str) -> bool:
    return any(cls.lower().startswith(p) for p in _FRAMEWORK_CLASS_PREFIXES)


def _token_has_business_scope(token: str) -> bool:
    """Return True if a CSS token provides a business-domain scope.

    A token has business scope when it contains at least one class that is NOT
    a framework-prefixed class.  Pure HTML elements, ARIA attributes, and
    tokens whose every class is framework-prefixed all lack business scope.
    """
    t = token.strip().lower()
    # ID selector is always a business scope
    if "#" in t:
        return True
    classes = _extract_classes_from_token(t)
    if not classes:
        # bare tag, [attr], :pseudo — no business scope
        return False
    return any(not _is_framework_class(cls) for cls in classes)


def _is_table_container_token(token: str) -> bool:
    """Return True if a CSS token targets a table-level container, not a data row.

    Covers any component library: a class is considered a "table container"
    when it contains the word 'table' but does NOT end with a row-level
    suffix (-row, __row, -tr, __tr).
    """
    t = token.strip().lower()
    if t == "table":
        return True
    if re.match(r'^\[role=[\'"]?(grid|table)[\'"]?\]$', t):
        return True
    for cls in _extract_classes_from_token(t):
        if "table" in cls and not re.search(r'[-_](row|tr)$|__row$|__tr$', cls):
            return True
    return False


def _is_table_container_selector(selector: str) -> bool:
    """Return True if a full CSS selector ends at the table-container level.

    Checks the rightmost (most-specific) token, which is the element actually
    selected.  Works for any component library.
    """
    s = re.sub(r":has-text\([^)]*\)", "", selector).strip().lower()
    s = re.sub(r"\s+", " ", s)
    last_token = s.split()[-1] if s.split() else s
    return _is_table_container_token(last_token)


def _is_broad_table_row_selector(selector: str) -> bool:
    """Return True when selector reaches row level without a business-domain parent scope.

    Structural replacement for the previous El-UI-specific whitelist.
    Works for any component library: Element UI, Ant Design, Arco, Vxe, Naive UI,
    Bootstrap, or any custom table component.

    A selector is "broad" when its leftmost (outermost) token is:
      - a bare HTML structural element (tr, tbody, table, …),
      - an ARIA role attribute ([role=row], [role=grid]), or
      - a compound selector whose every class is framework-prefixed.
    All three cases mean the selector has no business-class parent to scope it
    to a specific table on the page.
    """
    s = re.sub(r":has-text\([^)]*\)", "", selector)
    s = re.sub(r"\s*:scope\s*", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    for prefix in ("html ", "body "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    if not s:
        return False

    leftmost = s.split()[0]

    # ID selector → unique business scope → not broad
    if "#" in leftmost:
        return False

    classes = _extract_classes_from_token(leftmost)
    if not classes:
        # bare tag or [attr] — no business scope
        return True

    # broad if every class is framework-prefixed or layout-only (no business class present)
    return all(_is_framework_class(cls) or _is_layout_only_class(cls) for cls in classes)


# ─── Lint: table extraction risks ─────────────────────────────────────────────

# Pre-filter keywords: node must mention at least one of these to be a
# candidate for table-extraction lint. Covers all major component libraries.
_TABLE_HINT_KEYWORDS: tuple[str, ...] = (
    "table", "tbody", "tr", "表格", "列表",
    "row", "grid", "-row", "__row", "--row",
    "vxe", "arco", "ant-table", "n-data",
)


def _lint_table_output_risks(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("type") != "browser.extract":
            continue
        selector = str(node.get("selector", "")).lower()
        title = str(node.get("title", "")).lower()
        if any(keyword in title for keyword in ("header", "表头", "列名")):
            continue
        if not any(keyword in f"{selector} {title}" for keyword in _TABLE_HINT_KEYWORDS):
            continue
        if node.get("extractMode") != "table":
            findings.append({
                "severity": "warn",
                "node_id": str(node.get("id", "?")),
                "node_title": str(node.get("title") or node.get("id", "?")),
                "issue": "table_extract_without_table_mode",
                "message": (
                    "表格/列表抽取节点没有使用 extractMode='table'，后续会得到纯文本数组，"
                    "难以稳定写 JSON/Excel，也容易丢列结构。"
                ),
                "fix": "设置 extractMode='table'，outputVariable 使用业务名，并补充 countVariable 统计行数。",
            })
        else:
            selector_parts = [part.strip() for part in selector.split(",") if part.strip()]
            container_like = False
            too_broad = False
            for part in selector_parts:
                s = re.sub(r":has-text\([^)]*\)", "", part).strip()
                if _is_table_container_selector(s):
                    container_like = True
                if _is_broad_table_row_selector(s):
                    too_broad = True
            # has_row_target: selector reaches down to row level at all
            has_row_target = any(
                kw in selector
                for kw in ("tbody", " tr", "__row", "-row", "--row", "[role=row]")
            )
            if container_like and not has_row_target:
                findings.append({
                    "severity": "error",
                    "node_id": str(node.get("id", "?")),
                    "node_title": str(node.get("title") or node.get("id", "?")),
                    "issue": "table_extract_selector_targets_container",
                    "message": (
                        "表格抽取使用了 extractMode='table'，但 selector 指向整张表容器而非数据行。"
                        "这容易把整张表抽成一个扁平数组，导致行结构和字段校验失效。"
                    ),
                    "fix": (
                        "调用 inspect_page，直接使用返回的 tables[].row_selector（已自动收窄到业务容器作用域）；"
                        "若 row_selector 为 null，从 page_layout 中找到业务容器 class，"
                        "手动拼接 '.<业务容器> tr' 格式。"
                    ),
                })
            if too_broad:
                findings.append({
                    "severity": "warn",
                    "node_id": str(node.get("id", "?")),
                    "node_title": str(node.get("title") or node.get("id", "?")),
                    "issue": "table_extract_selector_too_broad",
                    "message": (
                        "表格抽取 selector 最左侧没有业务域父容器作用域。"
                        "页面存在日期面板、分页器、隐藏表头、固定列或多个表格时，"
                        "容易混入非业务 UI 行，导致运行成功但业务审计失败。"
                    ),
                    "fix": (
                        "调用 inspect_page，优先使用 tables[].row_selector（已含业务作用域前缀）；"
                        "若 row_selector 为 null，从 page_layout 找业务容器 class，"
                        "手动拼接 '.<业务容器> <行selector>'。"
                        "最左侧 class 不能是框架前缀（el-/ant-/arco-/vxe-/n-）或纯布局词（app/main/container 等）。"
                    ),
                })
        if not node.get("countVariable"):
            findings.append({
                "severity": "warn",
                "node_id": str(node.get("id", "?")),
                "node_title": str(node.get("title") or node.get("id", "?")),
                "issue": "table_extract_missing_count",
                "message": "表格抽取节点缺少 countVariable，运行结果难以快速判断抓取行数。",
                "fix": "添加 countVariable，例如 project_table_count。",
            })
    return findings


def _lint_script_environment_risks(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    browser_globals = ("document.", "document[", "window.", "window[", "location.", "localstorage", "sessionstorage")
    for node in nodes:
        if node.get("type") not in {"script.javascript", "script.python", "script.shell"}:
            continue
        code = str(node.get("code") or "")
        if not code:
            continue
        lowered = code.lower()
        if not any(token in lowered for token in browser_globals):
            continue
        findings.append({
            "severity": "error",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "script_uses_browser_dom",
            "message": (
                "脚本节点包含 document/window/location/localStorage 等浏览器 DOM API。"
                "script.* 节点在本地 Python/Node/Shell 环境执行，不在页面上下文执行，运行时会报 document/window 未定义。"
            ),
            "fix": (
                "删除该脚本节点，改用 browser.fill/browser.click/browser.extract 等浏览器节点；"
                "若需要页面内 JS，必须使用已有浏览器动作能力或新增专门的 browser.evaluate 节点，"
                "不能把 DOM 脚本放进 script.javascript。"
            ),
        })
    return findings


def _lint_visual_layout(nodes: list[Any]) -> list[dict[str, Any]]:
    """Detect canvas node collisions that make generated flows hard to inspect.

    The runner does not care about node coordinates, but the Studio canvas does.
    AI-generated branch flows often place sibling branches at x=360 and x=560:
    with 240 px wide cards those rectangles overlap even though the columns look
    different numerically. Catching this in lint makes layout quality a backend
    contract instead of a prompt suggestion.
    """
    findings: list[dict[str, Any]] = []
    layout_nodes = [node for node in nodes if isinstance(node, dict) and isinstance(node.get("position"), dict)]
    seen_pairs: set[tuple[str, str]] = set()
    for index, left in enumerate(layout_nodes):
        for right in layout_nodes[index + 1:]:
            left_id = str(left.get("id", "?"))
            right_id = str(right.get("id", "?"))
            if {left_id, right_id} <= {"start", "end"}:
                continue
            if not _nodes_visually_overlap(left, right):
                continue
            pair = tuple(sorted((left_id, right_id)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            findings.append({
                "severity": "warn",
                "node_id": left_id,
                "node_title": str(left.get("title") or left_id),
                "issue": "node_visual_overlap",
                "message": (
                    f"节点 `{left_id}` 与 `{right_id}` 在画布中发生视觉重叠，"
                    "分支节点会互相遮挡，影响审查和调试。"
                ),
                "fix": (
                    "调用 update_flow 显式调整 position，或重新保存流程触发服务端布局规整。"
                    "建议主干 x=560，分支列至少使用 x=280 / x=840，纵向间距不小于 120。"
                ),
                "overlap_with": right_id,
            })
            if len(findings) >= 12:
                findings.append({
                    "severity": "warn",
                    "node_id": left_id,
                    "node_title": str(left.get("title") or left_id),
                    "issue": "node_visual_overlap_truncated",
                    "message": "画布重叠节点超过 12 组，已截断返回；建议触发整体布局规整。",
                    "fix": "调用 update_flow 批量调整 position，或减少不必要分支/诊断节点。",
                })
                return findings
    return findings


def _nodes_visually_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    NODE_W, NODE_H, GAP = 240, 84, 16
    left_pos = left.get("position") or {}
    right_pos = right.get("position") or {}
    left_x = _read_layout_number(left_pos.get("x"), 560)
    left_y = _read_layout_number(left_pos.get("y"), 0)
    right_x = _read_layout_number(right_pos.get("x"), 560)
    right_y = _read_layout_number(right_pos.get("y"), 0)
    return (
        left_x < right_x + NODE_W + GAP
        and left_x + NODE_W + GAP > right_x
        and left_y < right_y + NODE_H + GAP
        and left_y + NODE_H + GAP > right_y
    )


def _read_layout_number(value: Any, default: int) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return default


def _read_node_x(node: dict[str, Any]) -> int:
    position = node.get("position") or {}
    return _read_layout_number(position.get("x"), 560) if isinstance(position, dict) else 560


def _read_node_y(node: dict[str, Any]) -> int:
    position = node.get("position") or {}
    return _read_layout_number(position.get("y"), 0) if isinstance(position, dict) else 0


def _next_layout_lane(current_lane: int, label: Any) -> int:
    normalized = str(label or "").strip().lower()
    if normalized in {"true", "body", "是"}:
        return current_lane - 1
    if normalized in {"false", "exit", "否"}:
        return current_lane + 1
    return current_lane


def _choose_layout_lane(current_lane: int, incoming_lane: int) -> int:
    if current_lane == incoming_lane:
        return current_lane
    # When branches merge, return toward the main lane instead of keeping the
    # first branch lane forever. This keeps downstream work centered.
    if current_lane < 0 < incoming_lane or incoming_lane < 0 < current_lane:
        return 0
    return current_lane if abs(current_lane) <= abs(incoming_lane) else incoming_lane


_KIND_BY_TYPE_PREFIX = {
    "browser": "browser",
    "ui": "browser",
    "excel": "excel",
    "file": "file",
    "http": "http",
    "variable": "variable",
    "control": "control",
    "data": "data",
    "script": "python",
}


def _normalize_generated_node(node: Any, index: int) -> Any:
    """把不同模型的节点输出规整为执行器使用的平铺结构。"""
    if not isinstance(node, dict):
        return node

    normalized: dict[str, Any] = {}
    action_payload = node.get("action") if isinstance(node.get("action"), dict) else None
    config_payload = node.get("config") if isinstance(node.get("config"), dict) else None
    for payload in (action_payload, config_payload, node):
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            if key in {"action", "config", "data"}:
                continue
            normalized[key] = value

    node_id = str(normalized.get("id") or f"n_{uuid4()}")
    node_type = str(normalized.get("type") or "")
    if not node_type and action_payload:
        node_type = str(action_payload.get("type") or "")
    normalized["id"] = node_id
    if node_type:
        normalized["type"] = node_type
    normalized.setdefault("title", _default_node_title(node_type, node_id))
    normalized.setdefault("status", "pending")
    normalized.setdefault("description", str(normalized.get("title", node_id)))

    kind = normalized.get("kind")
    if not isinstance(kind, str) or not kind:
        normalized["kind"] = _KIND_BY_TYPE_PREFIX.get(node_type.split(".")[0], "control")

    position = normalized.get("position")
    if not isinstance(position, dict):
        normalized["position"] = {"x": 560, "y": 20 + index * 120}
    else:
        normalized["position"] = {
            "x": _read_layout_number(position.get("x"), 560),
            "y": _read_layout_number(position.get("y"), 20 + index * 120),
        }

    if isinstance(normalized.get("delayMs"), str) and str(normalized["delayMs"]).isdigit():
        normalized["delayMs"] = int(normalized["delayMs"])
    if isinstance(normalized.get("timeoutMs"), str) and str(normalized["timeoutMs"]).isdigit():
        normalized["timeoutMs"] = int(normalized["timeoutMs"])
    if node_type in {"browser.open", "browser.tab.open"} and not normalized.get("targetUrl") and normalized.get("url"):
        normalized["targetUrl"] = normalized["url"]
    if node_type == "browser.fetch" and not normalized.get("targetUrl") and normalized.get("url"):
        normalized["targetUrl"] = normalized["url"]
    if node_type == "browser.fill" and not normalized.get("inputValue") and normalized.get("value") is not None:
        normalized["inputValue"] = normalized["value"]
    if node_type == "browser.press" and not normalized.get("inputValue") and normalized.get("key") is not None:
        normalized["inputValue"] = normalized["key"]
    if (
        node_type.startswith(("file.", "excel.", "script."))
        and not normalized.get("path")
        and normalized.get("filePath")
    ):
        normalized["path"] = normalized["filePath"]
    if node_type == "excel.addrow" and not normalized.get("rowData"):
        for alias in ("row", "rows", "content", "value"):
            if normalized.get(alias) is not None:
                normalized["rowData"] = normalized[alias]
                break
    if node_type == "file.write" and not normalized.get("content") and normalized.get("value") is not None:
        normalized["content"] = normalized["value"]

    return normalized


def _normalize_generated_nodes(nodes: list[Any]) -> list[Any]:
    return [_normalize_generated_node(node, index) for index, node in enumerate(nodes)]


def _normalize_generated_edge(edge: Any, index: int) -> Any:
    """规整连线标签和 id，减少不同模型命名差异。"""
    if not isinstance(edge, dict):
        return edge
    normalized = dict(edge)
    label = normalized.get("label")
    if isinstance(label, str):
        label_map = {
            "true": "true", "false": "false",
            "body": "body", "exit": "exit",
            "yes": "true", "no": "false",
            "loop": "body", "each": "body", "iterate": "body",
            "done": "exit", "complete": "exit",
            "是": "true", "否": "false",
            "循环体": "body", "退出": "exit",
            "循环": "body", "每项": "body", "迭代": "body",
            "完成": "exit", "结束": "exit", "跳出": "exit",
        }
        normalized["label"] = label_map.get(label.strip().lower(), label.strip())
    if not normalized.get("id") and normalized.get("source") and normalized.get("target"):
        normalized["id"] = f"e_{uuid4()}"
    return normalized


def _normalize_generated_edges(edges: list[Any]) -> list[Any]:
    return [_normalize_generated_edge(edge, index) for index, edge in enumerate(edges)]


def _node_ref(node: Any) -> dict[str, str] | None:
    """返回面向用户的节点引用，避免助手只输出 n12 这类机器 ID。"""
    if not isinstance(node, dict):
        return None
    node_id = node.get("id")
    if not isinstance(node_id, str) or not node_id:
        return None
    title = str(node.get("title") or node.get("name") or node_id)
    node_type = str(node.get("type") or node.get("actionType") or node.get("kind") or "")
    label = f"{title}（{node_id} · {node_type}）" if node_type else f"{title}（{node_id}）"
    return {"id": node_id, "title": title, "type": node_type, "label": label}


def _default_node_title(node_type: str, node_id: str) -> str:
    if node_type == "start":
        return "开始"
    if node_type == "end":
        return "结束"
    title_map = {
        "browser.open": "打开网页",
        "browser.click": "点击元素",
        "browser.fill": "填写输入",
        "browser.wait": "等待元素",
        "browser.extract": "提取数据",
        "control.condition": "条件判断",
        "control.foreach": "循环遍历",
        "variable.set": "设置变量",
        "file.write": "写入文件",
        "excel.addrow": "追加 Excel 行",
        "excel.save": "保存 Excel",
    }
    return title_map.get(node_type, node_id)


def _build_run_root_cause_hints(
    failed_node_id: str | None,
    all_logs: list[Any],
    failed_node_config: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """从运行日志里提取可验证根因，而不是只回显最后一个异常。"""
    hints: list[dict[str, Any]] = []
    if not failed_node_id:
        return hints

    login_detection_warnings = [
        log for log in all_logs
        if log.node_id and "check" in str(log.node_id).lower()
        and "继续执行" in str(log.message)
        and ("登录" in str(log.message) or "password" in str(log.detail).lower())
    ]
    failed_type = (failed_node_config or {}).get("type")
    if login_detection_warnings and failed_type in {"browser.wait", "browser.click", "browser.extract"}:
        hints.append({
            "type": "login_detection_may_have_skipped_login",
            "confidence": "high",
            "message": (
                "失败前出现登录检测节点超时但继续执行，随后浏览器节点失败。"
                "这通常不是当前 selector 单点失效，而是登录检测把“页面未渲染/登录页未出现”误判为“已登录”。"
            ),
            "next_actions": [
                "检查登录检测节点是否使用 continueOnError + countVariable 默认 0",
                "先用 inspect_page 或截图确认当前真实页面是登录页、应用页还是空白加载页",
                "默认保留 Cookies/localStorage 复用登录态；只有用户要求重置登录或确认过期 token 卡死时，才临时清理存储",
                "登录完成后显式 browser.open 到目标数据页，再等待目标表格",
                "修复前调用 lint_flow 查看 single_navigation_node、登录检测和筛选控件相关警告",
            ],
        })

    return hints


def _find_swallowed_critical_failures(
    all_logs: list[Any],
    failed_node_id: str | None,
    flow_id: str | None,
) -> list[dict[str, Any]]:
    """从运行日志中找出“失败但继续”的前置关键动作。

    不绑定页面或组件库，只看运行事实：关键筛选、提交、导航类动作失败后继续，
    后续等待/抽取节点再失败时，根因通常在前置链路。
    """
    if not failed_node_id or not flow_id:
        return []

    swallowed: list[dict[str, Any]] = []
    for log in all_logs:
        if log.level != "error" or not log.node_id or log.node_id == failed_node_id:
            continue
        message = str(log.message or "")
        if "继续执行" not in message and "continue" not in message.lower():
            continue
        marker = f"{message} {log.detail or ''}".lower()
        if not any(keyword in marker for keyword in (
            "筛选", "过滤", "查询", "搜索", "提交", "确认", "确定",
            "日期", "时间", "状态", "进度", "下拉", "多选",
            "filter", "search", "submit", "confirm", "date", "status",
        )):
            continue
        swallowed.append({
            "node_id": log.node_id,
            "message": message,
            "detail": log.detail,
        })
    return swallowed[-6:]


def _parse_runtime_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text[0] not in "[{\"0123456789-tnf":
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def _build_input_variable_defaults(input_variables: list[Any]) -> dict[str, Any]:
    """把流程声明的 input_variables 默认值转换成运行时变量。

    AI 工具的 run_flow 会直接调用 TaskManager；如果不在这里合并默认值，
    静态校验会认为变量已定义，但实际执行器的变量仓库里没有这些值。
    """
    defaults: dict[str, Any] = {}
    for variable in input_variables:
        if isinstance(variable, dict):
            name = variable.get("name")
            value = variable.get("value", variable.get("defaultValue", ""))
        else:
            name = getattr(variable, "name", None)
            value = getattr(variable, "value", "")
        if isinstance(name, str) and name.strip():
            defaults[name.strip()] = _parse_runtime_value(value)
    return defaults


def _find_header_variable(variables: dict[str, Any]) -> list[str] | None:
    for name, value in variables.items():
        lower = name.lower()
        if "header" not in lower and "columns" not in lower:
            continue
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return [str(item) for item in value]
    return None


def _find_table_candidates(variables: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for name, value in variables.items():
        rows = _coerce_table_rows(value)
        if not rows:
            continue
        score = 0
        lower = name.lower()
        if any(keyword in lower for keyword in ("row", "rows", "table", "data", "list")):
            score += 10
        if "header" in lower or "count" in lower:
            score -= 8
        score += min(len(rows), 20)
        candidates.append({"name": name, "rows": rows, "score": score})
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates


def _coerce_table_rows(value: Any) -> list[Any]:
    if isinstance(value, list):
        if value and all(isinstance(item, str) for item in value):
            nested: list[Any] = []
            for item in value:
                parsed = _parse_runtime_value(item)
                if isinstance(parsed, (list, dict)):
                    nested.append(parsed)
            if nested:
                return nested
        return value if all(isinstance(item, (list, dict)) for item in value) else []
    return []


def _check_structured_rows(rows: list[Any], headers: list[str] | None) -> dict[str, Any] | None:
    if not rows:
        return {"issue": "empty_rows", "message": "结果行为空。"}
    if all(isinstance(row, dict) for row in rows):
        mixed_issue = _detect_mixed_ui_rows(rows)
        if mixed_issue is not None:
            return mixed_issue
        return None
    if not all(isinstance(row, list) for row in rows):
        return {"issue": "unstructured_rows", "message": "结果不是 list[dict] 或 list[list]，无法稳定校验字段。"}
    lengths = [len(row) for row in rows if isinstance(row, list)]
    if not lengths:
        return {"issue": "unstructured_rows", "message": "结果没有可识别的行数组。"}
    mixed_issue = _detect_mixed_ui_rows(rows)
    if mixed_issue is not None:
        return mixed_issue
    if headers:
        if max(lengths) > len(headers) * 2:
            return {
                "issue": "whole_table_flattened",
                "message": (
                    f"检测到单行列数 {max(lengths)} 远大于表头列数 {len(headers)}，"
                    "疑似把整张表抽成一个扁平文本数组，而不是逐行抽取。"
                ),
            }
        return None
    if len(rows) == 1 and lengths[0] > 30:
        return {
            "issue": "whole_table_flattened",
            "message": "结果只有 1 行但包含大量单元格，疑似整张表被抽成一个扁平数组。",
        }
    return None


def _detect_mixed_ui_rows(rows: list[Any]) -> dict[str, Any] | None:
    """检测抓取结果是否混入日期面板、分页、按钮等非业务 UI 行。"""
    ui_like_indexes: list[int] = []
    for index, row in enumerate(rows[:80]):
        values: list[str]
        if isinstance(row, dict):
            values = [str(value).strip() for value in row.values()]
        elif isinstance(row, list):
            values = [str(value).strip() for value in row]
        else:
            continue
        compact = [value for value in values if value]
        if not compact:
            continue
        joined = "|".join(compact)
        weekday_set = {"日", "一", "二", "三", "四", "五", "六"}
        if len(compact) == 7 and set(compact) <= weekday_set:
            ui_like_indexes.append(index)
            continue
        numeric_cells = sum(1 for value in compact if re.fullmatch(r"\d{1,2}", value))
        if len(compact) in {6, 7} and numeric_cells >= 5:
            ui_like_indexes.append(index)
            continue
        if any(token in joined for token in ("上一页", "下一页", "确定", "取消", "今天", "清空")):
            ui_like_indexes.append(index)
            continue
    if not ui_like_indexes:
        return None
    return {
        "issue": "mixed_ui_rows",
        "message": (
            "结果中混入了日历、分页、按钮或其他非业务 UI 控件行。"
            "这说明抽取 selector/scope 过宽，虽然有行数据，但不能证明业务筛选和字段约束可信。"
        ),
        "ui_like_row_indexes": ui_like_indexes[:10],
    }


def _row_get(row: Any, headers: list[str] | None, field: str) -> Any:
    if isinstance(row, dict):
        if field in row:
            return row[field]
        normalized = field.strip().lower()
        for key, value in row.items():
            if str(key).strip().lower() == normalized:
                return value
        return None
    if isinstance(row, list) and headers:
        for index, header in enumerate(headers):
            if str(header).strip() == field and index < len(row):
                return row[index]
    return None


def _assert_date_range(
    rows: list[Any],
    headers: list[str] | None,
    field: str,
    start_date: str | None,
    end_date: str | None,
) -> list[dict[str, Any]]:
    from datetime import date

    issues: list[dict[str, Any]] = []
    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None
    bad: list[dict[str, Any]] = []
    missing = 0
    for index, row in enumerate(rows):
        raw = _row_get(row, headers, field)
        if raw is None:
            missing += 1
            continue
        match = re.search(r"\d{4}-\d{2}-\d{2}", str(raw))
        if not match:
            bad.append({"row": index + 1, "value": raw, "reason": "无法解析日期"})
            continue
        current = date.fromisoformat(match.group(0))
        if (start and current < start) or (end and current > end):
            bad.append({"row": index + 1, "value": raw, "reason": "日期超出范围"})
    if missing == len(rows):
        issues.append({"issue": "date_field_missing", "message": f"所有行都找不到日期字段 `{field}`。"})
    elif bad:
        issues.append({
            "issue": "date_range_violation",
            "message": f"字段 `{field}` 存在 {len(bad)} 行不在日期范围内。",
            "examples": bad[:5],
        })
    return issues


def _assert_allowed_values(
    rows: list[Any],
    headers: list[str] | None,
    field: str,
    allowed_values: list[str],
) -> list[dict[str, Any]]:
    allowed = {str(value).strip() for value in allowed_values}
    bad: list[dict[str, Any]] = []
    missing = 0
    for index, row in enumerate(rows):
        raw = _row_get(row, headers, field)
        if raw is None:
            missing += 1
            continue
        if str(raw).strip() not in allowed:
            bad.append({"row": index + 1, "value": raw})
    if missing == len(rows):
        return [{"issue": "enum_field_missing", "message": f"所有行都找不到枚举字段 `{field}`。"}]
    if bad:
        return [{
            "issue": "enum_value_violation",
            "message": f"字段 `{field}` 存在 {len(bad)} 行不属于允许值 {sorted(allowed)}。",
            "examples": bad[:5],
        }]
    return []


def _guess_date_field(headers: list[str] | None, rows: list[Any]) -> str | None:
    if not headers:
        return None
    preferred = [header for header in headers if any(keyword in str(header) for keyword in ("日期", "时间", "date", "time"))]
    for header in preferred + headers:
        values = [_row_get(row, headers, str(header)) for row in rows[:10]]
        parseable = sum(1 for value in values if value is not None and re.search(r"\d{4}-\d{2}-\d{2}", str(value)))
        if parseable:
            return str(header)
    return None


def _guess_enum_field(headers: list[str] | None, rows: list[Any], allowed_values: list[str] | None) -> str | None:
    if not headers or not allowed_values:
        return None
    allowed = {value.strip() for value in allowed_values}
    best: tuple[int, str] | None = None
    for header in headers:
        values = [_row_get(row, headers, str(header)) for row in rows[:20]]
        hits = sum(1 for value in values if str(value).strip() in allowed)
        if hits and (best is None or hits > best[0]):
            best = (hits, str(header))
    return best[1] if best else None


def _infer_constraints_from_requirement(requirement_text: str) -> dict[str, Any]:
    """从用户原始需求里提取通用约束，不绑定任何页面或字段名。"""
    text = requirement_text.strip()
    if not text:
        return {}
    inferred: dict[str, Any] = {}
    dates = re.findall(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})", text)
    if dates:
        normalized = [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in dates]
        inferred["start_date"] = normalized[0]
        if len(normalized) > 1:
            inferred["end_date"] = normalized[1]
        elif "今天" in text or "今日" in text:
            from datetime import datetime
            inferred["end_date"] = datetime.now().strftime("%Y-%m-%d")

    enum_match = re.search(r"(?:多选|状态|进度|类型|类别|分类)[：:]\s*([^，。,；;\n]+)", text)
    if enum_match:
        raw = enum_match.group(1)
        raw = re.sub(r"[（(]\s*多选\s*[）)]", "", raw)
        values = [part.strip() for part in re.split(r"[/、,，|]", raw) if part.strip()]
        if len(values) >= 2:
            inferred["allowed_values"] = values
    return inferred


def _build_quality_repair_plan(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    issue_names = {str(issue.get("issue", "")) for issue in issues}
    if any("whole_table_flattened" in name or "table_extract_selector_targets_container" in name for name in issue_names):
        plan.append({
            "action": "fix_table_extraction_selector",
            "reason": "输出不是按行结构化表格，通常是 extract selector 指向表格容器而不是数据行。",
            "steps": [
                "调用 get_flow 找到 browser.extract 表格节点。",
                "将 selector 改为真实数据行选择器；标准表格优先 tbody tr，Element UI/Ant Design 从 inspect_page/page_layout 中找行 class。",
                "保持 extractMode='table'，补充 outputVariable 和 countVariable。",
                "重新运行后再次调用 assert_run_output。",
            ],
        })
    if any("date_range_violation" in name for name in issue_names):
        plan.append({
            "action": "verify_date_filter_applied",
            "reason": "输出数据包含日期范围外的记录，说明筛选条件填入 UI 但未真实生效（组件内部状态未更新）。",
            "steps": [
                "**禁止只改日期 selector 后再跑一遍**——这只会重复得到未生效筛选的结果。",
                "上方 issue.examples 已包含实际越界的行和日期值（无需再调 get_run_output），直接判断筛选是否真实生效。",
                "调用 get_flow 找到日期筛选相关节点；如果存在 browser.fill 日期输入框，或日期 click 节点 selector 过宽，必须重建日期链路。",
                "调用 inspect_page(scope_selector=筛选区域容器)。**若返回 date_controls[].interaction_recipe，直接按 steps 和对应 selector 重建节点，无需参考 n14-n17 的具体 selector。若 date_controls 为空**，再按 n14-n17 四段式作为 Element UI 兜底（selector 来自 inputs 字段）。",
                "若是 Element UI/Ant Design DatePicker：日期单元格 selector 必须排除 prev-month/next-month；不能直接 fill 输入框。",
                "若控件接受直接 fill：fill 后必须触发一次 change/input/blur 事件（用 browser.click 点击查询按钮或按 Enter），再等待表格刷新。",
                "在查询按钮点击后增加 browser.wait 等待表格数据更新（waitForSelector 或 delayMs 2000），再执行 extract。",
                "重新运行后调用 assert_run_output，用 start_date/end_date 参数确认第一行和最后一行日期均在范围内。",
            ],
        })
    if any("enum_value_violation" in name for name in issue_names):
        plan.append({
            "action": "filter_header_rows_and_fix_enum_extraction",
            "reason": "输出中出现枚举字段值等于字段名（表头行被混入数据），或枚举值不在允许列表中。",
            "steps": [
                "上方 issue.examples 已包含实际不合法的行和枚举值，直接据此判断是表头混入还是枚举筛选未生效（无需再调 get_run_output）。",
                "首先确认是否混入了表头行：若 issue.examples 中行值等于字段名（如 '项目进度'），则 selector 覆盖了 thead。",
                "修复表头混入：将 selector 改为 tbody tr 或 tbody tr:not(:first-child)；"
                "若已是 tbody tr 仍出现表头，改用 extractMode='table'（自动过滤表头）。",
                "若是枚举筛选未生效（数据含'项目通过'以外的枚举值）：检查下拉筛选控件是否真实触发了查询——"
                "点击下拉选项后必须点击查询/确认按钮，否则前端只是选中了选项但未重新拉数据。",
                "重新运行后调用 assert_run_output 并传入 allowed_values 参数，确认枚举列所有值均合法。",
            ],
        })
    if any("mixed_ui_rows" in name for name in issue_names):
        plan.append({
            "action": "narrow_extraction_scope",
            "reason": "输出中混入日期面板、分页、按钮等非业务 UI 行，说明抽取范围过宽。",
            "steps": [
                "调用 inspect_page 查看业务数据区域与浮层/筛选控件的 DOM 边界。",
                "将抽取 selector 或 scope 收窄到业务数据容器内的真实数据项/数据行。",
                "避免使用 tbody tr、[role=row] 等全页面宽泛 selector，除非已经限定父容器。",
                "重新运行后调用 assert_run_output，确认 sample_rows 不再包含星期、日历数字、分页或按钮文本。",
            ],
        })
    if any("date_range_fill_may_not_update_model" in name for name in issue_names):
        plan.append({
            "action": "fix_filter_widget_interaction",
            "reason": "筛选控件使用 fill 等高风险交互，可能 UI 显示有值但组件内部模型未更新。",
            "steps": [
                "调用 get_flow 找到日期 fill 节点及其前后节点，准备替换为点击式日期链路。",
                "调用 inspect_page(scope_selector=筛选区域容器)。**若返回 date_controls[].interaction_recipe，直接按 steps 和对应 selector 替换 fill 节点，无需参考 n14-n17 的具体 selector。若 date_controls 为空**，按 n14-n17 四段式（触发 → 开始日期 → 结束日期 → 确定）作为 Element UI 兜底。",
                "优先使用组件真实交互：打开面板、点击选项/日期、点击确定或查询；日期单元格 selector 排除 prev/next month。",
                "增加 includeInResult=false 的轻量校验节点或依赖 assert_run_output 校验最终数据。",
                "不要只增加 delayMs 或重复 fill 同一输入框。",
            ],
        })
    if any("date_trigger_selector_too_broad" in name for name in issue_names):
        plan.append({
            "action": "narrow_date_trigger_selector",
            "reason": "日期触发 selector 过宽，可能打开错误控件或错误日期面板，导致后续日期点击链路偏移。",
            "steps": [
                "调用 inspect_page(scope_selector=筛选区域容器)。**若返回 date_controls[].interaction_recipe，直接用 recipe.trigger 替换当前过宽的触发节点 selector，其余 selector 也按 recipe 更新。若 date_controls 为空**，从 inputs 字段选取唯一精确输入框替换，不要使用逗号候选、`placeholder*=` 或 `first-of-type`。",
                "修复后运行并调用 assert_run_output(start_date/end_date)，确认输出日期范围真实生效。",
            ],
        })
    if any("critical_action_continue_on_error" in name for name in issue_names):
        plan.append({
            "action": "stop_swallowing_critical_action_failures",
            "reason": "关键筛选/提交/导航动作失败后继续执行，会把根因伪装成后续等待或抽取失败。",
            "steps": [
                "调用 lint_flow 找到 issue=critical_action_continue_on_error 的节点。",
                "移除关键业务动作上的 continueOnError；仅保留在可选弹窗、登录检测、Cookie 横幅等可缺失节点上。",
                "若确实需要容错，必须在后续增加可验证校验节点，并让校验失败中断流程。",
                "重新运行失败时先看 get_run_error.swallowed_critical_failures，再决定修复前置动作还是末端抽取。",
            ],
        })
    if any("constraint_not_verifiable" in name or "field_missing" in name for name in issue_names):
        plan.append({
            "action": "make_output_verifiable",
            "reason": "用户约束无法在输出中定位字段，说明抽取结构或字段命名不足以验证业务正确性。",
            "steps": [
                "确保表头和数据行被结构化输出；必要时单独抽取表头并设置 includeInResult=false。",
                "使用 table 模式输出 list[list] 或 list[dict]，避免纯文本拼接。",
                "重新运行 assert_run_output，让工具自动从表头/行值匹配约束字段。",
            ],
        })
    if not plan and issues:
        plan.append({
            "action": "inspect_and_repair_flow_structure",
            "reason": "运行质量审计失败，但没有匹配到专门修复模板。",
            "steps": [
                "调用 get_flow 和 lint_flow 查看结构风险。",
                "调用 get_run_output 查看实际变量形态。",
                "根据 issues 修复最靠前的结构性问题后重新运行。",
            ],
        })
    return plan


# ─── Tool JSON Schemas (OpenAI tool format) ───────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lint_flow",
            "description": (
                "对流程进行全面静态质量检查（与 AI 模型无关的程序化扫描），发现以下问题并返回结构化 findings：\n"
                "  • 孤儿节点（无法从起点到达，运行时被跳过）\n"
                "  • foreach 节点缺少 body/exit 标签出边（循环逻辑断路）\n"
                "  • condition 节点缺少 true/false 分支边（分支逻辑断路）\n"
                "  • browser.extract 缺少 outputVariable 或 extractMode（结果丢失）\n"
                "  • http.request 缺少 outputVariable（响应无法被引用）\n"
                "  • variable.input 误用于账号密码等凭据字段（每次运行暂停等手动输入）\n"
                "  • 输出文件路径无时间戳（覆盖上次结果）\n"
                "  • browser.*/select 等节点缺少 selector（运行时崩溃）\n"
                "每条 finding 包含 severity（error/warn）、node_id、issue 类型、message 和 fix 建议。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string", "description": "要检查的流程 ID"}
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_node_types",
            "description": "列出所有可用的 RPA 节点类型、关键字段及其输出变量字段，在构建或修改流程前调用以了解能力边界。",
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_flow",
            "description": "获取流程完整结构（节点列表、连线、变量配置），修改前必须先调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string", "description": "流程 ID"}
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_flow",
            "description": (
                "扫描流程的变量依赖关系，返回：\n"
                "  • defined_variables — 所有已定义变量（含输入变量和运行时内置）\n"
                "  • issues — 每个引用了未定义变量的节点及缺失变量名\n"
                "  • is_valid — 无问题时为 true"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string"}
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_flows",
            "description": "列出所有已有流程（id、名称、状态）。",
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_flow",
            "description": (
                "创建**全新**的 RPA 流程，返回新流程 ID。\n"
                "⚠️ 仅在用户明确要求创建新流程时调用。已有流程需修改时用 update_flow，不要重复创建同名流程。\n"
                "⚠️ 调用前确认已了解目标 URL、是否需要登录、要提取的内容、输出格式。信息不完整时先提问。\n"
                "节点格式：type 为点分格式（browser.open）；所有配置字段平铺在根层，不嵌套在 config 中；\n"
                "必填公共字段：id、type、title、kind、status(pending)、position({x,y})、description。\n"
                "连线 id 格式：e_{source}_{target}。foreach 出边必须加 label：body / exit。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "流程名称"},
                    "description": {"type": "string", "description": "流程描述"},
                    "nodes": {
                        "type": "array",
                        "description": "节点列表",
                        "items": {"type": "object"},
                    },
                    "edges": {
                        "type": "array",
                        "description": "连线列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "source": {"type": "string"},
                                "target": {"type": "string"},
                            },
                            "required": ["source", "target"],
                        },
                    },
                    "input_variables": {
                        "type": "array",
                        "description": (
                            "流程输入变量声明。每项字段：\n"
                            "  name: 变量名（英文）\n"
                            "  type: 类型，必须是以下之一（区分大小写）：String | Integer | Boolean | List | Dict\n"
                            "  defaultValue: 默认值字符串（可为空字符串）\n"
                            "  category: 可选，flow（默认）| credential（账号/密码用这个）\n"
                            "  sensitive: 可选布尔，密码类变量设为 true\n"
                            "示例：{\"name\":\"username\",\"type\":\"String\",\"defaultValue\":\"\",\"category\":\"credential\"}\n"
                            "示例：{\"name\":\"password\",\"type\":\"String\",\"defaultValue\":\"\",\"category\":\"credential\",\"sensitive\":true}"
                        ),
                        "items": {"type": "object"},
                    },
                },
                "required": ["name", "nodes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_flow",
            "description": (
                "直接修改现有流程的节点或连线并立即写入，无需用户确认。\n"
                "适用场景：增删节点、调整连线、批量修改多个节点。\n"
                "系统会自动检测并清理因插入节点导致的旧边冲突。\n"
                "调用前先通过 get_flow 了解当前结构（包括 edges）。修改后若涉及变量引用变化，也应调用 validate_flow 验证。\n\n"
                "【add_nodes vs update_nodes 区别（必须严格区分）】\n"
                "  • add_nodes：只用于添加全新节点。若 id 与流程中已有节点冲突，服务器会报错拒绝。\n"
                "    ⚠️ 'start'、'end' 等已存在的节点绝不能放入 add_nodes——必须用 update_nodes 修改\n"
                "  • update_nodes：修改已存在节点的字段，每项含 {id, patch}，patch 只写要改的字段\n\n"
                "【连线管理规则】\n"
                "  add_edges 的 source/target 只能引用已存在的节点或本次 add_nodes 同时新建的节点 id——"
                "严禁连到尚未创建的节点。删除某节点的入边时，必须同时补上新的入边，"
                "否则其下游分支会变成不可达孤儿节点（系统会返回 connectivity_warning，你必须继续补连）。\n"
                "foreach 节点出边必须加 label：循环体边 label='body'，循环后边 label='exit'。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string"},
                    "add_nodes": {
                        "type": "array",
                        "description": "新增节点列表",
                        "items": {"type": "object"},
                    },
                    "update_nodes": {
                        "type": "array",
                        "description": "修改节点，每项包含 id 和 patch 字段",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "patch": {"type": "object"},
                            },
                            "required": ["id", "patch"],
                        },
                    },
                    "remove_node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "删除的节点 ID 列表",
                    },
                    "add_edges": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "新增连线",
                    },
                    "remove_edge_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "删除的连线 ID 列表",
                    },
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_flow",
            "description": (
                "运行指定流程并等待执行完成（内部自动轮询，最长等待 90 秒），"
                "直接返回最终 status（success / error / stopped / timeout）和 task_id。"
                "success → 调用 get_run_output；error → 调用 get_run_error；"
                "timeout 且含 variable.input → 流程等待用户输入，禁止重新调用 run_flow。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string"},
                    "variables": {
                        "type": "object",
                        "description": "运行时变量覆盖（键值对）",
                    },
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_run_status",
            "description": "手动查询运行任务的状态（running/success/error）和进度百分比。通常无需调用，run_flow 已等待完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"}
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_run_error",
            "description": (
                "获取运行失败的完整诊断：failed_node_id、run_error、error_logs、failed_node_config、"
                "last_browser_url、inspect_hint（selector 超时时存在）。\n"
                "仅用于流程已启动后的运行时错误；运行前错误（'引用了未定义变量'等）用 validate_flow。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"}
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_node_fix",
            "description": (
                "直接修改单个节点的配置字段（selector、outputVariable、path 等），立即写入，无需用户确认。\n"
                "⚠️ 写入后不会出现任何确认按钮——修复完成后直接告知用户结果，不要让用户'点击应用变更'。\n"
                "适用场景：修复单个节点的字段错误（如补全 path、修正 selector）。\n"
                "多节点结构性变更（增删节点、调整连线）请改用 update_flow。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string"},
                    "node_id": {"type": "string"},
                    "config_patch": {
                        "type": "object",
                        "description": "需要更新的配置字段键值对",
                    },
                },
                "required": ["flow_id", "node_id", "config_patch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_flow",
            "description": "将流程发布为 active 状态，使其可被调度执行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string"}
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_run_output",
            "description": (
                "获取已完成任务的输出结果：输出变量快照和采集产物列表。\n"
                "在 get_run_status 返回 status=success 后调用，用于向用户汇报实际运行结果。\n"
                "返回：variables（变量名→值）、artifacts（产物文件名列表）、summary（简要描述）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"}
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assert_run_output",
            "description": (
                "对已成功运行的流程做通用质量审计。注意：run_flow status=success 只代表节点未报错，"
                "不代表流程结构、筛选链路、抽取形态和输出内容可信。抓取/筛选/导出类流程在 get_run_output 后必须调用本工具。\n\n"
                "本工具不是针对某个页面的校验器，而是模型无关的质量闸门：\n"
                "  • 结合流程 lint 发现高风险结构问题（如日期 fill、表格未 table mode、下拉关闭错误）\n"
                "  • 自动识别输出变量里的表格候选，检查是否按行结构化，而不是整张表拼成一个文本数组\n"
                "  • 可选使用 requirement_text 辅助推断用户约束；也可传入通用显式约束\n"
                "审计失败时必须继续诊断和修复流程，禁止向用户报告“已成功完成”。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "要检查的运行任务 ID"},
                    "requirement_text": {
                        "type": "string",
                        "description": "用户原始需求文本，可选。工具会从中辅助推断日期、枚举、数量等约束，但不会绑定具体页面。",
                    },
                    "min_rows": {"type": "integer", "description": "最少结果行数，可选"},
                    "max_rows": {"type": "integer", "description": "最多结果行数，可选"},
                    "date_field": {"type": "string", "description": "日期字段名或列名，可选；未知时可不传，由 AI 根据输出/需求继续诊断"},
                    "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD，可选"},
                    "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD，可选"},
                    "enum_field": {"type": "string", "description": "枚举字段名或列名，可选；未知时可不传，由 AI 根据输出/需求继续诊断"},
                    "allowed_values": {
                        "type": "array",
                        "description": "枚举字段允许值",
                        "items": {"type": "string"},
                    },
                    "require_structured_rows": {
                        "type": "boolean",
                        "description": "是否要求结果是按行结构化表格，默认 true",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_page",
            "description": (
                "用 Playwright 访问指定 URL，提取页面上的交互元素（输入框、按钮、菜单项、表格头、下拉框等），"
                "返回结构化文本，无需图片分析即可获取精确 CSS 选择器。\n\n"
                "**何时使用**：\n"
                "• 构建流程前不确定选择器（尤其是 Element UI / Ant Design 等组件库）\n"
                "• 使用不支持图像分析的模型（如 DeepSeek），无法解读截图时\n"
                "• 登录后需要检查目标页面的表单/菜单结构\n\n"
                "**返回字段**：\n"
                "• inputs / buttons / selects：表单字段和按钮（含精确 selector）\n"
                "• links：页面所有有文字的链接（text/href/selector/cls）——AI 自行判断哪些是导航/操作入口\n"
                "• tables：含表头的表格（headers/selector/cls/row_selector）；"
                "**row_selector 已自动收窄到最近业务容器作用域，直接用于 browser.extract 的 selector 字段**\n"
                "• visible_options：当前已展开下拉弹层中的选项（仅在弹层打开时有值）\n"
                "• page_classes：页面上所有实际 CSS class（最多120个），用于识别真实框架/命名规律\n"
                "• page_layout：body 顶层结构元素数组（tag/cls/role/id/aria_label/html），动态反映页面实际布局——"
                "当 links/tables 为空时必须查看此字段的 html 片段以获取真实类名\n\n"
                "**注意**：此工具会使用持久化浏览器 Profile（含登录 Cookie），"
                "因此访问登录后的页面时无需再走登录流程。\n"
                "如有正在运行的任务占用浏览器，需等任务完成后再调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要检查的页面 URL",
                    },
                    "wait_selector": {
                        "type": "string",
                        "description": (
                            "等待该 CSS 选择器出现后再提取（强烈推荐用于 Vue/React 等 SPA 页面）。"
                            "若上次调用返回 warning（元素为空），必须在此次重试中指定 wait_selector，"
                            "如 'nav, table, [role=grid], [role=navigation], main'。"
                        ),
                    },
                    "scope_selector": {
                        "type": "string",
                        "description": "只在该选择器范围内提取元素，适合聚焦表单区域或弹窗（如 .search-form、.el-dialog）",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_run_logs",
            "description": "获取运行任务的日志，可按节点 ID 或日志级别过滤。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "node_id": {
                        "type": "string",
                        "description": "仅返回该节点的日志（可选）",
                    },
                    "level": {
                        "type": "string",
                        "enum": ["error", "warn", "info", "debug"],
                        "description": "日志级别过滤（可选）",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
]

# ─── Node type catalog ─────────────────────────────────────────────────────────
# type 字段为点分字符串，所有配置字段平铺在节点根层（不嵌套在 config 对象中）。
# output_var_field: 该节点用于定义输出变量的字段名（填入变量名后该变量在后续节点可用）

NODE_TYPE_CATALOG: list[dict[str, str]] = [
    # ── 浏览器操作 (kind: browser) ─────────────────────────────────────────────
    {
        "type": "browser.open",
        "key_fields": "targetUrl",
        "output_var_field": "（无输出变量）",
        "description": (
            "打开/跳转网页，需 targetUrl 字段（兼容历史 url 字段，但新流程必须使用 targetUrl）。"
            "可选字段：delayMs（导航完成后额外等待，SPA hash 路由建议 2000-3000）；"
            "clearStorage: true（导航后清除该域名的 localStorage/sessionStorage，然后自动 reload——"
            "**仅用于诊断或用户明确要求重置登录态**：默认不要设置。"
            "带登录态检测的流程应保留 Cookies/localStorage 复用已登录会话；"
            "只有 inspect_page/运行日志证明过期 token 导致 SPA 卡死时，才临时启用）；"
            "clearCookies: true（清除该浏览器上下文的所有 Cookie，与 clearStorage 可同时使用）"
        ),
    },
    {
        "type": "browser.click",
        "key_fields": "selector",
        "output_var_field": "（无输出变量）",
        "description": "点击页面元素，需 selector。点击可选元素（弹窗关闭按钮、Cookie 提示等）时必须加 continueOnError: true，防止元素不存在时流程中断。",
    },
    {
        "type": "browser.fill",
        "key_fields": "selector, inputValue",
        "output_var_field": "（无输出变量）",
        "description": "向输入框填写文本，inputValue 可用 ${var.xxx} 引用变量",
    },
    {
        "type": "browser.extract",
        "key_fields": "selector, extractMode",
        "output_var_field": "outputVariable（必填，提取结果存入该变量）",
        "description": (
            "提取元素到变量，extractMode: text|html|attribute|count|table。"
            "table 模式（selector 指向 tbody tr 数据行）会自动识别表头并把每行存为 {列名:值} 对象、"
            "自动剔除框架影子残行，可直接 file.write/excel 导出干净结构化数据，无需额外的表头节点或清洗脚本。"
        ),
    },
    {
        "type": "browser.fetch",
        "key_fields": "targetUrl, selector, extractMode",
        "output_var_field": "outputVariable（提取结果存入该变量）",
        "description": (
            "轻量抓取：直接请求 targetUrl 并用 selector 提取内容，无需打开持久浏览器会话，"
            "适合静态/服务端渲染页面的一次性抓取。extractMode: text|html|attribute。"
            "注意地址字段是 targetUrl（不是 url）。需要点击/填表等交互时改用 browser.open + browser.* 序列"
        ),
    },
    {
        "type": "browser.wait",
        "key_fields": "selector",
        "output_var_field": "（无输出变量）",
        "description": "等待元素出现，需 selector。等待可选元素时加 continueOnError: true，超时不中断流程。",
    },
    {
        "type": "browser.scroll",
        "key_fields": "direction, distance",
        "output_var_field": "（无输出变量）",
        "description": "滚动页面，direction: down|up|left|right",
    },
    {
        "type": "browser.select",
        "key_fields": "selector, inputValue",
        "output_var_field": "（无输出变量）",
        "description": "选择下拉框选项，inputValue 为选项的 value 属性",
    },
    {
        "type": "browser.screenshot",
        "key_fields": "",
        "output_var_field": "outputVariable（截图 base64 存入该变量）",
        "description": "截取当前页面截图",
    },
    {
        "type": "browser.press",
        "key_fields": "selector, inputValue",
        "output_var_field": "（无输出变量）",
        "description": "模拟按键，inputValue 填写按键名如 Enter/Tab/Escape",
    },
    {
        "type": "browser.dismiss",
        "key_fields": "",
        "output_var_field": "（无输出变量）",
        "description": "关闭浏览器原生 alert/confirm/prompt 弹框。必须加 continueOnError: true，因为弹框不一定出现。",
    },
    {
        "type": "browser.check",
        "key_fields": "selector, checked",
        "output_var_field": "（无输出变量）",
        "description": "勾选/取消复选框，checked: true|false",
    },
    {
        "type": "browser.paginateNext",
        "key_fields": "selector, targetSelector",
        "output_var_field": "outputVariable（翻页后提取到的内容列表，可选）",
        "description": "点击翻页按钮（selector）并等待加载，再用 targetSelector 提取本页内容；结果列表存入 outputVariable",
    },
    {
        "type": "browser.clickLoadMore",
        "key_fields": "selector, targetSelector",
        "output_var_field": "outputVariable（累计提取到的全部内容列表，可选）",
        "description": "反复点击“加载更多”按钮（selector）直到无更多，期间用 targetSelector 累计提取内容；适合无限滚动/瀑布流页面，结果存入 outputVariable",
    },
    {
        "type": "browser.drag",
        "key_fields": "selector, targetSelector",
        "output_var_field": "（无输出变量）",
        "description": "拖拽元素，selector 为被拖拽元素，targetSelector 为放置目标位置",
    },
    {
        "type": "browser.tab.open",
        "key_fields": "targetUrl",
        "output_var_field": "（无输出变量）",
        "description": "打开新标签页，targetUrl 为新标签页要加载的地址（可留空仅开空白页）",
    },
    {
        "type": "browser.tab.switch",
        "key_fields": "index",
        "output_var_field": "（无输出变量）",
        "description": "切换到指定标签页，index 为从 0 起的标签页序号（注意字段名是 index，不是 tabIndex）",
    },
    {
        "type": "browser.tab.close",
        "key_fields": "",
        "output_var_field": "（无输出变量）",
        "description": "关闭当前标签页",
    },
    {
        "type": "browser.hover",
        "key_fields": "selector",
        "output_var_field": "（无输出变量）",
        "description": (
            "鼠标悬停在元素上（hover），用于触发悬停展开的下拉菜单或二级菜单（如 Element UI NavMenu）。"
            "selector 同 browser.click。可选 delayMs（悬停后等待毫秒，默认 0）。"
            "悬停后再用 browser.click 点击展开的子菜单项。"
        ),
    },
    # ── 流程控制 (kind: control) ───────────────────────────────────────────────
    {
        "type": "control.foreach",
        "key_fields": "itemsVariable, itemVariable",
        "output_var_field": "itemVariable（循环体内可用的当前项变量名）",
        "description": (
            "遍历列表变量，itemsVariable 为源列表变量名，itemVariable 为当前项变量名。"
            "出边必须加 label：循环体出边 label='body'，循环后出边 label='exit'。"
            "循环体内节点用普通边顺序连接，不需要边回到 foreach 节点。"
        ),
    },
    {
        "type": "control.condition",
        "key_fields": "inputValue",
        "output_var_field": "（无输出变量）",
        "description": "条件分支（if/else），inputValue 为布尔表达式，可用 ${var.xxx}",
    },
    {
        "type": "control.delay",
        "key_fields": "delayMs",
        "output_var_field": "（无输出变量）",
        "description": "延时等待，delayMs 为毫秒数",
    },
    {
        "type": "control.retry",
        "key_fields": "retryCount, delayMs",
        "output_var_field": "（无输出变量）",
        "description": "自动重试，retryCount 次数，delayMs 间隔",
    },
    {
        "type": "control.try",
        "key_fields": "errorVariable",
        "output_var_field": "errorVariable（异常信息字符串存入该变量，必填）",
        "description": "异常捕获块，catch 分支可读 errorVariable",
    },
    {
        "type": "control.break",
        "key_fields": "",
        "output_var_field": "（无输出变量）",
        "description": "跳出当前循环",
    },
    {
        "type": "control.noop",
        "key_fields": "",
        "output_var_field": "（无输出变量）",
        "description": "空操作占位节点，不执行任何动作，用作分支汇合点或流程占位",
    },
    {
        "type": "control.subprocess",
        "key_fields": "flowId",
        "output_var_field": "（无输出变量）",
        "description": "调用子流程，flowId 为目标流程 ID",
    },
    # ── 变量 / 消息 (kind: variable) ──────────────────────────────────────────
    {
        "type": "variable.set",
        "key_fields": "variableName, value",
        "output_var_field": "variableName（被设置的变量名，后续节点可引用）",
        "description": "设置/修改变量，variableName 为目标变量名，value 可含 ${var.xxx}",
    },
    {
        "type": "variable.get",
        "key_fields": "variableName",
        "output_var_field": "outputVariable（读取值存入该变量）",
        "description": "读取变量值到另一个变量",
    },
    {
        "type": "variable.input",
        "key_fields": "message（提示文字）, variableName（存储变量名，必填）",
        "output_var_field": "variableName（用户输入存入该变量，必填；注意：不是 outputVariable）",
        "description": "弹出输入框等待用户输入，message 为提示文字，variableName 为存储变量名",
    },
    {
        "type": "variable.log",
        "key_fields": "message",
        "output_var_field": "（无输出变量）",
        "description": "记录日志，message 可含 ${var.xxx}",
    },
    {
        "type": "variable.notify",
        "key_fields": "channel, message",
        "output_var_field": "outputVariable（通知发送状态，可选）",
        "description": "发送通知，channel 为通知渠道名，message 可含 ${var.xxx}",
    },
    {
        "type": "variable.clipboard",
        "key_fields": "content",
        "output_var_field": "outputVariable（剪贴板内容，可选）",
        "description": "读取或写入剪贴板，content 填写时为写入；留空时读取当前剪贴板内容到 outputVariable",
    },
    # ── 数据处理 (kind: data) ──────────────────────────────────────────────────
    {
        "type": "data.json.parse",
        "key_fields": "inputVariable",
        "output_var_field": "outputVariable（解析结果存入该变量，必填）",
        "description": "解析 JSON 字符串为对象，inputVariable 为含 JSON 的变量名",
    },
    {
        "type": "data.string.transform",
        "key_fields": "inputVariable, operation",
        "output_var_field": "outputVariable（转换结果存入该变量，必填）",
        "description": "字符串操作：trim/upper/lower/replace/split 等",
    },
    {
        "type": "data.regex.match",
        "key_fields": "inputVariable, pattern",
        "output_var_field": "outputVariable（匹配结果列表存入该变量，必填）",
        "description": "正则匹配，inputVariable 为源变量名，pattern 为正则表达式",
    },
    {
        "type": "data.list.map",
        "key_fields": "inputVariable, operation",
        "output_var_field": "outputVariable（处理结果存入该变量，必填）",
        "description": "列表处理，operation: compact（去空）| unique（去重）| join（合并为字符串，需 delimiter）",
    },
    {
        "type": "data.math.compute",
        "key_fields": "left, right, operator",
        "output_var_field": "outputVariable（计算结果存入该变量，必填）",
        "description": "数学计算，left/right 可用 ${var.xxx}，operator: add|subtract|multiply|divide|mod",
    },
    {
        "type": "data.convert",
        "key_fields": "inputValue, operation",
        "output_var_field": "outputVariable（转换结果存入该变量，必填）",
        "description": "类型转换，operation: to_int | to_float | to_bool | to_str | to_list | to_json；inputValue 可含 ${var.xxx}",
    },
    {
        "type": "data.encrypt",
        "key_fields": "inputValue, operation",
        "output_var_field": "outputVariable（结果存入该变量，必填）",
        "description": (
            "哈希/加密/编解码，operation: md5 | sha256 | sha1 | base64_encode | base64_decode | aes_encrypt | aes_decrypt；"
            "AES 操作需在 pattern 字段填写密钥字符串（缺省使用内置默认密钥）"
        ),
    },
    # ── HTTP 请求 (kind: http) ─────────────────────────────────────────────────
    {
        "type": "http.request",
        "key_fields": "url, method",
        "output_var_field": "outputVariable（响应体存入该变量，必填）",
        "description": "发起 HTTP 请求，method: GET|POST|PUT|DELETE",
    },
    # ── 脚本 (kind: script) ────────────────────────────────────────────────────
    {
        "type": "script.python",
        "key_fields": "code（内联代码，默认）或 path+code（本地文件模式，首次运行自动生成）",
        "output_var_field": "outputVariable（脚本 stdout 存入该变量，可选）",
        "description": (
            "执行 Python 脚本。默认用 code 字段写内联代码；"
            "若用户需要在本地查看/编辑脚本文件，同时填 path（相对路径如 scripts/run.py）和 code（初始内容），"
            "首次运行时自动在工作区生成该文件，后续执行用户修改后的版本。"
            "path 只能是相对路径，工作区根目录为 ~/.easy-rpa/workspace/。"
            "【关键】脚本是独立子进程，流程变量必须从环境变量读取："
            "import json,os; _v=json.loads(os.environ.get('RPA_VARIABLES_JSON','{}')); val=_v.get('变量名','')"
            "——直接写变量名（如 data=my_var）会报 NameError。"
            "【输出文件】产物写到 _v['output_dir'] 下、文件名带 _v['run_timestamp']（如 "
            "os.path.join(_v['output_dir'], 'data_%s.json' % _v['run_timestamp'])），先 os.makedirs(_v['output_dir'], exist_ok=True)。"
        ),
    },
    {
        "type": "script.javascript",
        "key_fields": "code（内联代码，默认）或 path+code（本地文件模式，首次运行自动生成）",
        "output_var_field": "outputVariable（脚本 stdout 存入该变量，可选）",
        "description": (
            "执行 JavaScript 脚本。默认用 code 字段写内联代码；"
            "若用户需要本地可编辑文件，同时填 path（.js 相对路径）和 code（初始内容），首次运行自动生成。"
        ),
    },
    {
        "type": "script.shell",
        "key_fields": "command",
        "output_var_field": "outputVariable（命令 stdout 存入该变量，可选）",
        "description": "执行 shell 命令，command 可含 ${var.xxx}",
    },
    {
        "type": "script.websocket",
        "key_fields": "url, message",
        "output_var_field": "outputVariable（接收到的响应内容存入该变量，可选）",
        "description": "连接 WebSocket，发送 message 后等待一条响应消息，url 需以 ws:// 或 wss:// 开头",
    },
    # ── 文件 / Excel (kind: file / excel) ─────────────────────────────────────
    {
        "type": "file.read",
        "key_fields": "path",
        "output_var_field": "outputVariable（文件内容存入该变量，必填）",
        "description": "读取文件内容到变量，path 为相对工作区或绝对路径",
    },
    {
        "type": "file.write",
        "key_fields": "path, content",
        "output_var_field": "（无输出变量）",
        "description": "将内容写入文件，若目录不存在会自动创建，content 可含 ${var.xxx}",
    },
    {
        "type": "file.copy",
        "key_fields": "path, targetPath",
        "output_var_field": "（无输出变量）",
        "description": "复制文件，path 为源，targetPath 为目标",
    },
    {
        "type": "file.move",
        "key_fields": "path, targetPath",
        "output_var_field": "（无输出变量）",
        "description": "移动/重命名文件",
    },
    {
        "type": "file.delete",
        "key_fields": "path",
        "output_var_field": "（无输出变量）",
        "description": "删除文件",
    },
    {
        "type": "file.list",
        "key_fields": "path",
        "output_var_field": "outputVariable（文件列表存入该变量，必填）",
        "description": "列出目录中的文件，pattern 可过滤（如 *.csv）",
    },
    {
        "type": "file.compress",
        "key_fields": "path, targetPath, operation",
        "output_var_field": "outputVariable（输出文件路径，可选）",
        "description": "压缩或解压文件，operation: compress（默认）| decompress；targetPath 后缀决定格式（.zip / .tar.gz）",
    },
    {
        "type": "file.rename",
        "key_fields": "path, targetPath",
        "output_var_field": "outputVariable（新路径，可选）",
        "description": "重命名或移动文件，path 为源路径，targetPath 为新路径",
    },
    {
        "type": "file.watch",
        "key_fields": "path, pattern",
        "output_var_field": "outputVariable（新增文件列表存入该变量，必填）",
        "description": "监视目录，等待新文件出现后返回文件列表；pattern 为 glob 过滤规则（默认 *），超时则报错",
    },
    {
        "type": "excel.read",
        "key_fields": "path, sheetName",
        "output_var_field": "outputVariable（读取数据存入该变量，必填）",
        "description": "读取 Excel 工作表数据为列表",
    },
    {
        "type": "excel.write",
        "key_fields": "path, sheetName, cellAddress, value",
        "output_var_field": "（无输出变量）",
        "description": "写入 Excel 单元格",
    },
    {
        "type": "excel.addrow",
        "key_fields": "path, sheetName, rowData",
        "output_var_field": "（无输出变量）",
        "description": "向 Excel 追加一行，rowData 为字典或列表",
    },
    {
        "type": "excel.save",
        "key_fields": "path",
        "output_var_field": "（无输出变量）",
        "description": "保存 Excel 文件",
    },
    {
        "type": "excel.deleterow",
        "key_fields": "path, sheetName, rowIndex",
        "output_var_field": "（无输出变量）",
        "description": "删除 Excel 指定行",
    },
    {
        "type": "excel.filter",
        "key_fields": "path, sheetName, filterExpression",
        "output_var_field": "outputVariable（过滤结果存入该变量，必填）",
        "description": "按条件过滤 Excel 行",
    },
]


# ─── Tool Executor ─────────────────────────────────────────────────────────────

class RpaToolExecutor:
    def __init__(self, flow_service: "FlowService", task_manager: "TaskManager") -> None:
        self._flow_service = flow_service
        self._task_manager = task_manager
        # Per-conversation dedup: tracks (flow_id, node_id, patch) hashes to catch
        # repeated identical apply_node_fix calls that the model memory cannot reliably block.
        self._applied_patch_hashes: set[str] = set()
        # Per-conversation quality budget. A run can "succeed" technically while
        # still failing business assertions; keep that evidence available to the
        # next run_flow call so the assistant cannot loop through success →
        # failed audit → blind rerun forever.
        self._quality_failures_by_flow: dict[str, list[dict[str, Any]]] = {}

    async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
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
                return await self._run_flow(**args)
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

    async def _get_flow(self, flow_id: str) -> dict[str, Any]:
        flow = await self._flow_service.get_flow(flow_id)
        if flow is None:
            return {"error": f"流程 {flow_id} 不存在"}
        data = flow.model_dump(mode="json")
        # Strip snapshots (version history) — not needed for flow editing and can be
        # 60k-120k chars, causing both UI lag when expanding the tool card and
        # unnecessary token cost in every LLM context window.
        data.pop("snapshots", None)
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
        """Lay out the canvas from graph topology instead of trusting AI coordinates.

        AI-generated coordinates are treated as untrusted hints. The layout is
        derived from graph structure so create_flow/update_flow always produces a
        readable Studio canvas: longest-path levels keep joins below their
        longest branch, edge labels create stable lanes, and multi-parent joins
        return toward the main lane.
        """
        from collections import defaultdict, deque

        MAIN_X, LANE_STEP_X, ROW_STEP_Y = 560, 360, 130
        START_Y = 20
        NODE_W, NODE_H, GAP = 240, 84, 24

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

        # Cycles or malformed graphs can leave reachable nodes unprocessed. Keep
        # them visible in a deterministic audit chain instead of trusting their
        # original coordinates.
        if len(processed) < len(reachable):
            tail_level = max(level_by_id.values(), default=0) + 1
            for node_id in sorted(reachable - set(processed), key=lambda item: (_read_node_y(node_map[item]), _read_node_x(node_map[item]), item)):
                level_by_id.setdefault(node_id, tail_level)
                lane_by_id.setdefault(node_id, 0)
                tail_level += 1

        # Preserve every disconnected node but push it into an audit lane so the
        # graph stays inspectable while lint still reports unreachable nodes.
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

        # Ensure start/end sentinel nodes are present — safety net in case the AI omits them.
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

        # Normalize layout from graph topology so AI-supplied coordinates cannot
        # create overlapping branches on the Studio canvas.
        self._normalize_layout(nodes, edges)

        definition: dict[str, Any] = {"nodes": nodes, "edges": edges}

        # Build input variable snapshots if provided
        iv_names: list[str] = []
        iv_snapshots: list[dict[str, Any]] = []
        for iv in (input_variables or []):
            iv_name = iv.get("name", "")
            if iv_name:
                iv_names.append(iv_name)
                raw_type = iv.get("type", "String")
                # Normalize type — AI may pass lowercase variants
                _type_map = {"string": "String", "integer": "Integer", "int": "Integer",
                             "boolean": "Boolean", "bool": "Boolean", "list": "List",
                             "array": "List", "dict": "Dict", "object": "Dict"}
                norm_type = _type_map.get(str(raw_type).lower(), raw_type)
                raw_scope = iv.get("scope", "全局")
                norm_scope = raw_scope if raw_scope in ("全局", "循环", "局部") else "全局"
                iv_snapshots.append({
                    "name": iv_name,
                    "type": norm_type,
                    "value": str(iv.get("defaultValue", iv.get("value", ""))),
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

        # Validate variable references in the newly created flow
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
        return result

    async def _update_flow(
        self,
        flow_id: str,
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

        # ── Guard: protect structural anchors ────────────────────────────────
        protected_ids = {"start", "end"} & remove_set
        if protected_ids:
            return {
                "error": f"禁止删除流程锚节点：{', '.join(sorted(protected_ids))}。start/end 是流程入口/出口，删除会使整个流程无法运行。",
                "fix_hint": "如需重构流程，只移动或重连 start/end 的出入边，不要删除节点本身。",
            }

        # ── Normalize edge labels before any processing ───────────────────────
        # Weaker models sometimes emit "Body", "Exit", "True", "False" (capitalized).
        # The executor's branch router uses exact lowercase matches, so normalize here
        # to prevent silently broken condition/foreach branches.
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

        # ── Pre-mutation structural validation ───────────────────────────────
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

        # Apply changes to a working copy of the definition
        definition = _copy.deepcopy(dict(flow.definition))
        nodes: list = [n for n in definition.get("nodes", []) if isinstance(n, dict) and n.get("id") not in remove_set]
        edges: list = [e for e in definition.get("edges", []) if isinstance(e, dict) and e.get("id") not in final_remove_edge_ids]

        # Auto-prune dangling edges left by removed nodes
        surviving_ids = {n["id"] for n in nodes if isinstance(n, dict) and n.get("id")} | new_node_ids
        edges = [e for e in edges if isinstance(e, dict) and e.get("source") in surviving_ids and e.get("target") in surviving_ids]

        # Apply node patches
        patch_map = {u["id"]: u["patch"] for u in (update_nodes or []) if "id" in u and "patch" in u}
        for node in nodes:
            if isinstance(node, dict) and node.get("id") in patch_map:
                node.update(patch_map[node["id"]])

        # Auto-shift existing nodes to avoid y-overlap with newly inserted nodes.
        # Groups new nodes by x-column; for each column, shifts existing nodes that sit
        # at or below the insertion y down by the vertical span the new nodes occupy.
        # Nodes already repositioned by update_nodes patches are left untouched.
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

        # Add new nodes/edges — auto-fill `kind` from action type prefix so the frontend
        # renders the correct node colour even when the AI omits the field.
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

        # Deduplicate edges by (source, target)
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

        # Normalize layout from graph topology on every structural write.
        self._normalize_layout(nodes, edges)

        # ── Pre-write orphan check ────────────────────────────────────────────
        # Compute which nodes are currently reachable vs. which would become
        # unreachable AFTER this change. If the mutation would newly orphan any
        # node that was previously reachable, block the write and return an error.
        # This prevents the AI from accidentally severing the flow during repair.
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

        req = FlowUpdateRequest(definition=definition)
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
        for item in update_nodes or []:
            uid = item.get("id") if isinstance(item, dict) else None
            if isinstance(uid, str):
                ref = final_node_refs.get(uid) or existing_node_refs.get(uid)
                if ref:
                    changed_nodes.append({**ref, "change": "updated"})
        for node_id in remove_node_ids or []:
            ref = existing_node_refs.get(node_id)
            if ref:
                changed_nodes.append({**ref, "change": "removed"})

        result: dict[str, Any] = {
            "status": "applied",
            "flow_id": flow_id,
            "flow_name": flow.name,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }
        if changed_nodes:
            result["changed_nodes"] = changed_nodes
            result["changed_node_labels"] = [node["label"] for node in changed_nodes]
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

        # Connectivity check: nodes unreachable from the entry node will be skipped
        # at runtime. Removing an inbound edge (e.g. swapping a node's predecessor
        # but forgetting to reconnect downstream) silently orphans whole branches —
        # warn so the AI/user reconnects instead of believing the fix succeeded.
        unreachable = _unreachable_node_ids(nodes, edges)
        if unreachable:
            result["connectivity_warning"] = (
                f"以下节点无法从流程起点到达，运行时会被跳过：{', '.join(unreachable)}。"
                "通常是连线缺失或被误删，请补连后再确认完成。"
            )
            result["unreachable_nodes"] = unreachable
        return result

    async def _run_flow(
        self,
        flow_id: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import asyncio
        from app.models.schemas import RunTaskRequest

        flow = await self._flow_service.get_flow(flow_id)
        if flow is None:
            return {"error": f"流程 {flow_id} 不存在"}

        failure_gate = await self._recent_failure_gate(flow_id)
        if failure_gate is not None:
            return failure_gate

        # Pre-check: detect missing run variables before starting the task.
        # Models often call run_flow without passing variables even when input_variables
        # have no defaultValue, resulting in a cryptic "变量未定义" error mid-run.
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
                    "示例：variables={\"username\": \"admin\", \"password\": \"123456\", \"captcha\": \"123456\"}"
                ),
            }

        # Pre-check variable references before starting
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
        )
        task = await self._task_manager.start_task(req)

        # Poll until the task reaches a terminal state (max 90 s, 2 s interval)
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

        has_input_nodes = any(n.get("type") == "variable.input" for n in nodes)
        result: dict[str, Any] = {
            "task_id": task.task_id,
            "status": task.status if task.status in _TERMINAL else "timeout",
            "flow_id": flow_id,
            "progress": task.progress.model_dump(mode="json") if task.progress else {},
        }
        if task.status not in _TERMINAL:
            if has_input_nodes:
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
        """Stop AI-driven repeated runs when recent evidence shows no progress.

        This gate only affects the AI tool loop. Manual UI runs still go through
        the normal API. The goal is to force diagnosis after repeated failures
        instead of letting the assistant burn dozens of executions on the same
        navigation or selector problem.
        """
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
        error_logs = [l for l in all_logs if l.level == "error"]
        warn_logs  = [l for l in all_logs if l.level == "warn"]

        failed_node_id: str | None = None
        for log in reversed(error_logs):
            if log.node_id:
                failed_node_id = log.node_id
                break

        failed_node_config: dict[str, Any] | None = None
        if failed_node_id and task.flow_id:
            flow = await self._flow_service.get_flow(task.flow_id)
            if flow:
                nodes = flow.definition.get("nodes", [])
                failed_node_config = next(
                    (n for n in nodes if isinstance(n, dict) and n.get("id") == failed_node_id),
                    None,
                )

        error_text = task.error or ""
        error_lower = error_text.lower()
        is_selector_error = (
            ("timeout" in error_lower or "locator" in error_lower)
            and ("wait_for_selector" in error_lower or "locator(" in error_lower
                 or "page.fill" in error_lower or "page.click" in error_lower
                 or "page.press" in error_lower or "page.wait" in error_lower)
        )

        # Extract the last URL the browser successfully navigated to
        last_browser_url: str | None = None
        for log in reversed(all_logs):
            detail = log.detail or ""
            if detail.startswith("http://") or detail.startswith("https://"):
                last_browser_url = detail
                break

        result: dict[str, Any] = {
            "task_id": task_id,
            "status": task.status,
            "run_error": task.error,
            "failed_node_id": failed_node_id,
            "failed_node_config": failed_node_config,
            "error_logs": [
                {"message": l.message, "detail": l.detail, "node_id": l.node_id}
                for l in error_logs[-10:]
            ],
            "warn_logs": [
                {"message": l.message, "node_id": l.node_id}
                for l in warn_logs[-5:]
            ],
        }
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
            if selector_count is not None:
                selector_diagnostic = {
                    "kind": "selector_zero_match" if selector_count == 0 else "selector_match_not_actionable",
                    "matched_count": selector_count,
                    "message": (
                        "selector 在当前页面没有命中元素。"
                        if selector_count == 0
                        else "selector 命中了元素，但 Playwright 仍无法完成动作，通常是首个匹配元素隐藏、被遮挡或不稳定。"
                    ),
                }
            elif "element is not visible" in error_lower or "not visible" in error_lower:
                selector_diagnostic = {
                    "kind": "selector_match_hidden_or_not_visible",
                    "matched_count": None,
                    "message": "selector 命中的元素不可见或被隐藏；不要继续只改文案，应过滤可见元素、使用稳定目标 URL，或先展开父级导航/容器。",
                }
            if selector_diagnostic is not None:
                result["selector_diagnostic"] = selector_diagnostic

            url_part = f"，建议 URL：{last_browser_url}" if last_browser_url else ""
            result["inspect_hint"] = (
                f"⚠️ 这是 selector 定位超时。修复前必须先调用 "
                f"inspect_page(url='<当前页 URL>'{url_part}) 检查真实 DOM——"
                "直接猜测修改 selector 后重新运行大概率仍会失败。"
                "截图节点对非视觉模型无效，不要用截图取证。"
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

        return result

    async def _apply_node_fix(
        self,
        flow_id: str,
        node_id: str,
        config_patch: dict[str, Any],
    ) -> dict[str, Any]:
        from app.models.schemas import FlowUpdateRequest

        # ── Executor-level deduplication ─────────────────────────────────────
        # If the same patch has already been applied in this conversation session,
        # return a warning instead of writing again. This is a hard guard against
        # models that ignore the prompt-level "no-repeat" rule — particularly
        # relevant when a weaker model loops on the same selector/delayMs fix.
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
        result: dict[str, Any] = {
            "flow_id": flow_id,
            "node_id": node_id,
            "applied_patch": config_patch,
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

        # Collect output variables from the snapshot (exclude system builtins)
        _SYSTEM_VARS = frozenset({"run_timestamp"})
        variables: dict[str, Any] = {}
        for snap in (task.variables or []):
            if snap.name not in _SYSTEM_VARS:
                val = snap.value
                if isinstance(val, str) and len(val) > 500:
                    val = val[:500] + "…（已截断）"
                variables[snap.name] = val

        # Collect artifact metadata
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
        require_structured_rows: bool = True,
    ) -> dict[str, Any]:
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
                "date_range_fill_may_not_update_model",
                "table_extract_without_table_mode",
                "table_extract_selector_targets_container",
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

        if selected is None:
            issues.append({
                "issue": "no_table_like_output",
                "message": "未找到表格型输出变量。抓取流程应输出按行结构化的表格变量，而不是只保存截图/文本/空产物。",
            })
            if flow is not None:
                self._record_quality_failure(flow.flow_id, task_id, issues)
            return {
                "task_id": task_id,
                "passed": False,
                "issues": issues,
                "candidates": [],
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

        if require_structured_rows:
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

        passed = not issues
        if flow is not None and not passed:
            self._record_quality_failure(flow.flow_id, task_id, issues)

        return {
            "task_id": task_id,
            "passed": passed,
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
            "issues": issues,
            "repair_plan": _build_quality_repair_plan(issues),
            "lint_findings": lint_findings[:12],
            "sample_rows": rows[:3],
            "message": "运行质量审计通过。" if passed else "运行质量审计失败，必须继续诊断并修复流程。",
        }

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
            logs = [l for l in logs if l.node_id == node_id]
        if level:
            logs = [l for l in logs if l.level == level]
        return {
            "task_id": task_id,
            "count": len(logs),
            "logs": [
                {"level": l.level, "message": l.message, "detail": l.detail, "node_id": l.node_id}
                for l in logs[-50:]
            ],
        }

    async def _inspect_page(
        self,
        url: str,
        wait_selector: str | None = None,
        scope_selector: str | None = None,
    ) -> dict[str, Any]:
        """Navigate to a URL with the persistent browser profile and return structured DOM info."""
        # Fail fast if any task is currently running (browser profile is locked)
        running = [
            record for record in self._task_manager._tasks.values()
            if record.snapshot.status == "running"
        ]
        if running:
            return {
                "error": "浏览器正被运行中的任务占用，请等待任务完成后再调用 inspect_page。",
                "running_task_ids": [r.snapshot.task_id for r in running],
            }

        try:
            from playwright.async_api import async_playwright as _async_playwright
        except ModuleNotFoundError:
            return {"error": "未安装 Playwright，请执行 uv pip install playwright"}

        browser_profile = str(storage.resolve_browser_profile_dir())

        # JavaScript that extracts objective page facts — no pre-interpretation of structure.
        # Returns what HTML actually contains; AI decides what it means for the current task.
        _JS_EXTRACT = """(scopeSelector) => {
            const MAX = 60;
            const root = scopeSelector
                ? (document.querySelector(scopeSelector) || document)
                : document;

            function text(el) {
                return (el.innerText || el.textContent || el.value || el.placeholder || '')
                    .trim().replace(/\\s+/g, ' ').slice(0, 80);
            }

            // Stable CSS selector — prefers id/name/placeholder/type, falls back to :has-text()
            function selector(el) {
                if (el.id && !/^\\d/.test(el.id)) return '#' + CSS.escape(el.id);
                if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
                if (el.placeholder) return el.tagName.toLowerCase() + '[placeholder="' + el.placeholder + '"]';
                if (el.type && el.type !== 'text') return el.tagName.toLowerCase() + '[type="' + el.type + '"]';
                const t = text(el).slice(0, 30);
                if (t) return el.tagName.toLowerCase() + ':has-text("' + t + '")';
                return el.tagName.toLowerCase();
            }

            // Label for a form field: HTML semantics only, no class-name guessing.
            // 1. <label for="id">  2. enclosing <label>/<fieldset>  3. preceding sibling text
            function labelFor(el) {
                if (el.id) {
                    const lbl = document.querySelector('label[for="' + el.id + '"]');
                    if (lbl) return lbl.innerText.trim().slice(0, 40);
                }
                const wrap = el.closest('label, fieldset, [role=group]');
                if (wrap) {
                    const t = (wrap.querySelector('legend')?.innerText
                        || [...wrap.childNodes].filter(n => n.nodeType === 3)
                               .map(n => n.textContent.trim()).join(' ')).trim();
                    if (t) return t.slice(0, 40);
                }
                const prev = el.previousElementSibling;
                if (prev) {
                    const t = (prev.innerText || prev.textContent || '').trim();
                    if (t && t.length < 50) return t.slice(0, 40);
                }
                return null;
            }

            // ── Form fields (standard HTML, always accurate) ─────────────────
            const inputs = [...root.querySelectorAll('input:not([type=hidden]), textarea')]
                .slice(0, MAX).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || null,
                    name: el.name || null,
                    id: el.id || null,
                    placeholder: el.placeholder || null,
                    label: labelFor(el),
                    selector: selector(el),
                }));

            const selects = [...root.querySelectorAll('select')].slice(0, 20).map(el => ({
                name: el.name || null,
                id: el.id || null,
                label: labelFor(el),
                selector: selector(el),
                options: [...el.options].map(o => o.text.trim()).filter(Boolean).slice(0, 20),
            }));

            // ── Buttons (standard HTML + ARIA) ───────────────────────────────
            const buttons = [...root.querySelectorAll(
                'button, input[type=submit], input[type=button], [role=button]'
            )].slice(0, MAX)
                .map(el => ({ text: text(el), type: el.type || null, selector: selector(el) }))
                .filter(b => b.text);

            // ── All visible links — AI interprets which are navigation/action/content ──
            const links = [...root.querySelectorAll('a[href]')]
                .filter(el => text(el).length > 0)
                .slice(0, MAX)
                .map(el => ({
                    text: text(el),
                    href: el.href || null,
                    selector: selector(el),
                    cls: String(el.className || '').slice(0, 60),
                }));

            // ── Business-scope helpers for table row selectors ───────────────
            // A "business class" is one that is NOT a framework prefix AND NOT a
            // generic layout word — it uniquely identifies a specific table on the page.
            const FRAMEWORK_RE = /^(el|ant|arco|vxe|n|van|ivu|layui|semi|tdesign|varlet|vc|v)-/;
            const LAYOUT_WORDS = new Set([
                'app','page','main','layout','content','wrapper','container',
                'inner','outer','shell','frame','view','root','section',
                'area','panel','box','wrap','base','center','body','fluid','fixed','scroll',
            ]);
            function isLayoutOnly(cls) {
                const words = cls.toLowerCase().split(/[-_]/).filter(Boolean);
                return words.length > 0 && words.every(w => LAYOUT_WORDS.has(w));
            }
            function isBusinessClass(cls) {
                return cls.length > 2 && !FRAMEWORK_RE.test(cls) && !isLayoutOnly(cls);
            }
            // Walk up from el to find the nearest ancestor with a business-domain class.
            function nearestBizAncestor(el) {
                let cur = el.parentElement;
                while (cur && cur !== document.body) {
                    const classes = String(cur.className || '').split(/\\s+/).filter(Boolean);
                    const bizCls = classes.find(isBusinessClass);
                    if (bizCls) return { el: cur, cls: bizCls };
                    cur = cur.parentElement;
                }
                return null;
            }
            // Build a business-scoped row selector for a table element.
            function bizRowSelector(tbl) {
                const anc = nearestBizAncestor(tbl);
                if (!anc) return null;
                const scope = '.' + CSS.escape(anc.cls);
                // Prefer <tbody tr> (standard HTML), then look for a framework row class.
                if (tbl.querySelector('tbody tr')) return scope + ' tr';
                const rowEl = tbl.querySelector('[class*="row"], [class*="__row"], [class*="-row"]');
                if (rowEl) {
                    const rowCls = [...rowEl.classList].find(c =>
                        /row|__row|-row|--row/.test(c) && !isLayoutOnly(c)
                    );
                    if (rowCls) return scope + ' .' + CSS.escape(rowCls);
                }
                return scope + ' tr';
            }

            // ── Tables: standard HTML + ARIA grid/table ──────────────────────
            // Also catches custom components that render <th>/<role=columnheader> rows.
            const tableElSet = new Set([...root.querySelectorAll('table, [role=grid], [role=table]')]);
            [...root.querySelectorAll('[class]:not(table)')].forEach(el => {
                if (el.querySelector('th, [role=columnheader]')) tableElSet.add(el);
            });
            const tables = [...tableElSet].slice(0, 5).map(tbl => {
                const headers = [...tbl.querySelectorAll('th, [role=columnheader]')]
                    .map(th => text(th)).filter(Boolean);
                return {
                    headers,
                    selector: selector(tbl),
                    cls: String(tbl.className || '').slice(0, 60),
                    // row_selector: business-scoped path to data rows — use this for browser.extract extractMode=table
                    row_selector: bizRowSelector(tbl),
                };
            });

            // ── Currently-visible picker options (ARIA only) ─────────────────
            // Only populated when a dropdown/listbox is actually open.
            const visibleOptions = [...document.querySelectorAll('[role=option], [aria-selected]')]
                .slice(0, 40).map(el => text(el)).filter(Boolean);

            // ── All CSS class names on the page ──────────────────────────────
            // AI uses this to identify the UI framework (el-/ant-/arco-/n-/custom).
            const classSet = new Set();
            document.querySelectorAll('[class]').forEach(el =>
                String(el.className).split(/\\s+/).forEach(c => {
                    if (c.length > 2 && c.length < 40) classSet.add(c);
                })
            );
            const pageCls = [...classSet].slice(0, 120);

            // ── Page layout: top-level structural elements + their HTML ───────
            // Dynamic — no fixed categories. Body direct children; goes one level
            // deeper when the SPA shell wraps everything in 1-2 divs.
            const SKIP = new Set(['script','style','noscript','link','meta','title']);
            function meaningfulChildren(parent, limit) {
                return [...parent.children]
                    .filter(el => !SKIP.has(el.tagName.toLowerCase()) && el.textContent.trim().length > 5)
                    .slice(0, limit);
            }
            const bodyKids = meaningfulChildren(document.body, 10);
            const structuralEls = bodyKids.length <= 2
                ? bodyKids.flatMap(el => meaningfulChildren(el, 5)).slice(0, 8)
                : bodyKids.slice(0, 6);
            const pageLayout = structuralEls.map(el => ({
                tag: el.tagName.toLowerCase(),
                cls: String(el.className || '').slice(0, 80),
                role: el.getAttribute('role') || null,
                id: el.id || null,
                aria_label: el.getAttribute('aria-label') || null,
                html: el.outerHTML.replace(/<script[\\s\\S]*?<\\/script>/gi, '').slice(0, 2000),
            }));

            return {
                url: window.location.href,
                title: document.title,
                inputs,
                selects,
                buttons,
                links,
                tables,
                visible_options: visibleOptions,
                page_classes: pageCls,
                page_layout: pageLayout,
            };
        }"""

        try:
            async with _async_playwright() as pw:
                ctx = await pw.chromium.launch_persistent_context(
                    browser_profile,
                    headless=True,
                    args=["--disable-cache"],
                )
                try:
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                    await page.goto(url, wait_until="load", timeout=30_000)
                    # Give SPA JS time to mount; also try networkidle (best-effort)
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

                    result = await page.evaluate(_JS_EXTRACT, scope_selector)
                    result["scope_selector"] = scope_selector
                    result["note"] = (
                        "selector 字段为推荐选择器，可直接用于 browser.click / browser.fill 等节点。"
                        ":has-text() 为 Playwright 伪选择器，合法可用。"
                        "若 date_controls 字段存在，直接按 interaction_recipe.steps 和对应 selector 构建节点，"
                        "无需参考 n14-n17 的 selector。"
                    )

                    # ── Server-side SPA loading detection ────────────────────
                    # Check for nprogress-busy or common loading-state class names
                    # BEFORE the total_elements check, so a page with only a logo <a>
                    # tag (total_elements=1, no warning) still gets flagged when the
                    # SPA progress bar is active.  The `spa_loading` field is machine-
                    # readable — the AI can branch on it without scanning 120 classes.
                    page_classes: list[str] = result.get("page_classes", [])
                    spa_loading = (
                        "nprogress-busy" in page_classes
                        or any(
                            cls in page_classes
                            for cls in ("v-loading", "el-loading-mask", "ant-spin-spinning", "arco-spin")
                        )
                        or any(
                            "loading" in cls or "skeleton" in cls
                            for cls in page_classes
                            if cls not in ("el-loading-fade-enter", "el-loading-fade-leave")
                        )
                    )
                    # ── Component skill matching ──────────────────────────────
                    # Detect known UI library widgets from page_classes and inject
                    # interaction_recipe so the model doesn't need to guess selectors.
                    try:
                        from app.services.skills.registry import match_skills as _match_skills
                        from app.services.skills.registry import build_skill_recipe as _build_skill_recipe
                        _matched = _match_skills(page_classes)
                        if _matched:
                            result["date_controls"] = [
                                {
                                    "type": f"{s.library}/{s.component}",
                                    "library": s.library,
                                    "component": s.component,
                                    "description": s.description,
                                    "interaction_recipe": _build_skill_recipe(s, result.get("inputs", [])),
                                }
                                for s in _matched
                            ]
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

                    # If the page looks empty (SPA still rendering), add a retry hint
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

                    return result
                finally:
                    await ctx.close()
        except Exception as exc:
            return {"error": f"页面检查失败：{exc}"}
