"""lint_flow 主入口：结构完整性与变量契约类规则。

场景化的风险规则（登录/导航/表格/脚本等）在 app.services.ai_tools.lint_scenarios。
"""
from __future__ import annotations

from typing import Any

from app.services.ai_tools.graph import _collect_ancestor_node_ids, _collect_downstream_nodes, _unreachable_node_ids
from app.services.ai_tools.lint_scenarios import (
    _lint_extract_scalar_contract_for_scripts,
    _lint_blind_delay_instead_of_wait,
    _lint_flow_semantic_quality,
    _lint_login_without_navigation_to_data_page,
    _lint_probe_extract_without_continue_on_error,
    _lint_unrolled_repeat_chain,
    _lint_visual_layout,
)
from app.services.ai_tools.selectors import _detect_unsupported_css_selector_syntax
from app.services.ai_tools.variables import (
    _CONDITION_EXPRESSION_FIELDS,
    _VARIABLE_NAME_FIELDS,
    _collect_output_vars_in_node,
    _collect_refs_in_node,
    _template_refs,
    _validate_variable_refs,
)

# 两类循环节点的出边契约完全一致（body/exit），结构规则共用
_LOOP_LIKE_NODE_TYPES = frozenset({"control.foreach", "control.repeat_until"})

# 「后面还要拿结果」的节点：等待、取数、落盘。被吞掉的失败要归因错，前提是下游还有这么一步；
# 少收一类（脚本落盘、桌面通道取数、翻页）就等于换个写法同一个错误归因不再提示。
_RESULT_STEP_NODE_TYPES = frozenset({
    "browser.wait", "browser.waitFor", "browser.extract", "browser.paginateNext",
    "ui.wait", "ui.extract",
    "file.write", "excel.addrow", "excel.save", "excel.write",
    "script.python", "script.javascript", "script.shell",
})

# Credential-like variable name keywords
_CREDENTIAL_KEYWORDS = frozenset({
    "password", "passwd", "pwd", "username", "account", "user",
    "login", "secret", "token", "apikey", "api_key",
})

# warn 级但照样会挡住运行的 issue：它们不会让流程报错，只会让它安静地跑出错数据
# （只抽到一行、筛选没生效、登录后停在登录页），跑完才发现等于白跑一趟真实站点。
BLOCKING_LINT_ISSUES = frozenset({
    "critical_action_continue_on_error",
    "script_uses_browser_dom",
    "single_navigation_node",
    "clear_storage_breaks_login_persistence",
    "table_extract_selector_targets_container",
    "table_extract_selector_not_table_like",
    "table_extract_selector_too_broad",
    "client_side_filter_masks_page_filter",
    "date_filter_missing_verification",
    "submit_key_on_body",
    "date_trigger_selector_too_broad",
    "unrolled_repeat_click_chain",
    "login_without_navigation_to_data_page",
    "probe_extract_without_continue_on_error",
})


def is_blocking_finding(finding: Any) -> bool:
    if not isinstance(finding, dict):
        return False
    return finding.get("severity") == "error" or finding.get("issue") in BLOCKING_LINT_ISSUES


def annotate_lint_findings(findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """给每条 finding 标上会不会挡住运行，并给出对应的措辞。

    阻断名单只存在于编排层，模型看不见：它读到的是「warn」，据此判断可以先跑一次看看，
    然后被 requires_lint_fix 拦在 run_flow 上。这一轮既没跑成也没修成，而模型手上
    没有任何字段能让它提前避开——所以要修的是这里的返回值，不是提示词。
    """
    marked = [{**f, "blocks_run": is_blocking_finding(f)} for f in findings]
    blocking = sum(1 for f in marked if f["blocks_run"])
    errors = sum(1 for f in findings if f.get("severity") == "error")
    warns = sum(1 for f in findings if f.get("severity") == "warn")
    text = f"静态检查发现 {errors} 个错误、{warns} 个警告（见 lint_findings）。"
    if blocking:
        text += (
            f"其中 {blocking} 条标了 blocks_run=true，未修完就调 run_flow 会被直接阻断，"
            "请先逐项修复再运行。"
        )
    else:
        text += "没有会阻断运行的项。"
    return marked, text


def _lint_flow(
    nodes: list[Any],
    edges: list[Any],
    input_variable_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Programmatic flow quality checks; returns findings with severity/node_id/node_title/issue/message/fix."""
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

        # 1. foreach / repeat_until must have body + exit labeled edges
        if ntype in _LOOP_LIKE_NODE_TYPES:
            labels = {e.get("label", "") for e in out_edges.get(nid, [])}
            if "body" not in labels:
                findings.append({
                    "severity": "error", "node_id": nid, "node_title": ntitle,
                    "issue": "foreach_missing_body_edge",
                    "message": f"循环节点 `{nid}`（{ntype}）缺少 label='body' 的循环体出边，循环体永远不会执行。",
                    "fix": "用 update_flow add_edges 添加 source=该节点、target=循环体首节点、label='body' 的边。",
                })
            if "exit" not in labels:
                findings.append({
                    "severity": "error", "node_id": nid, "node_title": ntitle,
                    "issue": "foreach_missing_exit_edge",
                    "message": f"循环节点 `{nid}`（{ntype}）缺少 label='exit' 的循环后出边，迭代完成后流程无法继续。",
                    "fix": "用 update_flow add_edges 添加 source=该节点、target=循环后首节点、label='exit' 的边。",
                })

        # 2. condition must have true + false labeled edges
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

        # 2b. foreach / repeat_until ambiguous outgoing edges
        if ntype in _LOOP_LIKE_NODE_TYPES:
            outgoing = out_edges.get(nid, [])
            labels = [str(e.get("label", "")).strip().lower() for e in outgoing]
            unlabeled_count = sum(1 for label in labels if not label)
            if len(outgoing) >= 2 and unlabeled_count:
                findings.append({
                    "severity": "error", "node_id": nid, "node_title": ntitle,
                    "issue": "foreach_ambiguous_unlabeled_edges",
                    "message": (
                        f"循环节点 `{nid}`（{ntype}）有 {len(outgoing)} 条出边，但至少一条缺少 label。"
                        "不同端（前端校验、后端执行、AI 修复）可能对未标注出边的循环体/退出分支理解不一致，"
                        "会造成循环迭代了但写入节点未执行。"
                    ),
                    "fix": (
                        "显式标注两条出边：循环体首节点 label='body'，循环完成后节点 label='exit'。"
                        "循环体内部节点用普通边串联，不要把保存/结束节点误接成第二条未标注出边。"
                    ),
                })

        # 3. browser.extract must have outputVariable or countVariable
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

        # 4. http.request without outputVariable
        if ntype == "http.request" and not node.get("outputVariable"):
            findings.append({
                "severity": "warn", "node_id": nid, "node_title": ntitle,
                "issue": "http_no_output",
                "message": f"http.request 节点 `{nid}` 未设置 outputVariable，HTTP 响应无法被后续节点引用。",
                "fix": "用 apply_node_fix 添加 outputVariable 字段（如 'api_response'）。",
            })

        # 5. variable.input misused for credentials
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

        # 6. output file path without timestamp
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

        # 7. browser.* nodes missing selector
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

        # 7a. browser.fill missing inputValue
        if ntype == "browser.fill" and not node.get("inputValue") and not node.get("value"):
            findings.append({
                "severity": "error", "node_id": nid, "node_title": ntitle,
                "issue": "missing_inputValue",
                "message": (
                    f"browser.fill 节点 `{nid}` 缺少 inputValue 字段，运行时会抛出"
                    " '浏览器动作节点缺少 inputValue' 错误。"
                ),
                "fix": "添加 inputValue 字段，例如 inputValue: '${var.password}' 或具体的填写内容。",
            })

        # 7b. Playwright-only selector syntax in CSS selector fields
        if ntype in _NEED_SELECTOR:
            sel = str(node.get("selector", ""))
            unsupported_selector = _detect_unsupported_css_selector_syntax(sel)
            if unsupported_selector is not None:
                findings.append({
                    "severity": "error", "node_id": nid, "node_title": ntitle,
                    "issue": "unsupported_selector_syntax",
                    "message": (
                        f"节点 `{nid}` 的 selector `{sel[:80]}` 使用了 `{unsupported_selector}` 这类 "
                        "Playwright 专用定位语法。该字段会进入 CSS/querySelectorAll 兼容链路，"
                        "运行时可能报“不是有效选择器”。"
                    ),
                    "fix": (
                        "调用 inspect_page 获取真实 DOM selector，改成合法 CSS selector；"
                        "文本匹配优先使用返回的稳定 id/name/placeholder/aria selector，"
                        "不要把 text=、role=、xpath= 写入 selector 字段。"
                    ),
                })
            if "text=" in sel and "," in sel:
                findings.append({
                    "severity": "error", "node_id": nid, "node_title": ntitle,
                    "issue": "invalid_text_selector_in_css_list",
                    "message": (
                        f"节点 `{nid}` 的 selector `{sel[:80]}` 将 Playwright text= 语法与 CSS 选择器"
                        " 用逗号混用，page.wait_for_selector 无法解析 '='，运行时会报"
                        " 'Unexpected token \"=\" while parsing css selector' 错误。"
                    ),
                    "fix": (
                        "把 text=XXX 改为 :has-text('XXX') 或 [aria-label='XXX']，"
                        "与其他 CSS 选择器保持统一语法，不要在一个 selector 字段内用逗号混合两种语法。"
                    ),
                })

        findings.extend(_lint_variable_contract_for_node(node, nid=nid, ntitle=ntitle, ntype=ntype))

    # 8. Unreachable (orphan) nodes
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
    findings.extend(_lint_extract_scalar_contract_for_scripts(nodes, edges))
    findings.extend(_lint_unrolled_repeat_chain(nodes, edges))
    findings.extend(_lint_blind_delay_instead_of_wait(nodes, edges))
    findings.extend(_lint_login_without_navigation_to_data_page(nodes, edges))
    findings.extend(_lint_probe_extract_without_continue_on_error(nodes, edges))
    findings.extend(_lint_critical_continue_on_error(nodes, edges))
    findings.extend(_lint_continue_on_error_output_defaults(nodes, edges))

    # 只有一个 browser.open 却同时有登录+抽取节点，是空白页失败的常见根因
    # （登录后未导航到数据页），AI 常误诊为下游 selector 问题。
    _EXTRACTION_TYPES = {"browser.extract", "browser.wait"}
    _LOGIN_INDICATORS = {"input[type='password']", "password", "用户名", "账号", "登录"}

    # ensureLogin 也会导航（打开 targetUrl），不算进去的话「ensureLogin + 一个数据页 open」
    # 这个规范拓扑会被误判成只有一次导航
    _NAVIGATION_TYPES = {"browser.open", "browser.ensureLogin"}
    open_nodes = [n for n in nodes if isinstance(n, dict) and n.get("type") in _NAVIGATION_TYPES]
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

    # Undefined variable references
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
    """continueOnError 节点失败时其 outputVariable/countVariable 可能不会写入；若下游引用了该变量但上游无 variable.set 默认值，会导致变量未定义。"""
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
    """continueOnError 只适合可选弹窗/Cookie提示/登录态探测；用在筛选、提交、导航等关键动作上会导致失败被吞掉，报错错误归因到下游节点。"""
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
            str(candidate.get("type")) in _RESULT_STEP_NODE_TYPES
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
