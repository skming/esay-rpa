"""场景化 lint 规则：登录、导航、筛选、表格抓取、脚本、视觉布局。

与 app.services.ai_tools.lint 里的结构规则分开，是因为这些规则针对具体业务场景的经验教训，
增删频繁且彼此独立，混在主入口里会让 _lint_flow 无法阅读。
"""
from __future__ import annotations

import re
from typing import Any

from app.services.ai_tools.graph import _collect_downstream_nodes
from app.services.ai_tools.normalize import _nodes_visually_overlap
from app.services.ai_tools.script_capabilities import (
    library_for_format,
    missing_library_hint,
    semantic_rewrite_node_types,
    unsupported_formats,
)
from app.services.ai_tools.selectors import _is_broad_table_row_selector, _is_table_container_selector
from app.services.ai_tools.variables import _SCRIPT_CHANNEL_NODE_TYPES, _find_script_http_fetch_marker

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
    findings.extend(_lint_scrape_flow_without_table_output(business_nodes))
    findings.extend(_lint_extract_union_selector(business_nodes))
    findings.extend(_lint_script_environment_risks(business_nodes))
    findings.extend(_lint_script_http_flow_drift(business_nodes))
    findings.extend(_lint_script_hardcoded_content(business_nodes))
    findings.extend(_lint_unavailable_artifact_format(business_nodes))
    findings.extend(_lint_claimed_semantic_capability(business_nodes))
    findings.extend(_lint_client_side_filter_masks_page_filter(business_nodes))
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


# 把值写进筛选控件的动作，浏览器与桌面两条通道对等：同一个日期筛选用哪条通道写、
# 用输入框还是下拉还是日历格，都可能「显示了值但组件没提交」。
# browser.press 不在此列——它按的是键（Enter），不携带筛选值，收进来只会把 "enter" 当条件值。
_VALUE_WRITE_NODE_TYPES = frozenset({
    "browser.fill", "browser.click", "browser.select", "browser.check",
    "ui.fill", "ui.click", "ui.select", "ui.check",
})


def _lint_filter_control_risks(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    def _is_date_related(node: dict[str, Any]) -> bool:
        blob = f"{node.get('title', '')} {node.get('selector', '')}".lower()
        return any(keyword in blob for keyword in ("日期", "时间", "date", "开始", "结束"))

    # 日期写入无论走键入还是点日历格，都可能「看起来有值但组件模型没提交」，
    # 页面于是返回全量数据、流程一路绿灯。所以拦的不是写法，而是缺少回读校验这件事。
    date_write_nodes = [
        node for node in nodes
        if node.get("type") in _VALUE_WRITE_NODE_TYPES and _is_date_related(node)
    ]
    readback_vars = {
        str(node.get("firstValueVariable") or node.get("outputVariable") or "")
        for node in nodes
        if node.get("type") in _EXTRACT_NODE_TYPES
        and str(node.get("attribute", "")).lower() == "value"
        and _is_date_related(node)
    } - {""}
    # 门控用哪种语言写不影响它是不是门控；只认 script.python 会把 JS/shell 写的同一道比对判成缺门控。
    # control.condition 不算：它只分支，false 分支接到 end 就是静默放过，不会让运行失败。
    script_code = "\n".join(
        str(node.get("code", "")) for node in nodes
        if node.get("type") in _SCRIPT_CHANNEL_NODE_TYPES
    )
    gated = any(var in script_code for var in readback_vars)
    if date_write_nodes and not gated:
        node = date_write_nodes[0]
        findings.append({
            "severity": "error",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "date_filter_missing_verification",
            "message": (
                "流程写了日期筛选，但没有「回读输入框实际值 + 脚本比对」这道硬门控。"
                "日期没真正提交给组件时页面会返回全量数据，流程会成功结束并抓回一堆范围外的数据。"
            ),
            "fix": (
                "在日期写入之后补两步："
                "1) browser.extract（extractMode='attribute'、attribute='value'、includeInResult=false）回读开始/结束日期输入框，写入变量；"
                "2) script.python 比对回读值与目标日期，不一致时 raise SystemExit。"
                "日期段节点不要设 continueOnError=true。"
                "若回读值确实没落下，先 inspect_page 取 date_controls[].interaction_recipe，"
                "按 steps（键入日期文本 + Enter）或 fallback_steps（先按面板标题翻到目标月份再点格）重建，"
                "不要靠加 delayMs 或重复运行碰运气。"
            ),
        })

    # 组件的按键处理挂在自己那个元素上，打在 body 上的 keydown 不会冒泡过去：
    # 文本显示出来了、组件的值却没提交，是「筛选静默失效」最常见的写法。
    # 与控件是不是日期无关——搜索框、金额区间、任何要 Enter 提交的组件同理。
    if any(node.get("type") == "browser.fill" for node in nodes):
        for node in nodes:
            if node.get("type") != "browser.press":
                continue
            if str(node.get("selector", "")).strip().lower() not in ("body", "html", "document"):
                continue
            if str(node.get("inputValue", "")).strip().lower() != "enter":
                continue
            findings.append({
                "severity": "error",
                "node_id": str(node.get("id", "?")),
                "node_title": str(node.get("title") or node.get("id", "?")),
                "issue": "submit_key_on_body",
                "message": (
                    f"节点 `{node.get('id', '?')}` 把提交用的 Enter 打在 `body` 上。"
                    "组件的按键处理挂在它自己的输入框元素上，body 上的 keydown 不会冒泡过去，"
                    "结果是输入框显示了文本但组件的值从未提交，筛选静默失效。"
                ),
                "fix": "把该节点的 selector 改成刚填写的那个输入框（与前面 browser.fill 的 selector 一致）。",
            })
            break

    date_click_nodes = [node for node in date_write_nodes if node.get("type") == "browser.click"]
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
                "其余节点 selector 也按 recipe 更新。"
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


# Lint: table extraction risks

# Pre-filter keywords: node must mention at least one of these to be a
# candidate for table-extraction lint. Covers all major component libraries.
_TABLE_HINT_KEYWORDS: tuple[str, ...] = (
    "table", "tbody", "tr", "表格", "列表",
    "row", "grid", "-row", "__row", "--row",
    "vxe", "arco", "ant-table", "n-data",
)


_UNROLLED_CHAIN_MIN_LENGTH = 3
# 够写下「为什么是这个次数」的最短长度；空串和「点击」这种占位描述算不上依据
_REPEAT_JUSTIFICATION_MIN_LENGTH = 8
# 能被「重复 N 次」展开的动作。clickLoadMore 是这条规则最典型的案例（fix 文案里点了名的
# 「点到加载更多消失」），漏掉它等于放过主要形态；桌面通道的点击同理。
_UNROLLABLE_NODE_TYPES = (
    "browser.click", "browser.press", "browser.scroll", "browser.clickLoadMore",
    "ui.click",
)


def _lint_unrolled_repeat_chain(nodes: list[Any], edges: list[Any]) -> list[dict[str, Any]]:
    """同一个元素被连续操作 N 次，展开成了 N 个一模一样的节点。

    次数是生成当时数出来的常量（"从本月回翻 5 个月"、"再点 3 次加载更多"），
    而它依赖的日期或数据量随时在变，下次运行就不是这个数。
    """
    node_map = {
        str(node["id"]): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    successors: dict[str, list[str]] = {}
    for edge in edges or []:
        if isinstance(edge, dict) and edge.get("source") and edge.get("target"):
            successors.setdefault(str(edge["source"]), []).append(str(edge["target"]))

    def signature(node_id: str) -> tuple[str, str] | None:
        node = node_map.get(node_id)
        if not isinstance(node, dict) or node.get("type") not in _UNROLLABLE_NODE_TYPES:
            return None
        # 同一个「点哪儿」在不同节点类型上叫不同字段名（clickLoadMore 用 targetSelector）
        selector = str(node.get("selector") or node.get("targetSelector") or "").strip()
        return (str(node["type"]), selector) if selector else None

    findings: list[dict[str, Any]] = []
    visited: set[str] = set()
    for node_id in node_map:
        if node_id in visited:
            continue
        current = signature(node_id)
        if current is None:
            continue
        chain = [node_id]
        cursor = node_id
        # 分叉处断链：只有单出边才算「接着又点了一次同一个东西」
        while len(successors.get(cursor, [])) == 1:
            nxt = successors[cursor][0]
            if signature(nxt) != current:
                break
            chain.append(nxt)
            cursor = nxt
        if len(chain) < _UNROLLED_CHAIN_MIN_LENGTH:
            continue
        visited.update(chain)
        head = node_map[chain[0]]
        # 「确实是固定次数的业务动作」是合法写法，前提是给出了依据。链上任一节点在
        # description 里说明了次数由来，就降为提示——否则这条规则等于不许业务定次数，
        # 而它的 fix 文案又明说这种情况可以保留，自相矛盾。
        justified = any(
            len(str(node_map[node_id].get("description") or "").strip()) >= _REPEAT_JUSTIFICATION_MIN_LENGTH
            for node_id in chain
        )
        findings.append({
            "severity": "warn" if justified else "error",
            "node_id": chain[0],
            "node_title": str(head.get("title") or chain[0]),
            "issue": "unrolled_repeat_click_chain",
            "message": (
                f"{len(chain)} 个连续节点操作同一个元素、selector 完全相同"
                f"（{', '.join(chain)}）。重复次数被写死成了常量。"
                + ("已在 description 里说明了次数依据，按固定次数的业务动作处理，不阻断；"
                   "若这个次数其实取决于运行时状态，仍应改用 control.repeat_until。"
                   if justified else "")
            ),
            "fix": (
                "确定这个次数由什么决定：\n"
                "• 由运行时状态决定（翻到目标月份、点到「加载更多」消失、等状态变化）→ 用 "
                "`control.repeat_until`：循环体放这个动作 + 一个刷新状态的 extract，"
                "condition 写退出条件（如 panel_month == '2026-06'）。次数交给运行时算，不写死。\n"
                "• 由数据量决定（翻页）→ 用 browser.paginateNext 让运行器自己判断何时停。\n"
                "• 确实是固定次数的业务动作 → 保留，并在 description 里写明为什么是这个数（写了就不再阻断）。"
            ),
            "chain_node_ids": chain,
        })
    return findings


# 抽取节点：浏览器与桌面两条通道，凡是判「抽取」的规则都用这一份
_EXTRACT_NODE_TYPES = frozenset({"browser.extract", "ui.extract"})
_BARE_CLASS_SELECTOR = re.compile(r"(?:div|span|section|ul|ol)?\.[A-Za-z0-9_-]+$")


def _lint_extract_union_selector(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """抽取节点把并集当兜底用。

    并集在 browser.wait 上是"任一出现即可"，在抽取节点上却是"全都抓"。写成一串
    由粗到细的纯 class（.x-page, .x-section, .x-grid, .x-card）说明作者没定下哪个是
    目标，而最粗的那个通常是其余几个的祖先，抽取范围会塌到整片页面。
    """
    findings: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("type") not in _EXTRACT_NODE_TYPES:
            continue
        selector = str(node.get("selector") or "")
        parts = [part.strip() for part in selector.split(",") if part.strip()]
        if len(parts) < 3 or not all(_BARE_CLASS_SELECTOR.fullmatch(part) for part in parts):
            continue
        findings.append({
            "severity": "error",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "extract_selector_union_used_as_fallback",
            "message": (
                f"抽取节点的 selector 由 {len(parts)} 个纯 class 并集组成：{selector}。"
                "抽取节点不会择一命中，而是把每一项匹配到的元素全部抓下来；"
                "这几项若存在嵌套关系，最外层会吞掉其余项，结果变成整片区域甚至整页。"
            ),
            "fix": (
                "先用 inspect_page 确认目标数据所在的那一层容器，只保留最贴近数据的一项。"
                "需要兼容多种页面结构时，用 browser.wait 做存在性判断，不要在抽取节点里堆并集。"
            ),
        })
    return findings


# 翻页/加载更多节点的 selector 是按钮，抽取用的行选择器在 targetSelector 上
_ROW_SELECTOR_FIELD_BY_TYPE = {
    "browser.extract": "selector",
    "ui.extract": "selector",
    "browser.paginateNext": "targetSelector",
    "browser.clickLoadMore": "targetSelector",
}


# 输出形态静态不可知的节点，一律按「可能出表」放行，避免误报。
# 这是豁免而不是判据：漏一个类型就是凭空多一条误报，所以三种脚本通道和所有列变换节点都要在内。
_ROW_PRODUCING_TYPES = frozenset({
    "script.python", "script.javascript", "script.shell",
    "data.json.parse", "data.list.map", "data.convert",
    "excel.read", "http.request",
})


def _lint_scrape_flow_without_table_output(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """抓取型流程没有任何按行结构化的输出。

    assert_run_output 本来就会以 no_table_like_output 判失败，但那是跑完之后——
    等于白跑一次浏览器再回来重建抽取链路。这条规则把同一道门挪到运行前。
    """
    extract_nodes = [
        node for node in nodes
        if isinstance(node, dict) and str(node.get("type")) in _ROW_SELECTOR_FIELD_BY_TYPE
    ]
    if not extract_nodes:
        return []
    if any(node.get("extractMode") == "table" for node in extract_nodes):
        return []
    if any(
        isinstance(node, dict)
        and str(node.get("type")) in _ROW_PRODUCING_TYPES
        and node.get("outputVariable")
        for node in nodes
    ):
        return []

    first = extract_nodes[0]
    return [{
        "severity": "warn",
        "node_id": str(first.get("id", "?")),
        "node_title": str(first.get("title") or first.get("id", "?")),
        "issue": "scrape_flow_without_table_output",
        "message": (
            "流程有抽取节点，但没有任何节点产出按行结构化的表格变量："
            f"{first.get('extractMode') or '未声明'} 模式只会得到文本数组。"
            "运行后 assert_run_output 会以 no_table_like_output 判定不合格。"
        ),
        "fix": (
            "把抽取节点改为 extractMode='table'（selector 指向数据行）；"
            "确实无法从 DOM 直接出表时，补一个 script.python 把文本整理成 [{列: 值}] 并写入 outputVariable。"
        ),
    }]


def _lint_table_output_risks(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for node in nodes:
        selector_field = _ROW_SELECTOR_FIELD_BY_TYPE.get(str(node.get("type")))
        if selector_field is None:
            continue
        selector = str(node.get(selector_field, "")).lower()
        title = str(node.get("title", "")).lower()
        if any(keyword in title for keyword in ("header", "表头", "列名")):
            continue
        # hint 关键字只用来猜"这节点本该是表格抽取吗"；extractMode 已声明 table 时不必猜
        if node.get("extractMode") != "table" and not any(
            keyword in f"{selector} {title}" for keyword in _TABLE_HINT_KEYWORDS
        ):
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
            if not container_like and not has_row_target:
                findings.append({
                    "severity": "error",
                    "node_id": str(node.get("id", "?")),
                    "node_title": str(node.get("title") or node.get("id", "?")),
                    "issue": "table_extract_selector_not_table_like",
                    "message": (
                        f"节点使用 extractMode='table'，但 {selector_field} {node.get(selector_field)!r} "
                        "既没指向表格容器也没指向数据行。若它命中的是页面级容器，"
                        "抽取会把容器内所有表格的行混在一起（表头错配、行数虚高）；"
                        "若命中的是卡片/列表等非表格结构，则一行都抓不到。"
                    ),
                    "fix": (
                        "调用 inspect_page 确认目标结构：真是表格就用返回的 tables[].row_selector；"
                        "若目标是指标卡片、列表项等非表格结构，改用 extractMode='text' 或 'attribute'。"
                    ),
                })
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


def _lint_script_http_flow_drift(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """流程没有 browser 主链路却用脚本直接请求网页，通常是修复过程中擅自切换了执行通道。"""
    has_browser_flow = any(
        node.get("type") in {"browser.open", "browser.extract", "ui.extract", "browser.fetch"}
        for node in nodes
    )
    if has_browser_flow:
        return []

    findings: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("type") not in _SCRIPT_CHANNEL_NODE_TYPES:
            continue
        code = str(node.get("code") or "")
        marker = _find_script_http_fetch_marker(code)
        if marker is None:
            continue
        findings.append({
            "severity": "warn",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "script_http_fetch_without_browser_flow",
            "message": (
                f"流程没有 browser.open/browser.extract 主链路，但脚本节点使用 `{marker}` 直接抓网页。"
                "如果用户没有明确要求 Python/Scrapling/HTTP 脚本，这通常是 AI 修复时擅自切换执行通道。"
            ),
            "fix": (
                "恢复或保留原浏览器采集链路；对分页、多页、加载更多等问题，应新增分页节点和累积变量，"
                "不要用本地脚本 HTTP 请求替代浏览器流程。若用户明确要求脚本方案，可忽略此 warning。"
            ),
        })
    return findings


_ENV_VAR_PARSE_PATTERN = re.compile(
    r"(\w+)\s*=\s*json\.loads\(\s*os\.environ\.get\(\s*['\"]RPA_VARIABLES_JSON['\"]"
)
# 长文本字面量（非插值构造）：中日韩标点句读 + 足够长度，视为"写死的成品内容"。
# f-string/拼接构造的文本不在此列，因为那是从变量动态生成的。
_LITERAL_PROSE_PATTERN = re.compile(
    r"(?<![fF])(['\"])((?:(?!\1).){24,})\1"
)
_CJK_SENTENCE_PUNCTUATION = ("。", "，", "！", "？", "；", "：")
# 报错/日志文案不是交付内容：它只在数据为空或异常时出现，写死是对的。
# 三种脚本通道各自的写法都要覆盖：只认 Python 写法，同一段逻辑换成 JS 就又开始误报。
# 不豁免 print/echo——script 节点靠 stdout 交付，那里的固定长文本正是这条规则要抓的。
_ERROR_MESSAGE_CALL = re.compile(
    r"(?:raise\s+\w+\s*\(|SystemExit\s*\(|assert\s|throw\s+new\s+\w*Error\s*\(|"
    r"sys\.stderr\.write\s*\(|\.(?:error|warning|warn|exception|critical)\s*\()[^\n]*$"
)
_STDERR_REDIRECT = re.compile(r">&\s*2|>>?\s*/dev/stderr")


# 「按条件保留子集」的脚本特征：既做比较，又把命中的行收进一个新集合。
# 三种脚本通道的写法都要认：只认 Python 的 append/推导式，同一处静默丢数据换成
# JS 的 filter/push 就查不出来了——而这条规则拦的正是「所有信号都显示成功」的数据缺失。
_SUBSET_BUILD_RE = re.compile(
    r"\.append\(|\[[^\]]{0,120}\bfor\b[^\]]{0,120}\bif\b|"
    r"\.filter\(|\.push\(|\bgrep\b|\bawk\b"
)
_CONDITION_COMPARE_RE = re.compile(
    r"[<>]=?|===?|!==?|\bin\b|between|startsWith|startswith|endsWith|endswith|includes\("
)
# 断言型写法：发现不合条件的数据就让流程失败。各通道的「失败」都算。
_SCRIPT_ASSERTION_RE = re.compile(r"\braise\b|\bthrow\b|process\.exit\(|\bexit [1-9]|SystemExit")
_VAR_REF_RE = re.compile(r"\$\{var\.([A-Za-z_][A-Za-z0-9_]*)\}")


def _collect_page_filter_tokens(nodes: list[dict[str, Any]]) -> set[str]:
    """流程写进页面的筛选条件值——变量名与字面量。

    这是判断「脚本在替页面做筛选」的通用依据：脚本若拿这些同样的值去裁剪结果集，
    就说明同一个条件被施加了两次，而第二次会把第一次的失效完全掩盖。不按
    日期/枚举等语义关键词识别，任何字段的筛选都能落进来。
    """
    tokens: set[str] = set()
    for node in nodes:
        # 与日期门控同一份写值节点表：填输入框、选下拉、勾选框，浏览器与桌面通道对等。
        # click 类节点通常不带值，收进来也取不到 token，不必单独排除。
        if node.get("type") not in _VALUE_WRITE_NODE_TYPES:
            continue
        raw = str(node.get("inputValue") or node.get("value") or "")
        if not raw:
            continue
        refs = _VAR_REF_RE.findall(raw)
        tokens.update(refs)
        # 直接写死的条件值（非变量引用）也算，长度门槛避开单字符噪声
        if not refs and len(raw.strip()) >= 2:
            tokens.add(raw.strip())
    return tokens


def _lint_client_side_filter_masks_page_filter(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """拦「页面筛选没生效，就用脚本把不合条件的数据滤掉」。

    这是最难发现的一类错误：脚本一过滤，输出全部符合条件、质量审计通过、
    流程绿灯，用户拿到的却是「未筛选结果的前几页里恰好合规的那部分」——
    真实数据缺失，而所有信号都显示成功。分界线是**断言不是过滤**：
    发现不合条件的数据必须 raise 让流程失败，而不是把它悄悄删掉。

    日期只是最常见的一种；枚举、关键词、金额区间同样适用，所以触发条件取
    「脚本裁剪结果集所用的值 == 流程写进页面的筛选条件值」这个结构特征。
    """
    filter_tokens = _collect_page_filter_tokens(nodes)
    if not filter_tokens:
        return []  # 流程没声称要在页面上设筛选条件，脚本里怎么处理数据是它自己的事

    findings: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("type") not in _SCRIPT_CHANNEL_NODE_TYPES:
            continue
        code = str(node.get("code", ""))
        if not code:
            continue
        # 条件值只要出现在脚本里就算数：它常被先解析成中间变量（date_start → start）
        # 再参与比较，要求它和比较运算同行会漏掉绝大多数真实写法。
        hit = next((token for token in filter_tokens if token in code), None)
        if not hit:
            continue
        if not _SUBSET_BUILD_RE.search(code) or not _CONDITION_COMPARE_RE.search(code):
            continue
        if _SCRIPT_ASSERTION_RE.search(code):
            continue  # 断言型脚本正是我们想要的写法
        findings.append({
            "severity": "error",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "client_side_filter_masks_page_filter",
            "message": (
                f"节点 `{node.get('id', '?')}` 在脚本里按 `{hit}` 裁剪结果集，而流程本身已经把这个条件写进了页面筛选。"
                "页面筛选一旦失效，这个脚本会把不合条件的行悄悄删掉：输出全部符合条件、质量审计通过、流程绿灯，"
                "但数据其实来自未筛选结果的前几页，真实记录大量缺失，且没有任何信号提示出错。"
            ),
            "fix": (
                "把这个节点从「过滤」改成「断言」：发现不符合条件的数据时 raise SystemExit 让流程失败，"
                "不要删行、不要覆盖结果变量。页面筛选是否真的生效，只能由页面自己证明——"
                "回读输入框 value 只能说明文本写进去了，不能说明组件已提交筛选条件"
                "（直接给 input.value 赋值的执行器下它必然通过）。真正的证据是：抓回的数据全部符合条件。"
            ),
        })
    return findings


# 已有的文件搬过来（下载、复制）不需要任何库，那是传输不是生成
_ARTIFACT_TRANSPORT_MARKERS = (
    "requests.", "httpx", "aiohttp", "urllib", "urlretrieve", "curl ",
    "shutil.copy", "shutil.move", "read_bytes(", "download",
)
_ARTIFACT_SUFFIX_PATTERN = re.compile(r"(\.[a-z0-9]{2,5})\b", re.IGNORECASE)


def _lint_unavailable_artifact_format(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """环境里没有对应库的格式，不许生成——这条必须是 error，warn 拦不住。

    缺库不会让脚本报错：模型会拿标准库手拼字节流，跑完 success、产物也在，
    坏在打开的时候才知道（真实案例：手搓的 PDF 字体没内嵌，换个查看器就是空白页）。
    「跑得起来」在这里不是能力证明，所以判据只能放在运行之前。

    只查 script.python：可用性是 import 出来的，只对它跑的那个解释器成立。
    """
    blocked = set(unsupported_formats())
    if not blocked:
        return []
    findings: list[dict[str, Any]] = []
    for node in nodes:
        node_type = node.get("type")
        if node_type == "script.python":
            text = str(node.get("code") or "")
            if any(marker in text.lower() for marker in _ARTIFACT_TRANSPORT_MARKERS):
                continue
        elif node_type == "file.write":
            text = str(node.get("path") or "")
        else:
            continue

        # 后缀集合从能力表推出来，不在这里重写一份：加一个库就少拦一个格式，不该改两处
        suffix = next(
            (m.group(1).lower() for m in _ARTIFACT_SUFFIX_PATTERN.finditer(text) if m.group(1).lower() in blocked),
            None,
        )
        if suffix is None:
            continue
        findings.append({
            "severity": "error",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "unavailable_artifact_format",
            "message": (
                f"节点要生成 {suffix} 文件，但当前运行环境没有能生成它的库（需要 {missing_library_hint(suffix)}）。"
                "不装库也能写出这个后缀的文件，只是内容多半打不开，而流程会照常报 success。"
            ),
            "fix": (
                "改成环境支持的交付格式（.md/.html/.csv"
                + (f"/.xlsx（{library_for_format('.xlsx')}）" if library_for_format(".xlsx") else "")
                + f"），或者停下来告诉用户「当前环境缺 {missing_library_hint(suffix)}，装上后才能导出 {suffix}」，"
                "由用户决定装库还是换格式。不要自己拼字节流绕过去。"
            ),
        })
    return findings


# 用户看得见的地方声称做了语义加工：节点标题、说明、输出变量名、产物文件名
_SEMANTIC_CLAIM_PATTERN = re.compile(
    r"总结|摘要|概述|归纳|提炼|润色|改写|翻译|summar|abstract|rewrite|paraphrase|translat",
    re.IGNORECASE,
)
# 已经说清楚是规则产物的说法，不算冒充
_SEMANTIC_HONEST_MARKERS = ("原文摘录", "摘录", "要点提取", "节选", "excerpt")

# 只读/取数/控制流节点上出现「总结」是在描述读到的东西（「提取页面总结区域」「读取总结文档」），
# 不是声称自己做了语义加工；除这些之外的业务节点都会产出内容，都要判。
# 反过来白名单几种脚本类型是不够的：同一个冒充换成 data.string.transform、excel.write、
# variable.set 一样交付给用户，模型选哪种节点做拼装是随机的。
_SEMANTIC_CLAIM_EXEMPT_PREFIXES = ("browser.", "ui.", "control.")
_SEMANTIC_CLAIM_EXEMPT_TYPES = frozenset({
    "file.read", "file.list", "file.watch", "excel.read", "http.request",
    "data.json.parse", "variable.get", "variable.input", "variable.log",
})


def _lint_claimed_semantic_capability(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """没有会调模型的节点时，不许把规则处理命名成「总结」——这条必须是 error。

    与缺库同理，缺的是能力而不是语法：脚本取前 8 句照样 success、文件照样在，
    审计也能通过（正文确实来自抓取数据），坏在用户以为自己拿到的是总结。
    真实案例 flow ce71c23a：交付的「## 生成总结」是回复列表前 8 条原文逐字，
    「## 关键词」是正则噪音，助手回的是「已验收通过」，全程没说这不是总结。

    出路是改说法 + 告诉用户，不是换个写法再切一次——所以判据放在节点的对外字段上。
    """
    if semantic_rewrite_node_types():
        return []
    findings: list[dict[str, Any]] = []
    for node in nodes:
        node_type = str(node.get("type") or "")
        if node_type.startswith(_SEMANTIC_CLAIM_EXEMPT_PREFIXES) or node_type in _SEMANTIC_CLAIM_EXEMPT_TYPES:
            continue
        claims = " ".join(
            str(node.get(field) or "")
            # 各节点类型给产物起名的字段不同：脚本/转换用 outputVariable，variable.set 用
            # variableName，写文件用 path，excel 用 sheetName——少收一个就少拦一类节点。
            for field in ("title", "description", "outputVariable", "variableName", "path", "sheetName")
        )
        if not _SEMANTIC_CLAIM_PATTERN.search(claims):
            continue
        if any(marker in claims for marker in _SEMANTIC_HONEST_MARKERS):
            continue
        findings.append({
            "severity": "error",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "claimed_semantic_capability_unavailable",
            "message": (
                "节点对外声称做总结/摘要/改写一类的语义加工，但当前没有任何会调模型的节点类型，"
                "脚本只能截取、正则、统计——产出必然是原文的子集，不是新表述。"
                "这类冒充不会报错：流程 success、文件非空、内容也确实来自抓取数据。"
            ),
            "fix": (
                "先告诉用户「平台没有语义加工能力，只能给原文摘录/要点提取，要真总结需要接入模型节点」，"
                "由用户决定接受还是改需求；用户接受后，把节点标题、description、输出变量名和文档里的标题"
                "都改成实际做的事（如「原文摘录」「要点提取」），不要保留「总结」的说法。"
            ),
        })
    return findings


def _lint_script_hardcoded_content(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """检测脚本"看起来数据驱动、实际写死内容"的风险：装饰性解析 RPA_VARIABLES_JSON 却从未引用，或出现非拼接构造的长中文字面量。"""
    findings: list[dict[str, Any]] = []
    for node in nodes:
        node_type = node.get("type")
        if node_type not in _SCRIPT_CHANNEL_NODE_TYPES:
            continue
        code = str(node.get("code") or "")
        if not code:
            continue

        # os.environ.get(RPA_VARIABLES_JSON) 是 Python 专属访问方式，仅对 script.python 检查
        env_match = _ENV_VAR_PARSE_PATTERN.search(code) if node_type == "script.python" else None
        if env_match:
            var_name = env_match.group(1)
            rest = code[env_match.end():]
            if not re.search(rf"\b{re.escape(var_name)}\b\s*[.\[]", rest):
                findings.append({
                    "severity": "warn",
                    "node_id": str(node.get("id", "?")),
                    "node_title": str(node.get("title") or node.get("id", "?")),
                    "issue": "script_decorative_variable_parsing",
                    "message": (
                        f"脚本解析了 RPA_VARIABLES_JSON 到变量 `{var_name}`，但之后从未使用它。"
                        "这通常说明脚本看起来是数据驱动的，实际内容是写死的，换一次输入不会跟着变化。"
                    ),
                    "fix": "改用抓取到的变量拼接实际内容，或删除这段未使用的解析代码。",
                })

        for match in _LITERAL_PROSE_PATTERN.finditer(code):
            literal = match.group(2)
            if not any(mark in literal for mark in _CJK_SENTENCE_PUNCTUATION):
                continue
            line_start = code.rfind("\n", 0, match.start()) + 1
            line_end = code.find("\n", match.end())
            line = code[line_start : line_end if line_end != -1 else len(code)]
            if _ERROR_MESSAGE_CALL.search(code[line_start : match.start()]):
                continue
            # shell 没有 raise：定向到 stderr 就是报错路径，而这个标记写在字面量之后
            if _STDERR_REDIRECT.search(line):
                continue
            findings.append({
                "severity": "warn",
                "node_id": str(node.get("id", "?")),
                "node_title": str(node.get("title") or node.get("id", "?")),
                "issue": "script_hardcoded_prose_literal",
                "message": (
                    f"脚本中出现较长的固定文本字面量（\"{literal[:20]}…\"），"
                    "包含中文句读标点，疑似把某次抓取结果的结论直接写成了成品文本，而非从变量动态生成。"
                ),
                "fix": "改用 f-string 或字符串拼接，从实际抓取到的变量组装输出内容，不要写死具体结论。",
            })
            break  # 每个节点报一次即可，避免同一处硬编码刷屏

    return findings


def _lint_extract_scalar_contract_for_scripts(nodes: list[Any], edges: list[Any]) -> list[dict[str, Any]]:
    """browser.extract 的 outputVariable 按列表保存；下游脚本若当字符串处理单个文本，必须改用 firstValueVariable，否则 list 无 splitlines/strip。"""
    node_map: dict[str, dict[str, Any]] = {
        str(node["id"]): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    children_by_source: dict[str, list[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict) or not edge.get("source") or not edge.get("target"):
            continue
        children_by_source.setdefault(str(edge["source"]), []).append(str(edge["target"]))

    findings: list[dict[str, Any]] = []
    scalar_modes = {"text", "html", "attribute"}
    extract_types = {"browser.extract", "ui.extract", "browser.fetch"}
    script_types = {"script.python", "script.javascript", "script.shell"}

    for node in node_map.values():
        if str(node.get("type")) not in extract_types:
            continue
        mode = str(node.get("extractMode") or "text").lower()
        if mode not in scalar_modes:
            continue
        output_variable = str(node.get("outputVariable") or node.get("responseVariable") or "").strip()
        if not output_variable or node.get("firstValueVariable"):
            continue

        downstream_scripts = [
            candidate
            for candidate in _collect_downstream_nodes(str(node.get("id")), children_by_source, node_map)
            if (
                str(candidate.get("type")) in script_types
                and _script_references_variable(candidate, output_variable)
                and not _script_normalizes_list_variable(candidate, output_variable)
            )
        ]
        if not downstream_scripts:
            continue

        findings.append({
            "severity": "error",
            "node_id": str(node.get("id", "?")),
            "node_title": str(node.get("title") or node.get("id", "?")),
            "issue": "extract_scalar_output_consumed_by_script_without_first_value",
            "message": (
                f"抽取节点 `{node.get('id', '?')}` 的 `{output_variable}` 会按列表保存，"
                "但下游脚本直接引用该变量。脚本若按字符串处理会出现 list 没有 splitlines/strip 等错误。"
            ),
            "fix": (
                "给抽取节点添加 firstValueVariable（如把 outputVariable 改成复数 topic_texts，"
                "并新增 firstValueVariable='topic_text'），下游脚本读取首值变量；"
                "若确实消费列表，则脚本必须先 isinstance(value, list) 并 join 归一化。"
            ),
            "output_variable": output_variable,
            "downstream_script_ids": [str(script.get("id", "?")) for script in downstream_scripts[:5]],
        })

    return findings


def _script_references_variable(node: dict[str, Any], variable_name: str) -> bool:
    code = str(node.get("code") or node.get("command") or "")
    if not code:
        return False
    escaped = re.escape(variable_name)
    patterns = [
        rf"_vars\.get\(\s*['\"]{escaped}['\"]",
        rf"_vars\[\s*['\"]{escaped}['\"]\s*\]",
        rf"\$\{{var\.{escaped}\}}",
    ]
    return any(re.search(pattern, code) for pattern in patterns)


def _script_normalizes_list_variable(node: dict[str, Any], variable_name: str) -> bool:
    code = str(node.get("code") or node.get("command") or "")
    if not code:
        return False
    if variable_name not in code:
        return False
    lowered = code.lower()
    # 允许脚本显式按 list 处理，再 join 成字符串或逐项消费；这类脚本不需要首值变量。
    if "isinstance" in lowered and "list" in lowered:
        return True
    if ".join(" in lowered:
        return True
    return False


def _lint_visual_layout(nodes: list[Any]) -> list[dict[str, Any]]:
    """检测画布节点视觉重叠：执行器不关心坐标，但 Studio 画布关心；240px 宽卡片在 x=360/560 这类相近坐标下仍会重叠。"""
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















_LOGIN_FIELD_KEYWORDS = ("password", "passwd", "pwd", "密码")
_NAVIGATION_NODE_TYPES = frozenset({
    "browser.open", "browser.click", "browser.hover", "browser.ensureLogin",
    "browser.tab.open", "browser.tab.switch", "ui.click",
})
_DATA_READ_NODE_TYPES = frozenset({"browser.extract", "browser.paginateNext", "ui.extract"})


def _reachable_from(start_ids: set[str], adjacency: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(start_ids)
    while stack:
        current = stack.pop()
        for nxt in adjacency.get(current, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _lint_login_without_navigation_to_data_page(
    nodes: list[dict[str, Any]], edges: list[Any]
) -> list[dict[str, Any]]:
    """登录之后必须再导航一次才能到数据页——登录成功停留的是首页/工作台。

    少了这一段，下游 extract 会在登录后的落地页上找表格，报出来的却是 selector 超时，
    于是修复精力全花在 selector 上。这是结构问题，看拓扑就能判定，不必等运行。
    """
    node_map = {str(n["id"]): n for n in nodes if isinstance(n, dict) and n.get("id")}
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        if isinstance(edge, dict) and edge.get("source") and edge.get("target"):
            adjacency.setdefault(str(edge["source"]), []).append(str(edge["target"]))

    password_fills = {
        nid for nid, node in node_map.items()
        if node.get("type") in ("browser.fill", "ui.fill")
        and any(kw in f"{node.get('selector', '')}{node.get('title', '')}".lower() for kw in _LOGIN_FIELD_KEYWORDS)
    }
    if not password_fills:
        return []

    after_login = _reachable_from(password_fills, adjacency)
    reads = [nid for nid in after_login if node_map[nid].get("type") in _DATA_READ_NODE_TYPES]
    if not reads:
        return []
    # 登录后任何一种导航动作都算数：直达 URL、菜单点击、悬停展开都是合法走法，
    # 这里只判断「有没有导航过」，不规定该用哪一种。
    if any(node_map[nid].get("type") in _NAVIGATION_NODE_TYPES for nid in after_login):
        return []

    first_read = node_map[sorted(reads)[0]]
    return [{
        "severity": "error",
        "node_id": str(first_read.get("id", "?")),
        "node_title": str(first_read.get("title") or first_read.get("id", "?")),
        "issue": "login_without_navigation_to_data_page",
        "message": (
            f"填写密码之后直到取数节点 `{first_read.get('id')}` 之间没有任何导航动作。"
            "登录成功后浏览器停在首页/工作台，取数节点会在这个页面上找目标元素，"
            "表现为 selector 超时——改 selector 修不好。"
        ),
        "fix": (
            "在登录完成之后、取数之前补一段导航：browser.open(目标数据页 URL)，"
            "或用 inspect_page 拿到真实菜单 selector 后 browser.click；再 browser.wait 目标区域出现。"
        ),
    }]


def _lint_probe_extract_without_continue_on_error(
    nodes: list[dict[str, Any]], edges: list[Any]
) -> list[dict[str, Any]]:
    """探测型 extract（数元素个数喂给分支）必须容错，否则「数到 0」会变成运行失败。

    count 模式的语义就是「可能是 0」，而 Playwright 找不到元素是抛超时而不是返回 0；
    不加 continueOnError，本该走 else 分支的正常情况会直接中断流程。
    """
    node_map = {str(n["id"]): n for n in nodes if isinstance(n, dict) and n.get("id")}
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        if isinstance(edge, dict) and edge.get("source") and edge.get("target"):
            adjacency.setdefault(str(edge["source"]), []).append(str(edge["target"]))

    # 「数到 0」的歧义与消费它的是 if 还是循环退出条件无关：control.repeat_until 的
    # condition 同样在等这个计数，探测节点一抛超时，两种写法都是流程直接失败。
    condition_expressions = " ".join(
        str(node.get("condition") or node.get("expression") or node.get("inputValue") or "")
        for node in node_map.values()
        if str(node.get("type") or "").startswith("control.")
    )

    findings: list[dict[str, Any]] = []
    for nid, node in node_map.items():
        if node.get("type") not in _EXTRACT_NODE_TYPES or node.get("continueOnError"):
            continue
        count_var = str(node.get("countVariable") or "").strip()
        if not count_var or count_var not in condition_expressions:
            continue
        findings.append({
            "severity": "error",
            "node_id": nid,
            "node_title": str(node.get("title") or nid),
            "issue": "probe_extract_without_continue_on_error",
            "message": (
                f"节点 `{nid}` 用 countVariable=`{count_var}` 做条件分支的探测，却没有 continueOnError。"
                "元素不存在时运行器抛的是超时而不是 0，本该走另一条分支的正常情况会直接让流程失败。"
            ),
            "fix": (
                f"给该节点加 continueOnError:true，并在它之前用 variable.set 把 `{count_var}` 预设为 0；"
                "若这是登录态检测，更推荐直接换成 browser.ensureLogin，由运行器判定，没有这个歧义。"
            ),
        })
    return findings


# 1 秒以下按动画/防抖收尾处理，不报；以上视为在猜页面加载耗时。
_BLIND_DELAY_THRESHOLD_MS = 1000
# 「必须先有这个元素才做得成」的节点。少收一个类型，同样的盲等就只因为下游换了个
# 等价节点（clickLoadMore 代替 click、ui 通道代替 browser）而不报。
_SELECTOR_DEPENDENT_TYPES = frozenset({
    "browser.click", "browser.fill", "browser.wait", "browser.waitFor", "browser.extract",
    "browser.press", "browser.select", "browser.check", "browser.hover",
    "browser.drag", "browser.clickLoadMore", "browser.paginateNext",
    "ui.click", "ui.fill", "ui.wait", "ui.extract", "ui.select", "ui.check", "ui.drag",
})
# 等元素出现的节点：下游已经在等了，前面那个 delay 至多冗余
_WAIT_NODE_TYPES = frozenset({"browser.wait", "browser.waitFor", "ui.wait"})


def _lint_blind_delay_instead_of_wait(nodes: list[Any], edges: list[Any]) -> list[dict[str, Any]]:
    """用固定睡眠代替等元素：延时不够时，失败会报在下游节点的 selector 上。"""
    node_map = {str(n["id"]): n for n in nodes if isinstance(n, dict) and n.get("id")}
    downstream: dict[str, list[str]] = {}
    for edge in edges:
        if isinstance(edge, dict) and edge.get("source") and edge.get("target"):
            downstream.setdefault(str(edge["source"]), []).append(str(edge["target"]))

    findings: list[dict[str, Any]] = []
    for nid, node in node_map.items():
        delay = node.get("delayMs")
        if not isinstance(delay, int) or delay < _BLIND_DELAY_THRESHOLD_MS:
            continue
        successors = [node_map[t] for t in downstream.get(nid, []) if t in node_map]
        # 下游已经在等元素了，这个 delay 至多是冗余，不值得报
        if any(s.get("type") in _WAIT_NODE_TYPES for s in successors):
            continue
        if not any(s.get("type") in _SELECTOR_DEPENDENT_TYPES for s in successors):
            continue
        findings.append({
            "severity": "warn",
            "node_id": nid,
            "node_title": str(node.get("title") or nid),
            "issue": "blind_delay_instead_of_wait",
            "message": (
                f"节点 `{nid}` 用 delayMs={delay} 等页面就绪，后面直接就是需要元素存在的节点。"
                "这个毫秒数是生成时猜的，机器慢一点就不够——届时失败会报在下游节点的 selector 上，"
                "看起来像 selector 写错了。"
            ),
            "fix": (
                "在两者之间插入 browser.wait 等目标元素出现，或用 browser.waitFor "
                "（waitCondition='hidden' 等 loading 遮罩消失、'textContains' 等结果文案出现），"
                "把 delayMs 调小或删掉；"
                "确实没有元素可等（动画收尾、输入防抖）时才保留固定延时。"
            ),
        })
    return findings
