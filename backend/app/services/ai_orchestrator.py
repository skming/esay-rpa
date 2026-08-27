"""LiteLLM-based AI orchestrator with tool-call loop and SSE streaming."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from app.services import ai_repair_ledger as _repair_ledger
from app.services import ai_session_checkpoint as _session_checkpoint
from app.services.ai_config_service import AiConfigService
from app.services.ai_relay_errors import clean_litellm_error, is_vision_error
from app.services.ai_guard_state import GuardState
from app.services.ai_model_caps import _model_caps
from app.services.ai_context_window import (
    _compact_tool_messages,
    _context_char_budget,
    _elide_repeated_result,
    _expand_history_tool_calls,
    _mark_history_cache_anchor,
)
from app.services.ai_guards import (
    PARALLEL_SAFE_TOOLS as _PARALLEL_SAFE_TOOLS,
    WRITE_TOOLS,
    apply_pre_tool_guards,
)
from app.services.ai_phases import (
    Phase,
    admitted_tool_names,
    apply_phase_gate,
    note_evidence,
    note_failed_attempt,
    note_guard_block,
    note_progress,
    note_verified,
    reclassify_last_attempt,
    resolve_phase,
)
from app.services.ai_tools.lint_diff import ChangeContext as _ChangeContext
from app.services.ai_prompts import PAGE_DISCOVERY_PROMPT, SYSTEM_PROMPT
from app.services.ai_flow_state import (
    FlowState,
    build_flow_state,
    is_local_draft_flow_id as _is_local_draft_flow_id,
    render_flow_state,
    sync_state_message,
)
from app.services.ai_tool_events import attach_tool_events, current_verification_status, reduce_evidence_state
from app.services.ai_evidence_ledger import load_verification_state, record_events
from app.services.ai_tools import TOOL_SCHEMAS, RpaToolExecutor
from app.services.ai_tools.diagnostics import SELECTOR_DIAGNOSTIC_KINDS
from app.services.node_semantics import TRANSFORM_NODE_TYPES
from app.services.ai_tools.lint import is_blocking_finding

logger = logging.getLogger(__name__)

# 防止上游卡死导致前端一直停在"正在思考…"
LLM_REQUEST_TIMEOUT = 90       # 连接/读取超时（秒）
FIRST_TOKEN_TIMEOUT = 60       # 等待首个 chunk 超时（秒）
STALL_TIMEOUT = 60             # 相邻 chunk 间隔超时（秒）


MAX_TOOL_ROUNDS = 30  # strong 模型的轮次上限；weak/standard 模型用下方 tier 分级覆盖更小的值

# 场景化 guidance，按事件注入，避免每轮携带全量指令


def _after_write_directive(result: dict[str, Any], state: GuardState) -> str:
    """写入成功后的下一步，由结果与本轮授权推导，不靠模型记忆一串固定顺序。

    这里不再复述静态诊断。写入完成后下一轮的状态块就是按新 revision 重算的，
    在这里再列一遍只是同一份结论的第二个副本——两份一旦不一致（写入返回是写入那一刻的，
    状态块是下一轮读到的），模型无从判断该信哪个。
    """
    revision = result.get("revision")
    head = f"变更已写入并生效{f'（revision {revision}）' if revision is not None else ''}。"
    if result.get("connectivity_warning"):
        # 连通性是写入前后对比才看得出来的（孤儿分支、断掉的 foreach），
        # 静态诊断读单份定义看不到，所以这条必须在这里讲
        return (
            f"{head}但检测到连通性问题：{result.get('connectivity_warning')}\n"
            "先补连线，再谈运行。"
        )
    if not state.run_authorized:
        return (
            f"{head}本轮用户只要求修复、没有要求运行：向用户说明改了什么、为什么，"
            "并问是否要运行验证。不要调用 run_flow。"
        )
    return (
        f"{head}状态块里还有 severity=error 的诊断就先修完；"
        "已经干净了就调用 run_flow 验证，"
        "凭据未就绪时按状态块的提示让用户先填，不要编造凭据值。"
    )


# 运行成功后：验收结论已经在返回里，只剩「看产物」和「据实汇报」
_GUIDANCE_AFTER_RUN_SUCCESS = (
    "运行成功，返回里的 acceptance_audit 就是平台按流程冻结的验收契约算出的结论。"
    "acceptance_audit.passed=true 才能向用户汇报完成；要看产物本身调 get_run_output。"
)

# 运行失败后：get_run_error → 按错误类型修复
_GUIDANCE_AFTER_RUN_ERROR = (
    "运行失败。调用 get_run_error 获取 failed_node_id 和 error_logs，按类型处理：\n"
    "• inspect_hint 非空 → 编排层已锁定，必须先调 inspect_page(url=last_browser_url)，禁止直接改 selector。\n"
    "• Timeout / selector 定位失败 → 调 inspect_page 取真实 DOM，用 apply_node_fix 更新 selector。\n"
    "• ModuleNotFoundError → 用内置库重写 script 节点 code，删除第三方 import。\n"
    "• 变量未定义 → 状态块里已列出引用该变量的节点，apply_node_fix 补填 outputVariable。\n"
    "• 其他 → 按错误信息修复后重新 run_flow。"
)

# 质量审计失败后：按 repair_plan 修复结构
_GUIDANCE_AFTER_AUDIT_FAIL = (
    "acceptance_audit.passed=false。编排层已锁定下次 run_flow。\n"
    "必须按 acceptance_audit.repair_plan 修复流程结构后再重新 run_flow；"
    "审计由平台自己算，你无从「再审一次」，改流程是唯一出路。"
)

# 这些拦截意味着「工具走不下去了，得让用户拿主意」，与那些只是改道的拦截不同。
# 不含 ask_user：那是「活干完了、只剩一个决定」，走 _GUIDANCE_AFTER_ASK_USER +
# closing_statement_only，收尾话要由模型自己写，不能套这里的模板。
_TERMINAL_GUARD_ACTIONS = frozenset({
    "report_to_user_and_stop",
    "needs_user_navigation_target",
})
_TERMINAL_TOOL_STATUSES = frozenset({
    "blocked_page_access",
    "blocked_challenge_page",
    "blocked_browser_profile_busy",
})


def _is_terminal_result(result: dict[str, Any]) -> bool:
    """本轮到此为止、需要用户参与：编排层判定继续调工具不会推进任务。

    两处 after-tool 通道（引导注入与 state 回写）隔十几行先后跑同一判断，收成一处
    具名谓词，免得将来往名单里加词时只改一处、两条通道就此分叉。
    """
    return (
        result.get("required_action") in _TERMINAL_GUARD_ACTIONS
        or result.get("status") in _TERMINAL_TOOL_STATUSES
    )


_GUIDANCE_AFTER_TERMINAL_BLOCK = (
    "本轮到此结束：编排层判定继续调用工具不会推进任务，需要用户参与。\n"
    "接下来只输出面向用户的自然语言收尾，不要再调用任何工具，内容包含：\n"
    "1. 已经做了什么、卡在哪一步；\n"
    "2. 你判断的根因；\n"
    "3. 需要用户提供什么信息或做什么决定才能继续。\n"
    "工具结果里的 user_message 是给用户看的原话，可直接引用或改写。"
)

# ask_user 与上面那条的区别：这里没有任何东西卡住，修复已经落盘，只是「要不要运行」
# 这个决定归用户。用同一段文案会让模型去汇报一个不存在的阻塞点。
_GUIDANCE_AFTER_ASK_USER = (
    "本轮到此结束：改动已经写入生效，剩下的决定归用户。\n"
    "接下来只输出面向用户的自然语言收尾，不要再调用任何工具，内容包含：\n"
    "1. 改了哪些节点的哪些字段、为什么这么改（用工具返回的真实字段值，不要复述意图）；\n"
    "2. 这次改动还没有经过运行验证，明确说出来；\n"
    "3. 问用户要不要现在运行一遍验证。\n"
    "不要下「已修复」「问题已解决」这类结论——没有运行证据支撑。"
)

# 用户提出修复意图时注入，引导模型走诊断优先路径
_GUIDANCE_BEFORE_REPAIR = (
    "用户要求修复流程。强制执行顺序：\n"
    "1. 读状态块里的诊断列表，确认 issue 类型（不必再调工具查，那份就是当前结论）\n"
    "2. inspect_page(scope_selector=相关区域) → 获取 interaction_recipe 和真实 selector\n"
    "   （诊断里没有 browser/selector 类问题时可跳过步骤 2）\n"
    "3. 按 repair_plan + interaction_recipe 修复节点\n"
    "直接调用 apply_node_fix / update_flow 会被编排层阻断，直到页面证据到手。\n"
    "收到 repair_plan 后直接执行修复，不需要向用户解释或等待确认。"
)

_GUIDANCE_PRESERVE_EXECUTION_CHANNEL = (
    "当前流程已经有可用的浏览器采集主链路（browser.open/browser.wait/browser.extract 等）。"
    "用户这次只是在原流程基础上补充约束或报告局部问题，必须理解为“在现有流程上增量修复”，"
    "不是授权重构流程或切换执行通道。禁止删除已有浏览器主链路节点；禁止把浏览器采集节点改成 script.python；"
    "禁止用 urllib/requests/httpx/aiohttp/urlopen 等脚本 HTTP 请求抓网页来替代浏览器流程。"
    "正确做法是在原节点基础上新增/微调节点（如分页循环、条件判断、重试）来解决用户反馈的具体问题。"
    "如果确实需要把浏览器流程改成纯 Python/Scrapling/HTTP 方案，这是执行通道切换，必须先向用户说明并等待明确确认，"
    "不能因为浏览器采集遇到困难就自行静默切换方案。"
)

# 用户消息中表明修复意图的关键字（覆盖任意修复场景，不绑定具体问题类型）
_REPAIR_INTENT_KEYWORDS = frozenset({
    "修复", "fix", "repair", "帮我修", "帮我改", "修一下", "改一下", "重建", "优化",
    "报错", "跑不起来", "跑不通", "运行失败", "又失败", "卡住", "不能用",
    "分页", "多页", "下一页", "翻页", "加载更多", "pagination", "next page", "load more",
    "抓不全", "抓不到", "缺数据", "数据不全", "漏数据", "没抓全",
})

# 同一句话里给出了运行/验收授权。命中即 run_authorized。
#
# 为什么必须有这一条：修复词与验收词天天同时出现（「优化一下然后跑一遍验收」既含"优化"也含"验收"）。
# 只看修复词就整轮硬禁 run_flow，模型拿不到任何运行证据，只能交「静态检查通过；未做运行验证」，
# 用户看不到答案就再问一遍——实测同一句「流程审查验收」被重复发过 7 次。
# 这是"用户已经授权"，不是放宽护栏：没有这类措辞时锁照挂。
#
# 收词从严：run_flow 会真的打开浏览器操作目标站点，宁可漏判让用户补一句。
# 因此不收「试试」「验证」这类兼有别义的词（"试试改成 xpath"、"人机验证"），
# 也不收单独的「审查」——静态审查本身就能交付结论，它不必然要求运行。
_RUN_AUTHORIZATION_RE = re.compile(
    r"(验收|核对|跑一下|跑一次|跑一遍|跑通|跑起来看|运行一下|运行一次|运行看|运行验证|"
    r"执行一下|重跑|重新运行|再跑|测一下|测试一下|能不能用|是否可用|run it)",
    re.IGNORECASE,
)

# 用户显式要求切换执行通道时才不触发"保留浏览器主链路"guard——因为这次是用户主动
# 要求换方案，不是 AI 自行决定。
#
# 用子串列表（如旧版的 "用 python"）会误放行："用 python 处理一下提取到的数据" 这类
# 请求只是想加个数据清洗脚本，并没有放弃浏览器采集，却会命中子串并关掉整个 guard。
# 改为要求"切换动词"（改用/换成/不用...）与"目标通道词"（脚本/python/http/api...）
# 同时出现，才判定为用户明确要求切换执行通道。
_CHANNEL_SWITCH_VERB_PATTERN = re.compile(
    r"(不用|不要|别用|换成|换用|改用|改成|直接用|直接调用|直接发)"
)
_CHANNEL_SWITCH_TARGET_PATTERN = re.compile(
    r"(浏览器|脚本|python|http|api|curl|requests|urllib|httpx|aiohttp|接口请求|接口抓)",
    re.IGNORECASE,
)


def _is_explicit_channel_switch_request(user_text_lower: str) -> bool:
    return bool(
        _CHANNEL_SWITCH_VERB_PATTERN.search(user_text_lower)
        and _CHANNEL_SWITCH_TARGET_PATTERN.search(user_text_lower)
    )

# 用户消息中表明创建新流程意图的关键字（配合 URL 检测要求先看页面）
_CREATE_INTENT_KEYWORDS = frozenset({
    "创建", "新建", "生成流程", "生成一个", "帮我做", "做一个", "建一个", "写一个",
    "帮我创", "帮我生成", "自动化", "爬取", "抓取", "登录", "流程",
    "create", "make", "build", "generate", "scrape", "automate",
})

_RESUME_TASK_RE = re.compile(
    r"(继续|接着|重试|再试|重新检查|页面已打开|已经打开|已完成|完成了|登录好了|验证好了|继续创建|continue|retry)",
    re.IGNORECASE,
)
_NEW_TASK_RE = re.compile(r"(另一个|另外一个|换一个|换个网站|新的网址|新任务)")

# build_tool 随入口而变：全新会话是 create_flow，Studio 空白流程里提需求则是 update_flow
def _build_guidance_before_create(build_tool: str) -> str:
    return (
        "用户要求搭建流程，且消息包含目标 URL。当前流程还没有任何实质节点，"
        "这是一次从零构建，不是闲聊也不是无关问题——不得以「只能协助处理 RPA 流程」之类的话术回绝。\n"
        "强制执行顺序：\n"
        "1. inspect_page(url=登录页或目标页URL) → 获取真实 selector、表单结构、登录方式\n"
        "   - 若有登录：先 inspect 登录页，了解用户名/密码输入框 selector 和登录按钮\n"
        "   - 若有数据页：再 inspect 目标数据页，了解表格/筛选控件 selector\n"
        f"2. 基于 inspect_page 返回的真实 DOM 调用 {build_tool} — 所有 selector 必须来自检查结果\n"
        f"直接调用 {build_tool} 而不先 inspect_page 会被编排层阻断。\n"
        "目标 URL 重定向到登录页属于正常情况：说明需要先登录，按登录链路建节点，不要就此收尾。\n"
        "完成页面检查后直接落节点，不需要向用户解释或等待确认。"
    )

# lint 结果中需要 inspect_page 才能确认真实 selector 的 issue 类型
_BROWSER_SELECTOR_ISSUES = frozenset({
    "date_filter_missing_verification",
    "date_trigger_selector_too_broad",
    "table_extract_selector_targets_container",
    "table_extract_selector_not_table_like",
    "extract_selector_union_used_as_fallback",
    "table_extract_selector_too_broad",
    "dropdown_escape_bound_to_unstable_input",
    "invalid_attribute_extract_syntax",
})


# Tier 由 config/model_catalog.json 的 tier 字段定义；未在 catalog 中的模型默认 standard。
_WEAK_TOOL_ROUNDS = 12
_STANDARD_TOOL_ROUNDS = 20


def _demo_date_controls() -> list[dict[str, Any]]:
    """few-shot 里的 date_controls 直接由 skill registry 生成，避免示例配方与真实配方各改各的。"""
    from app.services.skills.registry import build_skill_recipe, match_skills

    demo_inputs = [
        {"placeholder": "开始日期", "label": "创建时间", "selector": "input[placeholder='开始日期']"},
        {"placeholder": "结束日期", "label": "创建时间", "selector": "input[placeholder='结束日期']"},
    ]
    return [
        {
            "type": f"{s.library}/{s.component}",
            "library": s.library,
            "component": s.component,
            "description": s.description,
            "interaction_recipe": build_skill_recipe(s, demo_inputs),
        }
        for s in match_skills(["el-date-range-picker"])
    ]


def _build_few_shot_messages() -> list[dict[str, Any]]:
    """真实验证过的一次会话作为 few-shot：登录检测→日期选择→多选→表格提取→校验。

    站点域名与凭据一律用 mock：这段每轮都随请求发给模型厂商。
    """
    _nodes: list[dict[str, Any]] = [
        {"id": "start",   "type": "start",             "title": "开始",               "kind": "control", "status": "pending", "position": {"x": 100, "y": 100}},
        {"id": "n1",      "type": "browser.ensureLogin", "title": "探测登录态",       "kind": "browser", "status": "pending", "position": {"x": 100, "y": 200},  "targetUrl": "https://erp.demo-rpa.test/", "selector": ".side-bar-container, nav", "targetSelector": "input[type='password']", "firstValueVariable": "login_status", "delayMs": 3000, "description": "持久 Cookie 有效则跳过登录 → login_status"},
        {"id": "n5",      "type": "control.condition", "title": "判断是否需要登录",   "kind": "control", "status": "pending", "position": {"x": 100, "y": 600},  "inputValue": "login_status == 'login_required'", "description": "login_required → 走登录分支"},
        {"id": "n6",      "type": "browser.fill",      "title": "填写账号",           "kind": "browser", "status": "pending", "position": {"x": 320, "y": 700},  "selector": "input[placeholder='请输入用户名']", "inputValue": "${var.username}", "delayMs": 800, "description": "填入 ${var.username}"},
        {"id": "n7",      "type": "browser.fill",      "title": "填写密码",           "kind": "browser", "status": "pending", "position": {"x": 320, "y": 800},  "selector": "input[placeholder='请输入密码']", "inputValue": "${var.password}", "delayMs": 500, "description": "填入 ${var.password}"},
        {"id": "n8_input", "type": "variable.input",    "title": "输入验证码",         "kind": "variable", "status": "pending", "position": {"x": 680, "y": 850},  "message": "请查看浏览器中的图形验证码并输入", "variableName": "captcha", "description": "运行时收集当次验证码 → captcha"},
        {"id": "n8_fill", "type": "browser.fill",      "title": "填写验证码",         "kind": "browser", "status": "pending", "position": {"x": 320, "y": 900},  "selector": "input[placeholder='请输入验证码']", "inputValue": "${var.captcha}", "delayMs": 500, "description": "填入 ${var.captcha}"},
        {"id": "n9",      "type": "browser.click",     "title": "点击登录按钮",       "kind": "browser", "status": "pending", "position": {"x": 320, "y": 1000}, "selector": "button:has-text('登录')", "delayMs": 2000, "description": "提交登录表单"},
        {"id": "n10",     "type": "browser.wait",      "title": "等待登录后导航栏",   "kind": "browser", "status": "pending", "position": {"x": 320, "y": 1100}, "selector": ".side-bar-container, nav", "timeoutMs": 15000, "description": "等应用壳出现，确认登录成功"},
        {"id": "n12",     "type": "browser.open",      "title": "打开项目列表页",     "kind": "browser", "status": "pending", "position": {"x": 100, "y": 1200}, "targetUrl": "https://erp.demo-rpa.test/#/project/list", "delayMs": 3000, "description": "合流后导航到目标数据页"},
        {"id": "n13",     "type": "browser.wait",      "title": "等待表格加载",       "kind": "browser", "status": "pending", "position": {"x": 100, "y": 1300}, "selector": "table, .el-table__body", "timeoutMs": 15000, "description": "等表格渲染完成"},
        {"id": "n14",     "type": "browser.fill",      "title": "填写开始日期",       "kind": "browser", "status": "pending", "position": {"x": 100, "y": 1400}, "selector": "input[placeholder='开始日期']", "inputValue": "${var.date_start}", "fillMode": "type", "timeoutMs": 15000, "delayMs": 500, "description": "键入开始日期；键盘输入才会触发组件的 change 提交，且与运行当天无关"},
        {"id": "n15",     "type": "browser.fill",      "title": "填写结束日期",       "kind": "browser", "status": "pending", "position": {"x": 100, "y": 1500}, "selector": "input[placeholder='结束日期']", "inputValue": "${var.date_end}", "fillMode": "type", "timeoutMs": 15000, "delayMs": 500, "description": "键入结束日期"},
        {"id": "n16",     "type": "browser.press",     "title": "提交日期区间",       "kind": "browser", "status": "pending", "position": {"x": 100, "y": 1600}, "selector": "input[placeholder='结束日期']", "inputValue": "Enter", "delayMs": 800, "description": "回车提交区间；打在输入框上而非 body，组件的按键处理挂在输入框自身"},
        {"id": "n17a",    "type": "browser.extract",   "title": "回读开始日期",       "kind": "browser", "status": "pending", "position": {"x": 100, "y": 1700}, "selector": "input[placeholder='开始日期']", "extractMode": "attribute", "attribute": "value", "firstValueVariable": "selected_start_date", "outputVariable": "selected_start_dates", "timeoutMs": 8000, "includeInResult": False, "description": "回读输入框实际值"},
        {"id": "n17b",    "type": "browser.extract",   "title": "回读结束日期",       "kind": "browser", "status": "pending", "position": {"x": 100, "y": 1800}, "selector": "input[placeholder='结束日期']", "extractMode": "attribute", "attribute": "value", "firstValueVariable": "selected_end_date", "outputVariable": "selected_end_dates", "timeoutMs": 8000, "includeInResult": False, "description": "回读输入框实际值"},
        {"id": "n17c",    "type": "script.python",     "title": "校验日期筛选生效",   "kind": "script",  "status": "pending", "position": {"x": 100, "y": 1900}, "timeoutMs": 10000, "inputVariables": ["date_start", "date_end", "selected_start_date", "selected_end_date"], "description": "日期没写进组件时页面会返回全量数据，这里硬失败，不允许 continueOnError", "code": "import json, os\n_vars = json.loads(os.environ.get('RPA_VARIABLES_JSON', '{}'))\nexpected = (_vars.get('date_start', ''), _vars.get('date_end', ''))\nactual = (_vars.get('selected_start_date', ''), _vars.get('selected_end_date', ''))\nif actual != expected:\n    raise SystemExit(f'日期筛选未生效：期望 {expected}，实际 {actual}')\nprint(json.dumps({'date_filter': f'{actual[0]}~{actual[1]}'}, ensure_ascii=False))\n"},
        {"id": "n18",     "type": "browser.click",     "title": "打开项目进度下拉",   "kind": "browser", "status": "pending", "position": {"x": 100, "y": 2000}, "selector": ".el-select:has-text('项目进度') .el-select__tags", "description": "展开项目进度多选"},
        {"id": "n18b",    "type": "browser.wait",      "title": "等下拉选项渲染",     "kind": "browser", "status": "pending", "position": {"x": 100, "y": 2100}, "selector": ".el-select-dropdown__item", "timeoutMs": 5000, "description": "等选项出现再点，别用固定延时赌渲染速度"},
        {"id": "n19",     "type": "browser.click",     "title": "选择项目通过",       "kind": "browser", "status": "pending", "position": {"x": 100, "y": 2200}, "selector": ".el-select-dropdown__item:has-text('项目通过')", "delayMs": 500, "description": "勾选「项目通过」"},
        {"id": "n20",     "type": "browser.click",     "title": "选择待尽调",         "kind": "browser", "status": "pending", "position": {"x": 100, "y": 2300}, "selector": ".el-select-dropdown__item:has-text('待尽调')", "delayMs": 500, "description": "勾选「待尽调」"},
        {"id": "n21",     "type": "browser.click",     "title": "点击查询按钮",       "kind": "browser", "status": "pending", "position": {"x": 100, "y": 2400}, "selector": "button:has-text('查询')", "delayMs": 2000, "description": "提交筛选条件；按钮用文本定位，不挂库专属 class"},
        {"id": "n22",     "type": "browser.wait",      "title": "等待筛选结果",       "kind": "browser", "status": "pending", "position": {"x": 100, "y": 2500}, "selector": "table, .el-table__body", "timeoutMs": 10000, "description": "等筛选后的表格刷新"},
        {"id": "n23",     "type": "browser.paginateNext", "title": "翻页提取全部数据", "kind": "browser", "status": "pending", "position": {"x": 100, "y": 2600}, "selector": ".audit-table-wrapper .el-pagination button.btn-next", "targetSelector": ".audit-table-wrapper tbody tr", "extractMode": "table", "outputVariable": "project_data", "countVariable": "project_table_count", "pageCountVariable": "project_page_count", "maxIterations": 20, "delayMs": 1500, "description": "从当前页起逐页累计提取，翻页按钮消失/禁用即停 → project_data"},
        {"id": "n24",     "type": "file.write",        "title": "保存结果",           "kind": "file",    "status": "pending", "position": {"x": 100, "y": 2700}, "path": "${var.output_prefix}.json", "content": "${var.project_data}", "description": "写出 JSON 到本次运行目录"},
        {"id": "end",     "type": "end",               "title": "结束",               "kind": "control", "status": "pending", "position": {"x": 100, "y": 2800}},
    ]
    _edges: list[dict[str, Any]] = [
        {"id": "e_start_n1",  "source": "start",   "target": "n1"},
        {"id": "e_n1_n5",     "source": "n1",      "target": "n5"},
        {"id": "e_n5_n6",     "source": "n5",      "target": "n6",      "label": "true"},
        {"id": "e_n5_n12",    "source": "n5",      "target": "n12",     "label": "false"},
        {"id": "e_n6_n7",     "source": "n6",      "target": "n7"},
        {"id": "e_n7_n8input", "source": "n7",      "target": "n8_input"},
        {"id": "e_n8input_n8fill", "source": "n8_input", "target": "n8_fill"},
        {"id": "e_n8fill_n9", "source": "n8_fill", "target": "n9"},
        {"id": "e_n9_n10",    "source": "n9",      "target": "n10"},
        {"id": "e_n10_n12",   "source": "n10",     "target": "n12"},
        {"id": "e_n12_n13",   "source": "n12",     "target": "n13"},
        {"id": "e_n13_n14",   "source": "n13",     "target": "n14"},
        {"id": "e_n14_n15",   "source": "n14",     "target": "n15"},
        {"id": "e_n15_n16",   "source": "n15",     "target": "n16"},
        {"id": "e_n16_n17a",  "source": "n16",     "target": "n17a"},
        {"id": "e_n17a_n17b", "source": "n17a",    "target": "n17b"},
        {"id": "e_n17b_n17c", "source": "n17b",    "target": "n17c"},
        {"id": "e_n17c_n18",  "source": "n17c",    "target": "n18"},
        {"id": "e_n18_n18b",  "source": "n18",     "target": "n18b"},
        {"id": "e_n18b_n19",  "source": "n18b",    "target": "n19"},
        {"id": "e_n19_n20",   "source": "n19",     "target": "n20"},
        {"id": "e_n20_n21",   "source": "n20",     "target": "n21"},
        {"id": "e_n21_n22",   "source": "n21",     "target": "n22"},
        {"id": "e_n22_n23",   "source": "n22",     "target": "n23"},
        {"id": "e_n23_n24",   "source": "n23",     "target": "n24"},
        {"id": "e_n24_end",   "source": "n24",     "target": "end"},
    ]
    _ivs: list[dict[str, Any]] = [
        {"name": "username", "type": "String", "value": "", "category": "credential"},
        {"name": "password", "type": "String", "value": "", "category": "credential", "sensitive": True},
        {"name": "date_start", "type": "String", "value": "2026-06-01", "category": "flow"},
        {"name": "date_end",   "type": "String", "value": "2026-06-24", "category": "flow"},
    ]
    # 两份 inspect 结果的字段严格对齐 PAGE_PROBE_JS 的真实返回，示例编造字段会让模型
    # 去读运行时根本不存在的 key（曾编造 page_title / login_form / sample_row_count）
    _inspect_login = json.dumps({
        "requested_url": "https://erp.demo-rpa.test/",
        "url": "https://erp.demo-rpa.test/#/login",
        "title": "示例项目管理系统",
        "inputs": [
            {"tag": "input", "type": "text",     "name": "username", "id": None, "placeholder": "请输入用户名", "label": "用户名", "selector": "input[placeholder='请输入用户名']"},
            {"tag": "input", "type": "password", "name": "password", "id": None, "placeholder": "请输入密码",   "label": "密码",   "selector": "input[placeholder='请输入密码']"},
            {"tag": "input", "type": "text",     "name": "captcha",  "id": None, "placeholder": "请输入验证码", "label": "验证码", "selector": "input[placeholder='请输入验证码']"},
        ],
        "selects": [],
        "buttons": [{"text": "登 录", "type": "submit", "selector": "button:has-text('登录')"}],
        "tables": [],
        "page_classes": ["el-form", "el-input", "el-button", "login-container", "login-form-panel"],
    }, ensure_ascii=False)
    _inspect_list = json.dumps({
        "requested_url": "https://erp.demo-rpa.test/#/project/list",
        "url": "https://erp.demo-rpa.test/#/project/list",
        "title": "示例项目管理系统",
        "inputs": [
            {"tag": "input", "type": "text", "name": None, "id": None, "placeholder": "开始日期", "label": "创建时间", "selector": "input[placeholder='开始日期']"},
            {"tag": "input", "type": "text", "name": None, "id": None, "placeholder": "结束日期", "label": "创建时间", "selector": "input[placeholder='结束日期']"},
        ],
        "selects": [],
        "buttons": [
            {"text": "查询",   "type": "button", "selector": "button:has-text('查询')"},
            {"text": "重置",   "type": "button", "selector": "button:has-text('重置')"},
            {"text": "下一页", "type": "button", "selector": ".audit-table-wrapper .el-pagination button.btn-next"},
        ],
        "tables": [{
            "headers": ["项目名称", "创建时间", "项目进度", "负责人"],
            "container_selector": ".audit-table-wrapper .el-table",
            "cls": "el-table audit-table",
            "row_selector": ".audit-table-wrapper tbody tr",
        }],
        "page_classes": ["el-table", "el-pagination", "el-date-editor", "el-date-range-picker", "el-select", "el-select-dropdown", "audit-table-wrapper", "side-bar-container"],
        "date_controls": _demo_date_controls(),
    }, ensure_ascii=False)
    _flow_id = "11111111-2222-4333-8444-555555555555"
    _task_id = "fs-demo-task-001"
    _rows = [
        {"项目名称": "示例项目 A", "创建时间": "2026-06-03", "项目进度": "项目通过", "负责人": "张三"},
        {"项目名称": "示例项目 B", "创建时间": "2026-06-07", "项目进度": "待尽调",   "负责人": "李四"},
        {"项目名称": "示例项目 C", "创建时间": "2026-06-11", "项目进度": "项目通过", "负责人": "王五"},
        {"项目名称": "示例项目 D", "创建时间": "2026-06-15", "项目进度": "待尽调",   "负责人": "赵六"},
        {"项目名称": "示例项目 E", "创建时间": "2026-06-19", "项目进度": "项目通过", "负责人": "张三"},
        {"项目名称": "示例项目 F", "创建时间": "2026-06-23", "项目进度": "待尽调",   "负责人": "李四"},
    ]
    _contract = {
        "requirements": [{
            "id": "requested-filters",
            "description": "按用户指定日期范围和项目进度抓取项目列表",
            "sourceKind": "user",
            "sourceQuote": "筛选创建时间 2026-06-01 至 2026-06-24，项目进度为「项目通过/待尽调」",
            "confidence": 1,
            "confirmed": True,
        }],
        "deliverables": [{
            "id": "project-list",
            "variable": "project_data",
            "kind": "table",
            "minRows": 1,
            "requiredFields": ["项目名称", "创建时间", "项目进度", "负责人"],
            "dateRanges": [{"field": "创建时间", "start": "2026-06-01", "end": "2026-06-24"}],
            "allowedValues": [{"field": "项目进度", "values": ["项目通过", "待尽调"]}],
            "requirementIds": ["requested-filters"],
        }],
    }
    _create_result   = json.dumps({"flow_id": _flow_id, "name": "项目列表抓取-筛选", "status": "draft", "revision": 1, "acceptance_contract": _contract, "lint_findings": []}, ensure_ascii=False)
    # 验收结论跟着 run_flow 一起回来，示例里也必须是这个形状：模型没有审计工具可调，
    # 样例若还演示「再调一次审计」，它就会去找一个不存在的工具。
    _audit = {
        "task_id": _task_id, "passed": True, "flow_revision": 1,
        "deliverables": [{"id": "project-list", "variable": "project_data", "kind": "table"}],
        "issues": [], "warnings": [],
        "message": "运行产物满足验收契约。",
    }
    _run_result    = json.dumps({"task_id": _task_id, "status": "success", "flow_id": _flow_id, "flow_revision": 1, "progress": {"current_step": 26, "total_steps": 26, "percent": 100, "elapsed_ms": 41200}, "acceptance_audit": _audit}, ensure_ascii=False)
    _output_result = json.dumps({
        "task_id": _task_id, "status": "success",
        "summary": "运行成功，共输出 6 个变量、1 个产物文件。",
        "variables": {"login_status": "logged_in", "selected_start_date": "2026-06-01", "selected_end_date": "2026-06-24", "project_page_count": 2, "project_table_count": 6, "project_data": _rows},
        "artifacts": [{"filename": "project-list-20260624-101500.json", "type": "json"}],
    }, ensure_ascii=False)

    return [
        {"role": "user", "content": "帮我抓取 https://erp.demo-rpa.test/ 项目列表。筛选创建时间 2026-06-01 至 2026-06-24，项目进度为「项目通过/待尽调」。需要账号密码登录，运行时还有图形验证码。"},
        # 每个要交互的页面都单独探一次：登录页给出表单 selector，数据页给出 row_selector 与日期控件配方
        {"role": "assistant", "content": None, "tool_calls": [{"id": "fs_t1", "type": "function", "function": {"name": "inspect_page", "arguments": json.dumps({"url": "https://erp.demo-rpa.test/", "wait_selector": "input[type='password'], nav"})}}]},
        {"role": "tool", "tool_call_id": "fs_t1", "content": _inspect_login},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "fs_t2", "type": "function", "function": {"name": "inspect_page", "arguments": json.dumps({"url": "https://erp.demo-rpa.test/#/project/list", "wait_selector": "table, .el-table"})}}]},
        {"role": "tool", "tool_call_id": "fs_t2", "content": _inspect_list},
        # 示例站点恰好是 Element UI，下面的 el- selector 全部来自这次 inspect_page 的 page_classes。
        # 不写明出处的话，模型会把它们当成通用模板抄到 Arco/Vant/自研站点上去。
        {"role": "assistant", "content": "`page_classes` 里是 el- 前缀，说明这个站点用的是 Element UI，所以下面的表格/下拉/翻页 selector 按 Element UI 的类名取。换一个站点这些类名一律不成立，必须重新 `inspect_page` 拿它自己的 `page_classes` 和 `tables[].row_selector`。", "tool_calls": [{"id": "fs_t3", "type": "function", "function": {"name": "create_flow", "arguments": json.dumps({"name": "项目列表抓取-筛选", "description": "登录后筛选日期范围+项目进度，翻页提取项目列表", "input_variables": _ivs, "nodes": _nodes, "edges": _edges, "acceptance_contract": _contract}, ensure_ascii=False)}}]},
        {"role": "tool", "tool_call_id": "fs_t3", "content": _create_result},
        # 写完不复检：下一轮开头的状态块已经带着这份定义的诊断结果，再调一次读取工具
        # 只会拿回同一个答案。
        {"role": "assistant", "content": "流程已创建，状态块显示静态检查无阻断项。请先在右侧「输入变量」面板配置账号和密码；运行到验证码步骤时会弹出输入框。"},
        {"role": "user", "content": "我已在输入变量面板配置好账号和密码，请继续运行并验收。"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "fs_t5", "type": "function", "function": {"name": "run_flow", "arguments": json.dumps({"flow_id": _flow_id})}}]},
        {"role": "tool", "tool_call_id": "fs_t5", "content": _run_result},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "fs_t6", "type": "function", "function": {"name": "get_run_output", "arguments": json.dumps({"task_id": _task_id})}}]},
        {"role": "tool", "tool_call_id": "fs_t6", "content": _output_result},
        {"role": "assistant", "content": "已创建并验证通过。翻页抓取 2 页共 6 条项目记录，创建时间均落在 2026-06-01 至今天，项目进度全部为「项目通过」或「待尽调」；结果已写入 project-list-20260624-101500.json。"},
    ]


_FEW_SHOT_MESSAGES: list[dict[str, Any]] = _build_few_shot_messages()
_FEW_SHOT_CREATE_SIGNALS = frozenset((
    "生成", "创建", "新建", "帮我抓取", "抓取", "采集", "导出", "保存为excel", "保存为 excel",
    "爬取", "爬一下", "帮我做", "做一个", "搞一个", "自动化", "定时抓", "监控",
))
_FEW_SHOT_WEB_SIGNALS = frozenset((
    "http://", "https://", "网页", "页面", "表格", "筛选", "登录", "验证码", "分页", "excel",
    "网站", "列表页", "后台", "管理系统",
))
_FEW_SHOT_COMPLEX_SIGNALS = frozenset((
    "登录", "验证码", "日期", "筛选", "分页", "翻页", "excel", "下拉", "多选",
))
_FEW_SHOT_REPAIR_SIGNALS = frozenset((
    "修复", "报错", "失败", "审查", "分析", "优化", "重命名", "改名", "删除",
    "跑不起来", "跑不通", "不能用", "怎么错了", "为什么错", "运行不了", "卡住",
))


def _should_inject_few_shot(messages: list[dict[str, Any]]) -> bool:
    """只在“新建网页抓取流程”场景注入样例，避免审查/修复类任务被成功流程模式带偏。

    只看最后一句：拼整个会话的话，第一轮的「帮我建流程」会一直生效到后面每一轮。
    """
    user_text = _latest_user_message(messages).lower()
    if not user_text:
        return False
    if any(signal in user_text for signal in _FEW_SHOT_REPAIR_SIGNALS):
        return False
    return (
        bool(_URL_IN_TEXT_RE.search(user_text))
        and any(signal in user_text for signal in _FEW_SHOT_CREATE_SIGNALS)
        and any(signal in user_text for signal in _FEW_SHOT_WEB_SIGNALS)
        and any(signal in user_text for signal in _FEW_SHOT_COMPLEX_SIGNALS)
    )


# 弱模型额外注入的开篇提示，让模型在有限轮次内尽量聚焦
_WEAK_MODEL_PREAMBLE = (
    "【精简模式·轮次有限】严格按顺序操作，不要跳步或重复调用同一工具：\n"
    "① inspect_page 获取真实 selector\n"
    "② create_flow / update_flow 构建或修改流程\n"
    "③ 按状态块的诊断列表修完所有阻断项（不要调工具去复查，状态块每轮都是最新的）\n"
    "④ run_flow，看返回里的 acceptance_audit.passed 再汇报\n"
    "字段不确定时用 list_node_types(types=[...]) 只查所需节点；selector 失效时调用 inspect_page。禁止盲猜。"
)


_relay_models_cache: dict[str, tuple[list[str], float]] = {}
_RELAY_CACHE_TTL = 300  # seconds

# (base_url, api_key) -> AsyncOpenAI。客户端按配置复用：一次 30 轮的编排若每轮
# 新建 AsyncOpenAI + httpx.AsyncClient 且从不关闭，会持续泄漏连接池/socket。
_relay_client_cache: dict[str, Any] = {}


def _get_relay_client(base_url: str, api_key: str) -> Any | None:
    """按 (base_url, api_key) 缓存的 AsyncOpenAI 客户端，剥离遥测请求头。"""
    cache_key = f"{base_url}|{api_key}"
    client = _relay_client_cache.get(cache_key)
    if client is not None:
        return client
    try:
        import httpx as _httpx
        import openai as _openai
    except Exception:
        return None  # openai 包不可用时回退到普通 litellm

    class _CleanRelay(_httpx.AsyncHTTPTransport):
        """剥离 SDK 遥测请求头，替换部分中转商会拦截的 user-agent。"""

        async def handle_async_request(self, request: "_httpx.Request") -> "_httpx.Response":
            clean = [
                (k, b"python-httpx/0.27" if k.lower() == b"user-agent" else v)
                for k, v in request.headers.raw
                if k.decode().lower() not in _STRIP_HEADERS
            ]
            request = _httpx.Request(
                method=request.method,
                url=request.url,
                headers=clean,
                stream=request.stream,
                extensions=request.extensions,
            )
            return await super().handle_async_request(request)

    client = _openai.AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=_httpx.AsyncClient(transport=_CleanRelay()),
    )
    _relay_client_cache[cache_key] = client
    return client

# OpenAI SDK 注入的、部分中转商会拦截的请求头；user-agent 单独替换
_STRIP_HEADERS = frozenset({
    "x-stainless-lang", "x-stainless-package-version", "x-stainless-os",
    "x-stainless-arch", "x-stainless-runtime", "x-stainless-runtime-version",
    "x-stainless-async", "x-stainless-raw-response", "x-stainless-retry-count",
    "x-stainless-read-timeout",
})


def _normalize_base_url(url: str | None) -> str | None:
    """确保 base_url 以 /v1 结尾，供 LiteLLM 拼接 /v1/chat/completions。"""
    if not url:
        return url
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url = url + "/v1"
    return url


async def _fetch_relay_models(base_url: str, api_key: str) -> list[str]:
    """中转上架的模型 ID；空列表表示没问出来（网络失败、非 200、或该密钥无权列举）。

    空和「确实一个模型都没有」不可分，所以空列表只能当作无证据，不能拿来判某模型不存在。
    """
    import httpx

    cache_key = f"{base_url}|{api_key}"
    cached = _relay_models_cache.get(cache_key)
    if cached and time.monotonic() - cached[1] < _RELAY_CACHE_TTL:
        return cached[0]

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        relay_models = [m["id"] for m in r.json().get("data", []) if m.get("id")] if r.status_code == 200 else []
    except Exception:
        relay_models = []
    _relay_models_cache[cache_key] = (relay_models, time.monotonic())
    return relay_models


async def _resolve_relay_model(model: str, base_url: str, api_key: str) -> str:
    """在中转 base_url 下从其实际提供的模型中匹配最接近的一个，按 openai/ 前缀路由。"""
    relay_models = await _fetch_relay_models(base_url, api_key)

    if not relay_models:
        bare = model.split("/", 1)[-1]
        return f"openai/{bare}"

    non_chat = (
        "embed", "whisper", "tts", "audio", "dall-e", "dalle", "image",
        "moderation", "rerank", "vision-ocr", "stable-diffusion", "flux",
        "realtime", "codex",
    )
    chat_models = [rm for rm in relay_models if not any(kw in rm.lower() for kw in non_chat)]
    if not chat_models:
        chat_models = relay_models  # 中转只有非标准模型时的兜底

    # 无 provider 前缀的 OpenAI 原生 ID（如 "gpt-5.4-mini"）取首个 "-" 段作为 family
    bare = model.split("/", 1)[-1]
    if "/" in model:
        family = model.split("/", 1)[0].lower()
    else:
        family = bare.split("-")[0].lower()

    for rm in chat_models:
        if rm == bare:
            return f"openai/{rm}"
    for rm in chat_models:
        if rm.startswith(bare):
            return f"openai/{rm}"
    # 按名称倒序，优先匹配高版本/latest
    family_matches = sorted(
        [rm for rm in chat_models if family in rm.lower()],
        reverse=True,
    )
    if family_matches:
        return f"openai/{family_matches[0]}"
    return f"openai/{sorted(chat_models, reverse=True)[0]}"


def _split_partial_tag_suffix(text: str, tag: str) -> tuple[str, str]:
    """若 text 以 tag 的真前缀结尾（如 "<thi"——标签被流式 chunk 边界劈开），
    把该前缀扣下留到拼上下一个 chunk 后再判定，返回 (可安全发出的部分, 扣下的部分)。"""
    for plen in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:plen]):
            return text[:-plen], text[-plen:]
    return text, ""


_SCREENSHOT_PLACEHOLDER = "[截图已省略：当前模型不支持图片输入，请改用 inspect_page]"


def _strip_image_messages(messages: list[dict[str, Any]]) -> bool:
    """将视觉内容块替换为文本占位符；返回是否有替换发生。"""
    stripped = False
    for m in messages:
        content = m.get("content")
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "image_url" for part in content
        ):
            texts = [
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            m["content"] = ("\n".join(t for t in texts if t) or "") + "\n" + _SCREENSHOT_PLACEHOLDER
            stripped = True
    return stripped


def _build_system_message(
    model: str,
    relayed: bool,
    prompt: str = SYSTEM_PROMPT,
) -> dict[str, Any]:
    """构造系统消息，Anthropic 原生端点额外打一个提示词缓存断点。

    Anthropic 的缓存前缀按 tools → system → messages 累积，断点打在 system 上会把当前阶段
    的工具 Schema 一并缓存。OpenAI/DeepSeek 自动缓存，不需要标记；中转端点是否透传
    cache_control 不可知，按普通字符串发。
    """
    if relayed or not _model_caps(model).supports_cache_control:
        return {"role": "system", "content": prompt}
    return {
        "role": "system",
        "content": [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}],
    }


def _build_few_shot_block(model: str, relayed: bool) -> list[dict[str, Any]]:
    """few-shot 序列，Anthropic 原生端点在末条上再打一个缓存断点。

    few-shot 排在 system 断点之后、所有真实历史之前，内容恒定且 _compact_tool_messages
    不会改写它，是第二段稳定可缓存的前缀；不打断点则这 1.5 万字符每轮原价重发。
    再往后就没得缓存了——历史每轮都被压缩改写，断点命中不了。
    """
    if relayed or not _model_caps(model).supports_cache_control:
        return _FEW_SHOT_MESSAGES
    # 复制末条：_FEW_SHOT_MESSAGES 是模块级共享对象，就地改会污染其它厂商的请求
    tail = dict(_FEW_SHOT_MESSAGES[-1])
    tail["content"] = [
        {"type": "text", "text": str(tail["content"]), "cache_control": {"type": "ephemeral"}}
    ]
    return [*_FEW_SHOT_MESSAGES[:-1], tail]


@dataclass
class _SessionMeter:
    """累计一次会话的轮次、token 与工具用量，随流推给前端。

    数字只进日志的话，用户无从判断"再让它试一次"值不值——而多轮自愈恰恰是这里最贵的路径。
    缓存命中要单列：同样的轮数，命中与否差出几倍成本。
    """

    rounds: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    tool_calls: int = 0
    blocked_calls: int = 0
    llm_seconds: float = 0.0

    def add_round(self, usage: Any, elapsed_s: float) -> None:
        self.rounds += 1
        self.llm_seconds += elapsed_s
        if usage is None:
            return
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details is not None else None
        self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        self.cached_tokens += int(cached or 0)

    def snapshot(self, max_rounds: int) -> dict[str, Any]:
        return {
            "rounds": self.rounds,
            "max_rounds": max_rounds,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "tool_calls": self.tool_calls,
            "blocked_calls": self.blocked_calls,
            "llm_seconds": round(self.llm_seconds, 1),
        }


def _log_prompt_cache_usage(model: str, round_num: int, usage: Any, elapsed_s: float | None = None) -> None:
    """记录每轮输入规模、缓存命中量与耗时。

    缓存数为 0 也照记：只打印非零命中会让缓存完全没生效的会话一条日志都没有，
    正好是最需要排查的那种。
    """
    if usage is None:
        return
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    created = getattr(usage, "cache_creation_input_tokens", None)
    logger.info(
        "llm round (model=%s round=%s): 输入 %s tokens（缓存命中 %s / 写入 %s），输出 %s tokens，耗时 %s",
        model, round_num,
        getattr(usage, "prompt_tokens", "?"), cached or 0, created or 0,
        getattr(usage, "completion_tokens", "?"),
        f"{elapsed_s:.1f}s" if elapsed_s is not None else "?",
    )


# 每轮请求前展示的状态文案，按上一轮实际执行的工具归类，替代千篇一律的
# "正在分析结果…"——多轮自愈（创建→试运行→诊断→修复→再运行）常持续数分钟，
# 用户需要能感知到 AI 具体处于哪个阶段。
_ROUND_STATUS_BY_TOOL: dict[str, str] = {
    "create_flow": "正在规划流程结构…",
    "update_flow": "正在验证变更方案…",
    "apply_node_fix": "正在验证节点修复…",
    "run_flow": "正在分析运行结果…",
    "get_run_error": "正在定位失败原因…",
    "get_run_output": "正在核对运行产物…",
    "get_run_logs": "正在阅读运行日志…",
    "inspect_page": "正在解读页面结构…",
    "inspect_screenshot": "正在查看页面截图…",
    "publish_flow": "正在完成发布…",
    "list_node_types": "正在查询可用节点…",
}


def _next_round_status_text(last_tool_name: str | None) -> str:
    if last_tool_name is None:
        return "正在分析结果…"
    return _ROUND_STATUS_BY_TOOL.get(last_tool_name, "正在分析结果…")


# 工具执行期间的状态文案。上面那张表说的是「拿到结果之后在干什么」，
# 而 run_flow 这类工具本身要跑几分钟，这几分钟里面板必须说清楚在等什么。
_EXECUTING_STATUS_BY_TOOL: dict[str, str] = {
    "run_flow": "正在运行流程（浏览器已启动，通常需要 1–3 分钟）…",
    "inspect_page": "正在打开页面抓取真实结构…",
    "inspect_screenshot": "正在截取页面…",
    "get_run_output": "正在读取输出变量与产物…",
    "get_run_logs": "正在拉取运行日志…",
    "create_flow": "正在写入流程…",
    "update_flow": "正在写入变更…",
    "apply_node_fix": "正在修改节点…",
    "publish_flow": "正在发布…",
}


def _executing_status_text(tool_name: str) -> str:
    return _EXECUTING_STATUS_BY_TOOL.get(tool_name, f"正在执行 {tool_name}…")


class _ThinkTagFilter:
    """把嵌在正文里的 <think>…</think> 拆成可见文本与思维链。

    DeepSeek-R1、非 API-thinking 模式的 Qwen3 不走独立的 reasoning_content 字段。
    """

    def __init__(self) -> None:
        self._in_think = False
        # 标签可能被 chunk 边界劈开（"<thi"+"nk>"），结尾的疑似标签前缀先扣下
        self._carry = ""

    def feed(self, delta: str) -> list[tuple[str, str]]:
        """返回 [(事件类型, 文本)]，事件类型为 "text" 或 "thinking"。"""
        events: list[tuple[str, str]] = []
        raw = self._carry + delta
        self._carry = ""
        visible = ""
        while raw:
            if not self._in_think:
                idx = raw.find("<think>")
                if idx == -1:
                    raw, self._carry = _split_partial_tag_suffix(raw, "<think>")
                    visible += raw
                    break
                visible += raw[:idx]
                self._in_think = True
                raw = raw[idx + 7:]
            else:
                idx = raw.find("</think>")
                if idx == -1:
                    raw, self._carry = _split_partial_tag_suffix(raw, "</think>")
                    if raw:
                        events.append(("thinking", raw))
                    break
                frag = raw[:idx]
                if frag:
                    events.append(("thinking", frag))
                self._in_think = False
                raw = raw[idx + 8:].lstrip("\n")
        if visible:
            events.append(("text", visible))
        return events

    def flush(self) -> list[tuple[str, str]]:
        """流结束：扣下的疑似标签前缀并不是完整标签，按当前状态原样补发。"""
        if not self._carry:
            return []
        events = [("thinking" if self._in_think else "text", self._carry)]
        self._carry = ""
        return events


def _site_knowledge_message(
    messages: list[dict[str, Any]], flow_state: FlowState
) -> dict[str, Any] | None:
    """用户消息或当前流程含已知域名时，注入该站点沉淀的 selector/框架/登录特征与已证伪的写法。"""
    try:
        from app.services.site_knowledge import get_site_knowledge_store
        store = get_site_knowledge_store()
        text = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")
        if flow_state.nodes:
            # 节点配置里的 targetUrl / selector 是域名的另一处来源：用户这句话里可能
            # 只说了「修一下」，站点是谁全靠流程本身告诉我们。
            text += "\n" + json.dumps(flow_state.nodes, ensure_ascii=False)
        profiles = store.match_text(text)
        if not profiles:
            return None
        return {"role": "system", "content": store.build_context_message(profiles)}
    except Exception:
        return None  # 经验注入失败不影响正常对话


_URL_IN_TEXT_RE = re.compile(r'https?://[^\s,，。？！\]）)]+')


@dataclass
class _TurnIntents:
    repair: bool = False
    preserve_execution_channel: bool = False
    create_requested: bool = False
    # 非 None 即检测到创建意图，值为需求里的首个 URL
    create_url: str | None = None
    # 用户这一句里已经授权运行（验收/跑一下/核对…）
    run_authorized: bool = False


@dataclass
class _ResumableTaskState:
    """从完整会话恢复的当前任务事实；只保存用户原话和真实工具结果，不保存模型推断。"""

    requirement_text: str = ""
    target_url: str | None = None
    target_source: str | None = None
    phase: str | None = None
    last_inspection_status: str | None = None
    last_inspection_source: str | None = None
    resume_requested: bool = False


def _tool_call_payload(call: dict[str, Any], key: str) -> dict[str, Any]:
    raw = call.get(key)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _latest_page_inspection(
    messages: list[dict[str, Any]],
) -> tuple[str | None, str | None, str | None]:
    """读取最近一次真实页面检查；工具结果比模型正文更适合作为恢复依据。"""
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        calls = message.get("toolCalls") or message.get("tool_calls") or []
        for call in reversed(calls if isinstance(calls, list) else []):
            if not isinstance(call, dict) or call.get("tool") != "inspect_page":
                continue
            args = _tool_call_payload(call, "args")
            result = _tool_call_payload(call, "result")
            url = str(
                result.get("requested_url")
                or result.get("url")
                or args.get("url")
                or ""
            ).strip()
            status = str(result.get("status") or "").strip() or None
            if status is None and result:
                status = "error" if result.get("error") else "success"
            source = str(result.get("inspection_source") or "").strip() or None
            return (url or None), status, source
    return None, None, None


def _resolve_resumable_task_state(
    messages: list[dict[str, Any]],
    flow_id: str | None,
    flow_state: FlowState,
) -> _ResumableTaskState:
    latest = _latest_user_message(messages)
    state = _ResumableTaskState(
        requirement_text=_session_requirement_text(messages),
        resume_requested=bool(_RESUME_TASK_RE.search(latest)),
    )
    current_urls = _URL_IN_TEXT_RE.findall(latest)
    inspected_url, inspected_status, inspected_source = _latest_page_inspection(messages)

    # 证据通道要在提前返回之前认下：下面的分支解决的是「这轮该去看哪个 URL」，
    # 而「上次那页是靠哪条通道拿到的」跟流程建没建好无关。放到返回之后，
    # 流程一存下来护栏就凭空消失，模型接着改 fetcher 或加 browser.click 无人阻拦。
    # 这轮自带新 URL 就不认：那条通道结论说的是上一个站点。
    if not current_urls:
        state.last_inspection_source = inspected_source
    if flow_id and not flow_state.is_blank:
        return state

    if current_urls:
        state.target_url = current_urls[0]
        state.target_source = "current_message"
    elif state.resume_requested and not _NEW_TASK_RE.search(latest):
        if inspected_url:
            state.target_url = inspected_url
            state.target_source = "inspection_history"
            state.last_inspection_status = inspected_status
            state.last_inspection_source = inspected_source
        else:
            for message in reversed(messages[:-1]):
                if message.get("role") != "user":
                    continue
                content = message.get("content")
                if not isinstance(content, str):
                    continue
                urls = _URL_IN_TEXT_RE.findall(content)
                if urls:
                    state.target_url = urls[0]
                    state.target_source = "conversation_history"
                    break

    if state.target_url:
        if state.last_inspection_status == "success":
            state.phase = "page_inspected"
        elif state.last_inspection_status in _TERMINAL_TOOL_STATUSES:
            state.phase = "page_inspection_blocked"
        else:
            state.phase = "awaiting_page_inspection"
    return state


def _task_state_message(state: _ResumableTaskState) -> dict[str, Any] | None:
    if not state.target_url or state.target_source == "current_message":
        return None
    payload = {
        "goal": state.requirement_text[:_SESSION_REQUIREMENT_MAX_CHARS],
        "target_url": state.target_url,
        "phase": state.phase,
        "last_inspection_status": state.last_inspection_status,
        "last_inspection_source": state.last_inspection_source,
        "next_action": (
            "build_flow"
            if state.phase == "page_inspected"
            else "retry_page_inspection" if state.resume_requested else "inspect_page"
        ),
    }
    return {
        "role": "system",
        "content": (
            "【当前可恢复任务状态】以下字段来自本会话用户原话和真实工具结果，不是模型推断。"
            "当前消息是在继续原任务，不要再次索取已存在的 URL 或需求：\n"
            + json.dumps(payload, ensure_ascii=False)
        ),
    }


def _detect_turn_intents(
    messages: list[dict[str, Any]],
    flow_id: str | None,
    flow_state: FlowState,
    task_state: _ResumableTaskState | None = None,
) -> _TurnIntents:
    intents = _TurnIntents()
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not last_user:
        return intents
    user_text = str(last_user.get("content") or "")
    user_text_lower = user_text.lower()

    if flow_id and any(kw in user_text_lower for kw in _REPAIR_INTENT_KEYWORDS):
        intents.repair = True
    # 用户明确说了要跑/要验收就是授权，即使同一句里还带着修复词。
    # 显式的「不要运行」优先级最高：它是撤回授权，不是没给授权。
    intents.run_authorized = bool(
        _RUN_AUTHORIZATION_RE.search(user_text)
        and not any(phrase in user_text for phrase in _NO_RUN_REQUEST_PHRASES)
    )
    # 结构性 guard，故意不靠关键字门控：用户描述问题的措辞（"抓不全"/"内容少了一半"等）
    # 是关键字列表永远无法穷举的集合。
    if flow_id and flow_state.has_browser_chain and not _is_explicit_channel_switch_request(user_text_lower):
        intents.preserve_execution_channel = True
    # 只看 `not flow_id` 会漏掉「Studio 新建流程后再对 AI 提需求」这个最常见入口。
    if (not flow_id or flow_state.is_blank) and not intents.repair:
        resolved = task_state or _resolve_resumable_task_state(messages, flow_id, flow_state)
        intents.create_requested = (
            any(kw in user_text_lower for kw in _CREATE_INTENT_KEYWORDS)
            or (resolved.resume_requested and resolved.target_url is not None)
        )
        urls = _URL_IN_TEXT_RE.findall(user_text)
        if urls and intents.create_requested:
            intents.create_url = urls[0]
        elif intents.create_requested and resolved.target_url and not _NEW_TASK_RE.search(user_text):
            intents.create_url = resolved.target_url
    return intents


def _tool_schemas_for_round(
    state: GuardState, intents: _TurnIntents
) -> list[dict[str, Any]]:
    """只暴露当前阶段与当前能力下能推进任务的工具。

    两类扣除，来源不同：
    - 阶段准入由 ai_phases 唯一持有（暴露了却会被拦，等于故意让模型白花一轮）；
    - 能力扣除是调用方授权（read_only）和模型自身能力（无视觉）——它们整轮固定，
      不随阶段变化。这两条原来只有拦截没有隐藏，于是每轮都要先被模型试一次：
      只读模式下它交出 update_flow，无视觉模型交出 inspect_screenshot，各烧一整轮。
      能力缺失不是模型该去发现的事实，schema 里不出现就不会被调用。

    拦截仍然保留在 `ai_guards`：只读是调用方给的授权边界，不能只靠"没暴露"来守
    （工具名是模型能凭记忆猜出来的，schema 之外的调用也照样会到达执行器）。
    """
    if state.terminal_response_only or state.closing_statement_only:
        return []
    if intents.create_requested and not intents.create_url:
        return []
    available = [
        schema for schema in TOOL_SCHEMAS
        if str(schema.get("function", {}).get("name") or "") not in _unavailable_tools(state)
    ]
    all_names = frozenset(
        str(schema.get("function", {}).get("name") or "") for schema in available
    )
    admitted = admitted_tool_names(all_names, state)
    if admitted == all_names:
        return available
    return [
        schema for schema in available
        if schema.get("function", {}).get("name") in admitted
    ]


def _unavailable_tools(state: GuardState) -> frozenset[str]:
    """本轮从头到尾都用不了的工具，与阶段无关。"""
    unavailable: set[str] = set()
    if state.read_only_tools:
        unavailable |= WRITE_TOOLS
    if state.model_no_vision:
        unavailable.add("inspect_screenshot")
    return frozenset(unavailable)


def _system_prompt_for_round(state: GuardState) -> str:
    """页面探测完成前只加载探测契约；DOM 到手后再加载完整构建规则。

    只对「从零建流程」这条路生效。修复路径上的取证阶段仍需完整规则：模型看完 DOM
    紧接着就要改节点，换成探测契约等于把构建规则从它手上拿走。
    """
    if resolve_phase(state) is Phase.DISCOVER and not state.flow_has_nodes:
        return PAGE_DISCOVERY_PROMPT
    return SYSTEM_PROMPT


def _terminal_tool_response(tool_name: str, result: Any) -> str | None:
    """把终止类工具结果转换为确定性用户回复，避免再烧一轮模型上下文。"""
    guidance, _ = _after_tool_guidance(tool_name, result)
    if guidance is None or not isinstance(result, dict):
        return None

    error = str(result.get("error") or "").strip()
    user_message = str(result.get("user_message") or result.get("message") or "").strip()
    if error and user_message and error != user_message:
        return f"**当前无法继续。** {error}\n\n{user_message}"
    detail = user_message or error
    return f"**当前无法继续。** {detail}" if detail else "**当前无法继续，需要用户处理后再试。**"


def _after_tool_guidance(
    tool_name: str, result: Any, state: GuardState | None = None
) -> tuple[str | None, bool]:
    """返回 (要注入的系统引导, 是否跳过本轮剩余的并行调用)。"""
    if not isinstance(result, dict):
        return None, False
    state = state or GuardState()
    if result.get("required_action") == "ask_user":
        return _GUIDANCE_AFTER_ASK_USER, True
    if _is_terminal_result(result):
        # 拦截本身不产出正文。不明确要求收尾，模型常常直接空轮结束，
        # 用户只看到一个空气泡，既不知道被挡住了也不知道该给什么。
        return _GUIDANCE_AFTER_TERMINAL_BLOCK, True
    if tool_name == "create_flow" and _tool_call_succeeded(result) and result.get("flow_id"):
        return _after_write_directive(result, state), True
    if tool_name == "update_flow" and _tool_call_succeeded(result):
        return _after_write_directive(result, state), True
    if tool_name == "apply_node_fix" and _tool_call_succeeded(result):
        return _after_write_directive(result, state), False
    if tool_name == "run_flow":
        status = result.get("status")
        if status == "success":
            audit = result.get("acceptance_audit")
            if isinstance(audit, dict) and audit.get("passed") is False:
                return _GUIDANCE_AFTER_AUDIT_FAIL, False
            return _GUIDANCE_AFTER_RUN_SUCCESS, False
        if status == "error":
            return _GUIDANCE_AFTER_RUN_ERROR, False
    return None, False


class _MissingApiKeyError(Exception):
    """缺 key 时给出可操作提示，而不是让 litellm 抛一堆栈。"""


class AiOrchestrator:
    def __init__(self, tool_executor: RpaToolExecutor, config_service: AiConfigService | None = None) -> None:
        self._executor = tool_executor
        self._config_service = config_service

    async def _completion_kwargs(self, model: str) -> tuple[str, dict[str, Any]]:
        """解析中转地址与鉴权，返回 (实际请求的模型名, litellm 额外参数)。"""
        raw_base_url = self._config_service.get_base_url_for_model(model) if self._config_service else None
        api_key = self._config_service.get_api_key_for_model(model) if self._config_service else None
        # LiteLLM 直接把 /chat/completions 拼到 base_url 后面，须以 /v1 结尾
        base_url = _normalize_base_url(raw_base_url)

        # 提前给出清晰的缺 key 提示；配置了中转时跳过（中转自行处理鉴权）
        if not api_key and self._config_service and not base_url:
            from app.services.ai_config_service import AI_MODEL_CATALOG
            required_env_key = next(
                (m.get("env_key", "") for m in AI_MODEL_CATALOG if m["id"] == model), ""
            )
            if required_env_key and not os.environ.get(required_env_key):
                raise _MissingApiKeyError(
                    f"模型 {model} 未配置 API Key（需要 {required_env_key}），请在设置页添加。"
                )
        # 中转未配置 key 时用占位符，供 litellm 组出合法 Authorization 头
        if base_url and not api_key:
            api_key = "sk-relay"

        extra: dict[str, Any] = {}
        effective_model = model
        if base_url and api_key:
            effective_model = await _resolve_relay_model(model, base_url, api_key)
        if base_url:
            extra["base_url"] = base_url
            relay_client = _get_relay_client(base_url, api_key or "sk-relay")
            if relay_client is not None:
                extra["client"] = relay_client
        if api_key:
            extra["api_key"] = api_key
        return effective_model, extra

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        flow_id: str | None = None,
        read_only: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式输出一轮 assistant 回复。

        read_only=True 用于自愈诊断等无人值守场景：允许全部诊断类工具，
        阻断 create_flow / update_flow / apply_node_fix / run_flow / publish_flow。
        """
        try:
            import litellm
        except ImportError:
            yield {"type": "error", "message": "litellm 未安装，请运行 pip install litellm"}
            yield {"type": "done"}
            return

        relayed = bool(self._config_service and self._config_service.get_base_url_for_model(model))
        full_messages: list[dict[str, Any]] = [_build_system_message(model, relayed)]
        if _should_inject_few_shot(messages):
            full_messages.extend(_build_few_shot_block(model, relayed))

        flow_state = FlowState(flow_id=flow_id)
        if flow_id:
            yield {"type": "status", "delta": "正在读取流程…"}
            flow_state = await build_flow_state(self._executor, flow_id)

        task_state = _resolve_resumable_task_state(messages, flow_id, flow_state)
        resumable_state_message = _task_state_message(task_state)
        if resumable_state_message:
            full_messages.append(resumable_state_message)

        # 静态前缀边界：超预算丢弃历史时，system/few-shot 与恢复状态不能被丢掉。
        # 流程定义刻意不在这里面——它每轮在消息尾部重建，保护一份过期副本毫无意义。
        protect_prefix = len(full_messages)
        context_budget = _context_char_budget(model)

        full_messages.extend(_expand_history_tool_calls(messages))

        site_knowledge = _site_knowledge_message(messages, flow_state)
        if site_knowledge:
            full_messages.append(site_knowledge)

        intents = _detect_turn_intents(messages, flow_id, flow_state, task_state)

        # 按模型分级：weak 模型注入精简开篇提示且轮次更少
        tier = _model_caps(model).tier
        if tier == "weak":
            full_messages.insert(1, {"role": "system", "content": _WEAK_MODEL_PREAMBLE})
            protect_prefix += 1
            effective_max_rounds = _WEAK_TOOL_ROUNDS
        elif tier == "standard":
            effective_max_rounds = _STANDARD_TOOL_ROUNDS
        else:
            effective_max_rounds = MAX_TOOL_ROUNDS

        # 防打转护栏的计数原本随每条用户消息清零，"还是不行"再来一轮就又能试同样的方案。
        # 台账把它们跨会话续上，并把历史尝试直接告诉模型。
        ledger = _repair_ledger.load(flow_id)
        ledger_summary = _repair_ledger.summarize(ledger)
        if ledger_summary:
            full_messages.insert(protect_prefix, {"role": "system", "content": ledger_summary})
            protect_prefix += 1

        evidence_state = load_verification_state(flow_id, flow_state.revision, flow_state.definition_digest)
        guard_state = GuardState(
            flow_id=flow_id,
            # 验收台账事实：current_flow_revision / run_verified_revision / accepted_revision
            **evidence_state,
            repair_sessions=int(ledger.get("sessions") or 0) + 1,
            node_field_history=dict(ledger.get("node_field_history") or {}),
            node_selector_fix_counts=dict(ledger.get("node_selector_fix_counts") or {}),
            # 阶段机读的事实。这里给的是「本轮没有特殊意图」时的局面：证据不作要求、
            # 运行不设限（run_authorized=True）；下面的意图接线才会逐项收紧。
            # blocking_diagnostics 每轮由状态块的诊断集重算，见主循环。
            flow_has_nodes=not flow_state.is_blank,
            page_evidence_done=task_state.phase == "page_inspected",
            run_authorized=True,
            # 本轮请求已被判定为「建流程 / 修流程 / 要运行」，即职责范围之内。
            # 拿拒答模板收尾会被撤回重写（见 _misapplied_refusal）。
            turn_intent_actionable=bool(
                intents.repair or intents.create_requested or intents.run_authorized
            ),
            page_evidence_source=task_state.last_inspection_source,
            read_only_tools=read_only,     # 自愈诊断模式：阻断所有写入类工具
            model_no_vision=not _model_caps(model).supports_vision,  # 阻断 inspect_screenshot
            # full_messages 里混着 few-shot 那轮虚构的 user 消息
            user_requirement_text=task_state.requirement_text,
            latest_user_message=_latest_user_message(messages),
            active_task={
                "target_url": task_state.target_url,
                "phase": task_state.phase,
                "last_inspection_status": task_state.last_inspection_status,
                "last_inspection_source": task_state.last_inspection_source,
            },
        )

        # 上一轮被中断（用户点停止、断流、关窗）时留下的预算与未了结义务。
        # 放在初始化之后覆盖：默认值是"这轮从零开始"，检查点存在才说明不是。
        checkpoint = _session_checkpoint.load(flow_id)
        if checkpoint:
            guard_state.apply_checkpoint(checkpoint)
            resume_note = _session_checkpoint.summarize(checkpoint)
            if resume_note:
                full_messages.insert(protect_prefix, {"role": "system", "content": resume_note})
                protect_prefix += 1

        if intents.repair:
            # 是否真要页面证据按当前诊断集判：selector/元素类问题必须真去看 DOM，
            # 纯变量或拓扑问题去抓一次页面纯属浪费。运行期报出 selector 失败时
            # （get_run_error 的 inspect_hint）会在 _orchestrator_guard_after_tool 里补置位。
            if any(f.get("issue") in _BROWSER_SELECTOR_ISSUES for f in flow_state.findings):
                guard_state.page_evidence_required = {"reason": "repair_touches_page_elements"}
                # 修复请求要的是「现在页面长什么样」。历史上探过一次不算——
                # 上次探测之后流程和站点都可能变了，那份 DOM 支撑不了这次判断。
                guard_state.page_evidence_done = False
            # 刻意不进 _PERSISTED_KEYS：只锁本轮。用户下一句往往就是「跑一下看看」，
            # 那时 repair 关键词不再出现，锁自然不会重新挂上。
            # 同一句里已给出运行授权时不挂：否则「修完跑一遍验收」这类既报问题又要结论的
            # 请求会被结构性地判成「不许运行」，模型只能交静态检查，用户拿不到答案。
            guard_state.run_authorized = bool(intents.run_authorized)
            full_messages.append({"role": "system", "content": _GUIDANCE_BEFORE_REPAIR})
        if intents.preserve_execution_channel:
            guard_state.repair_intent = "preserve_execution_channel"
            guard_state.browser_chain_node_ids = flow_state.browser_chain_node_ids
            full_messages.append({"role": "system", "content": _GUIDANCE_PRESERVE_EXECUTION_CHANNEL})

        if intents.create_url:
            # 空白流程已有 flow_id，该走 update_flow 落节点而不是再建一个
            build_tool = "update_flow" if flow_id and not _is_local_draft_flow_id(flow_id) else "create_flow"
            guard_state.page_evidence_required = {
                "url": intents.create_url,
                "reason": "build_from_page",
            }
            guard_state.page_evidence_done = task_state.phase == "page_inspected"
            full_messages.append({"role": "system", "content": _build_guidance_before_create(build_tool)})

        vision_fallback_done = False
        last_tool_name: str | None = None
        consecutive_empty_rounds = 0
        meter = _SessionMeter()
        repeated_results: dict[tuple[str, str], str] = {}
        last_verification_status = current_verification_status(guard_state)
        last_verification_revision = guard_state.current_flow_revision
        last_run: dict[str, Any] | None = None

        for round_num in range(effective_max_rounds):
            if round_num == 0:
                yield {"type": "status", "delta": "正在思考…"}
            else:
                yield {"type": "status", "delta": _next_round_status_text(last_tool_name)}

            # 每轮请求前压缩旧的大体积工具结果，控制上下文规模。
            _compact_tool_messages(full_messages, context_budget, protect_prefix)
            # 无条件重建，不做「看起来没变就跳过」的优化：用户可能在画布上直接改了流程，
            # 运行状态也会自己往前走。任何「我觉得它没变」的判断都是过期状态的复发路径。
            if round_num > 0 and flow_id:
                flow_state = await build_flow_state(self._executor, flow_id, last_run)
            else:
                flow_state.last_run = last_run
            sync_state_message(full_messages, render_flow_state(flow_state))
            # 阶段由事实推导，所以事实必须每轮跟着状态块一起重算：用户可能在画布上
            # 直接补了节点，也可能把节点删空。存一份「上轮的阶段」就是第二份真相。
            guard_state.blocking_diagnostics = _blocking_diagnostics(flow_state, guard_state)
            guard_state.flow_has_nodes = not flow_state.is_blank
            _mark_history_cache_anchor(full_messages, model, relayed)
            collected_tool_calls: dict[int, dict[str, str]] = {}
            round_usage: Any = None
            collected_text: list[str] = []
            think_filter = _ThinkTagFilter()
            # 记录已发出 tool_start 的流式索引，避免工具执行后重复发卡片
            emitted_tool_starts: set[int] = set()
            round_started_at = time.monotonic()

            try:
                effective_model, extra = await self._completion_kwargs(model)
                full_messages[0] = _build_system_message(
                    model,
                    relayed,
                    _system_prompt_for_round(guard_state),
                )
                round_tools = _tool_schemas_for_round(guard_state, intents)
                completion_args: dict[str, Any] = {
                    "model": effective_model,
                    "messages": full_messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "drop_params": True,
                    "timeout": LLM_REQUEST_TIMEOUT,
                    **extra,
                }
                if round_tools:
                    completion_args["tools"] = round_tools
                    completion_args["tool_choice"] = "auto"
                response = await litellm.acompletion(
                    **completion_args,
                )
            except _MissingApiKeyError as key_exc:
                yield {"type": "error", "message": str(key_exc)}
                yield {"type": "done"}
                return
            except Exception as exc:
                # 视觉降级：模型拒绝图片输入时剥离截图块并重试一次。
                if not vision_fallback_done and is_vision_error(str(exc)) and _strip_image_messages(full_messages):
                    vision_fallback_done = True
                    yield {"type": "status", "delta": "当前模型不支持图片，已移除截图重试…"}
                    continue
                yield {"type": "error", "message": clean_litellm_error(str(exc))}
                yield {"type": "done"}
                return

            finish_reason: str | None = None
            collected_thinking: list[str] = []
            try:
                # 逐 chunk 手动迭代 + 看门狗：中转卡死（无首 token 或流中途停滞）时主动
                # 抛 TimeoutError，避免请求和 UI 无限期挂起。
                response_iter = response.__aiter__()
                got_first_chunk = False
                while True:
                    chunk_timeout = STALL_TIMEOUT if got_first_chunk else FIRST_TOKEN_TIMEOUT
                    try:
                        chunk = await asyncio.wait_for(response_iter.__anext__(), timeout=chunk_timeout)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        if got_first_chunk:
                            hint = f"模型在输出中途停止响应（超过 {STALL_TIMEOUT}s 无新内容），请重试或更换模型。"
                        else:
                            hint = (
                                f"模型 {effective_model} 超过 {FIRST_TOKEN_TIMEOUT}s 未返回任何内容。"
                                "可能是当前中转服务不可用或该模型已下线，请在设置页更换模型或中转地址后重试。"
                            )
                        logger.warning("LLM stream timeout (model=%s, got_first=%s)", effective_model, got_first_chunk)
                        yield {"type": "error", "message": hint}
                        yield {"type": "done"}
                        return
                    got_first_chunk = True
                    round_usage = getattr(chunk, "usage", None) or round_usage
                    if not chunk.choices:
                        continue  # 用量统计块不带 choices
                    choice = chunk.choices[0]
                    delta = choice.delta
                    finish_reason = choice.finish_reason or finish_reason

                    thinking_delta: str | None = getattr(delta, "reasoning_content", None)
                    if thinking_delta:
                        collected_thinking.append(thinking_delta)
                        yield {"type": "thinking", "delta": thinking_delta}

                    if delta.content:
                        for kind, text in think_filter.feed(delta.content):
                            (collected_thinking if kind == "thinking" else collected_text).append(text)
                            yield {"type": kind, "delta": text}

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in collected_tool_calls:
                                # call_id 独立于厂商的 tc.id：后者可能缺失或到得比 tool_start 晚，
                                # 而前端要靠它把 tool_args/tool_result 对到具体那张卡片上
                                collected_tool_calls[idx] = {
                                    "id": "", "name": "", "arguments": "", "call_id": f"r{round_num}_{idx}",
                                }
                            entry = collected_tool_calls[idx]
                            if tc.id:
                                entry["id"] = tc.id
                            if tc.function and tc.function.name:
                                entry["name"] = tc.function.name
                            if tc.function and tc.function.arguments:
                                entry["arguments"] += tc.function.arguments
                            # 工具名一确定就立即发 tool_start，无需等整个流结束
                            if idx not in emitted_tool_starts and entry["name"]:
                                emitted_tool_starts.add(idx)
                                yield {"type": "tool_start", "tool": entry["name"], "args": "", "call_id": entry["call_id"]}
                for kind, text in think_filter.flush():
                    (collected_thinking if kind == "thinking" else collected_text).append(text)
                    yield {"type": kind, "delta": text}
                round_elapsed = time.monotonic() - round_started_at
                _log_prompt_cache_usage(effective_model, round_num, round_usage, round_elapsed)
                meter.add_round(round_usage, round_elapsed)
                yield {"type": "usage", "usage": meter.snapshot(effective_max_rounds)}
            except Exception as stream_exc:
                if not vision_fallback_done and is_vision_error(str(stream_exc)) and _strip_image_messages(full_messages):
                    # 已 yield 的本轮部分文本无法撤回，重试后可能出现重复段落——
                    # 视觉错误几乎总在首 token 前抛出（请求校验阶段），实际影响可忽略。
                    vision_fallback_done = True
                    yield {"type": "status", "delta": "当前模型不支持图片，已移除截图重试…"}
                    continue
                yield {"type": "error", "message": clean_litellm_error(str(stream_exc))}
                yield {"type": "done"}
                return

            tool_calls = list(collected_tool_calls.values())

            # 部分模型/中转流式返回 tool_calls 时不带 id；空 tool_call_id 会让
            # 严格的 OpenAI 兼容端点在下一轮请求时拒绝整个对话，这里合成兜底 id。
            for _tc_idx, _tc in collected_tool_calls.items():
                if not _tc["id"]:
                    _tc["id"] = f"call_r{round_num}_{_tc_idx}"

            if tool_calls or collected_text or collected_thinking:
                consecutive_empty_rounds = 0

            if not tool_calls:
                if not collected_text and not collected_thinking:
                    # 允许连续空响应重试一次：长编排跑到第 N 轮时一次瞬时抖动不该
                    # 废掉整个会话；连续两次空响应才判定为真故障。
                    consecutive_empty_rounds += 1
                    if consecutive_empty_rounds == 1:
                        full_messages.append({
                            "role": "system",
                            "content": "你上一次返回了空响应，请重新处理用户的请求并给出回复或调用工具。",
                        })
                        continue
                    yield {"type": "error", "message": "模型连续返回空响应，请检查 API Key 或更换模型。"}
                    yield {"type": "done"}
                    return
                if not collected_text and collected_thinking:
                    thinking_text = "".join(collected_thinking)
                    yield {"type": "text", "delta": thinking_text}
                    # 这段是用户看到的回复，同样要过证据门
                    collected_text.append(thinking_text)
                final_text = "".join(collected_text)
                # 顺序即优先级：先判「该干的活被推掉了」——模型一旦拿拒答模板收尾，
                # 后两条会把它误诊成「结论越界」或「没做验证」，给出方向完全错的更正。
                # 其余两条之间，说法不实比交付不全严重。
                claim_correction: str | None = None
                retract_reason = "结论超出已有证据，正在重写"
                for _checker, _reason in (
                    (_misapplied_refusal, "该请求在职责范围内，正在重写"),
                    (_overstated_result_claim, "结论超出已有证据，正在重写"),
                    (_unmet_verification_request, "结论超出已有证据，正在重写"),
                ):
                    claim_correction = _checker(final_text, guard_state)
                    if claim_correction is not None:
                        retract_reason = _reason
                        break
                if claim_correction is not None:
                    full_messages.append({"role": "assistant", "content": final_text})
                    full_messages.append({"role": "system", "content": claim_correction})
                    # 越界结论已经流式吐给前端了，只追加更正会留下一段自相矛盾的回复，
                    # 让前端丢弃本条已渲染正文，由下一轮重写。
                    yield {"type": "retract", "reason": retract_reason}
                    yield {"type": "status", "delta": "正在核对结论依据…"}
                    continue
                # 拿到了最终回复，这一轮对话就算了结：预算是为「这次任务」设的，
                # 留到下一条消息会让新需求一上来就背着上次的熔断。
                _session_checkpoint.clear(flow_id)
                # 收尾再补一次：本轮的工具/阻断计数发生在上一次 usage 之后
                yield {"type": "usage", "usage": meter.snapshot(effective_max_rounds)}
                yield {"type": "done"}
                return

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(collected_text) or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in tool_calls
                ],
            }
            full_messages.append(assistant_msg)

            tool_items = list(collected_tool_calls.items())

            # 只读工具先并发起跑，串行循环走到它时直接取结果，省掉逐个 await 的串行往返。
            # 少于两个不预取：单个并发没收益，只多一层任务管理。
            prefetched: dict[int, asyncio.Task[Any]] = {}
            if sum(1 for _, _tc in tool_items if _tc["name"] in _PARALLEL_SAFE_TOOLS) > 1:
                for _pf_idx, _pf_tc in tool_items:
                    if _pf_tc["name"] not in _PARALLEL_SAFE_TOOLS:
                        continue
                    try:
                        _pf_args, _pf_dups = (
                            _parse_tool_arguments(_pf_tc["arguments"]) if _pf_tc["arguments"].strip() else ({}, [])
                        )
                    except json.JSONDecodeError:
                        continue  # 参数非法交给下面的串行分支报错，这里不抢着执行
                    if _pf_dups:
                        continue
                    prefetched[_pf_idx] = asyncio.create_task(
                        self._executor.execute(_pf_tc["name"], _pf_args, {})
                    )

            _stop_after: int | None = None
            terminal_response: str | None = None
            for _exec_idx, (stream_idx, tc) in enumerate(tool_items):
                tool_name = tc["name"]
                raw_args = tc["arguments"]

                if stream_idx in emitted_tool_starts:
                    yield {"type": "tool_args", "tool": tool_name, "args": raw_args, "call_id": tc["call_id"]}
                else:
                    yield {"type": "tool_start", "tool": tool_name, "args": raw_args, "call_id": tc["call_id"]}

                try:
                    args, _dup_arg_keys = _parse_tool_arguments(raw_args) if raw_args.strip() else ({}, [])
                except json.JSONDecodeError as json_exc:
                    args = None
                    result = {
                        "error": (
                            "工具参数被截断（模型输出达到最大长度），请精简参数或拆分为多次更小的调用后重试。"
                            if finish_reason == "length"
                            else f"工具参数不是合法 JSON：{json_exc}"
                        ),
                        "status": "error",
                    }
                else:
                    if _dup_arg_keys:
                        args = None
                        result = {
                            "status": "error",
                            "error": (
                                f"工具参数里 {_dup_arg_keys} 出现了多次，只有最后一份会生效，其余修改会被静默丢弃。"
                                f"{tool_name} 每次只作用于一个目标；要改多个目标请拆成多次调用，"
                                "或改用 update_flow 的 update_nodes 数组一次提交多个节点。"
                            ),
                            "duplicate_argument_keys": _dup_arg_keys,
                        }
                if args is not None:
                    try:
                        # after-guard 需要读取本次调用参数（如 selector 修改目标节点）
                        guard_state._last_tool_args = args
                        guard_result = _orchestrator_guard_before_tool(tool_name, args, guard_state)
                        if guard_result is not None:
                            result = guard_result
                        else:
                            # 轮次状态是进入本轮时定的（"正在思考…"），工具真正开跑后就过期了
                            yield {"type": "status", "delta": _executing_status_text(tool_name)}
                            # 用 task 执行工具并每 5s 发心跳，避免长时任务（run_flow 等）期间 SSE 看似冻结
                            progress_sink: dict[str, Any] = {}
                            # 只读工具可能已在本轮开头并发起跑了，直接接管那个任务
                            tool_task = prefetched.pop(stream_idx, None)
                            if tool_task is None:
                                tool_task = asyncio.create_task(
                                    self._executor.execute(
                                        tool_name, args, progress_sink,
                                        _build_change_context(guard_state),
                                    )
                                )
                            tool_started_at = time.monotonic()
                            while not tool_task.done():
                                try:
                                    await asyncio.wait_for(asyncio.shield(tool_task), timeout=5.0)
                                except asyncio.TimeoutError:
                                    yield {
                                        "type": "heartbeat",
                                        "tool": tool_name,
                                        "elapsed_s": int(time.monotonic() - tool_started_at),
                                        "progress": dict(progress_sink) or None,
                                    }
                            result = tool_task.result()
                    except Exception as exc:
                        result = {"error": str(exc), "status": "error"}

                # 截图结果特殊处理：base64 不进入 UI 卡片和 tool 消息文本，
                # 改以 vision content block 注入，让模型真正"看到"页面。
                _image_b64: str | None = None
                _image_media = "image/jpeg"
                if isinstance(result, dict) and result.get("image_base64"):
                    _image_b64 = result.pop("image_base64")
                    _image_media = result.get("image_media_type") or "image/jpeg"

                # 记账与落盘都排在 yield 之前：一旦让出去，用户点停止就会让这个生成器
                # 停在这里再也不往下走，而这次工具的代价（run_flow 常以分钟计）已经付掉了。
                last_tool_name = tool_name
                result = attach_tool_events(tool_name, result)
                reduce_evidence_state(guard_state, result)
                if tool_name in _RUN_STATE_TOOLS and _tool_call_succeeded(result):
                    # 下一轮的状态块要讲「最近一次运行怎么样了」，来源就是这里
                    last_run = result
                if isinstance(result, dict):
                    event_flow_id = result.get("flow_id") or guard_state.flow_id
                    if isinstance(event_flow_id, str):
                        guard_state.flow_id = event_flow_id
                        record_events(event_flow_id, [
                            event for event in (result.get("events") or []) if isinstance(event, dict)
                        ])
                meter.tool_calls += 1
                if isinstance(result, dict) and str(result.get("status", "")).startswith("blocked_"):
                    meter.blocked_calls += 1
                _orchestrator_guard_after_tool(tool_name, result, guard_state)
                _session_checkpoint.save(flow_id, guard_state, rounds=meter.rounds)

                yield {"type": "tool_result", "tool": tool_name, "result": result, "call_id": tc["call_id"]}
                # 工具计数发生后立即推送；若用户此时关闭窗口，落盘的 usage 仍与工具卡片一致。
                yield {"type": "usage", "usage": meter.snapshot(effective_max_rounds)}
                verification_status = current_verification_status(guard_state)
                verification_revision = guard_state.current_flow_revision
                if (
                    verification_revision is not None
                    and (
                        verification_status != last_verification_status
                        or verification_revision != last_verification_revision
                    )
                ):
                    last_verification_status = verification_status
                    last_verification_revision = verification_revision
                    yield {
                        "type": "verification",
                        "status": verification_status,
                        "revision": verification_revision,
                    }

                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": _elide_repeated_result(
                        tool_name, tc["arguments"], result, repeated_results
                    ),
                })

                if _image_b64 and not guard_state.model_no_vision:
                    _image_label = (
                        f"[{tool_name} 失败现场截图：{result.get('failure_screenshot_note', '')}]"
                        if tool_name == "get_run_error"
                        else f"[{tool_name} 页面截图：{result.get('url', '')}]"
                    )
                    full_messages.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _image_label},
                            {"type": "image_url", "image_url": {"url": f"data:{_image_media};base64,{_image_b64}"}},
                        ],
                    })

                guidance, stop_round = _after_tool_guidance(tool_name, result, guard_state)
                if guidance:
                    full_messages.append({"role": "system", "content": guidance})
                if guard_state.terminal_response_only:
                    terminal_response = _terminal_tool_response(tool_name, result)
                if stop_round:
                    _stop_after = _exec_idx
                    break

            # 被 guard 拦下或 break 跳过的预取任务不会有人来取结果，留着会变成孤儿任务
            for _orphan in prefetched.values():
                _orphan.cancel()
            prefetched.clear()

            if _stop_after is not None and _stop_after + 1 < len(tool_items):
                # assistant 消息里已记录本轮全部 tool_calls；break 跳过的调用若不补
                # tool 应答，严格的 OpenAI 兼容端点会在下一轮以 400 拒绝整个对话。
                for _skip_stream_idx, _skip_tc in tool_items[_stop_after + 1:]:
                    _skip_result = {
                        "status": "skipped",
                        "message": "该调用未执行：流程刚被创建/修改，请先按系统引导完成后续校验，再视需要重新发起。",
                    }
                    if _skip_stream_idx not in emitted_tool_starts:
                        yield {"type": "tool_start", "tool": _skip_tc["name"], "args": _skip_tc["arguments"], "call_id": _skip_tc["call_id"]}
                    yield {"type": "tool_result", "tool": _skip_tc["name"], "result": _skip_result, "call_id": _skip_tc["call_id"]}
                    full_messages.append({
                        "role": "tool",
                        "tool_call_id": _skip_tc["id"],
                        "content": json.dumps(_skip_result, ensure_ascii=False),
                    })

            if terminal_response is not None:
                _session_checkpoint.clear(flow_id)
                yield {"type": "text", "delta": terminal_response}
                yield {"type": "usage", "usage": meter.snapshot(effective_max_rounds)}
                yield {"type": "done"}
                return

        # 唯一一条不经模型的收尾路径，措辞要能独立成话：这轮的正文可能一个字都没有
        yield {
            "type": "text",
            "delta": (
                f"\n\n已连续调用 {effective_max_rounds} 轮工具仍未收敛，我在这里停下来，避免继续空转。\n"
                "上面的处理时间线是这轮实际做过的操作。请告诉我优先解决哪一个问题，"
                "或把任务拆成更小的一步再发给我，我接着这里继续。"
            ),
        }
        yield {"type": "usage", "usage": meter.snapshot(effective_max_rounds)}
        yield {"type": "done"}


_NON_EXECUTED_STATUSES = {"blocked_by_orchestrator_guard", "skipped", "error"}

def _tool_call_succeeded(result: Any) -> bool:
    """这次工具调用是否真的执行成功了——guard 拦截结果里没有 error 字段。"""
    if not isinstance(result, dict):
        return False
    if result.get("error"):
        return False
    return result.get("status") not in _NON_EXECUTED_STATUSES


# run_flow 停在这些状态是「轮到用户了」，不是流程没修好。
# stopped 一并算进来：run_flow 是阻塞轮询的，轮询期间任务变成 stopped 只可能是用户
# 自己按了停止——把它记成一次失败的修复，等于用户每中止一次就替模型花掉三分之一额度。
_RUN_WAITING_STATUSES = frozenset({"paused_for_human", "waiting_for_user_input", "stopped"})

# 执行器在起跑前就拒掉的返回：流程一行都没跑。收敛额度定价的是「真跑过一次」的代价，
# 这些一次都不该按运行计价——`blocked_by_failure_budget` 尤其是自我加固：熔断锁自己的
# 拒绝会花掉产生这把锁的额度，没有新失败、用户也看不到任何症状，只是越锁越死。
# 判据挂状态名而不是「有没有 error 字段」：起跑前拒绝一律带 message 不带 error，
# 而真实运行失败带 error，两者在这里必须分开。
_RUN_NOT_STARTED_STATUSES = frozenset({
    "blocked_by_failure_budget",
    "blocked_browser_profile_busy",
    "extension_not_connected",
    "extension_disabled",
    "empty_credential_variables",
    "missing_run_variables",
    "misplaced_call_parameters",
    "blocking_lint_findings",
    "undefined_variable_refs",
    "blocking_acceptance_contract",
})

# 上面那些里模型自己就能清掉的几条：调用参数写错了、流程还带着阻断问题。
# 这几条不算「本轮尝试过运行」——不然模型把 run_flow 调错一次、接着写个总结收尾，
# 「用户要验收却一次没跑」那条撤回重写就被自己的错误调用静默关掉，用户什么都拿不到。
# 剩下的（凭据为空、扩展未连、profile 被占、熔断锁）确实只能等用户，算尝试过。
_RUN_REFUSED_MODEL_FIXABLE = frozenset({
    "misplaced_call_parameters",
    "blocking_lint_findings",
    "undefined_variable_refs",
    "blocking_acceptance_contract",
})

# 会改变「最近一次运行」这件事实的工具，其返回要进下一轮的状态块
_RUN_STATE_TOOLS = frozenset({"run_flow", "stop_run"})


def _parse_tool_arguments(raw_args: str) -> tuple[dict[str, Any], list[str]]:
    """解析工具参数，并单独报出重复出现的键名。

    模型想一次改多个节点时会把 node_id/config_patch 在同一个对象里重复写几遍，
    json.loads 默认只保留最后一份，其余修改无声消失但工具照样返回成功。
    """
    duplicates: list[str] = []

    def _collect(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        for key, _ in pairs:
            if key in seen and key not in duplicates:
                duplicates.append(key)
            seen.add(key)
        return dict(pairs)

    parsed = json.loads(raw_args, object_pairs_hook=_collect)
    return (parsed if isinstance(parsed, dict) else {}), duplicates


_FLOW_WRITE_TOOLS = ("create_flow", "update_flow", "apply_node_fix", "set_acceptance_contract")

# 承诺「流程已经落盘」。这批词是从评测录像里逐字抄回来的：模型一次写入都没成功，
# 却宣称流程已创建，还顺带报了节点数、验收契约和输出路径——用户打开画布是空的。
_FLOW_SAVED_CLAIM_PHRASES = (
    "流程已创建", "已创建流程", "流程创建完成", "已为你创建", "已经创建好",
    "流程已保存", "已保存流程", "流程已建好", "流程已生成", "已生成流程",
    "流程已更新", "已更新流程",
)

# 承诺「数据质量没问题」——只有平台读过产物算出的 acceptance_audit 才配得上
_ACCEPTANCE_CLAIM_PHRASES = ("验收通过", "通过验收", "可以验收", "已验收", "验收结论：通过", "验收：通过")
# 承诺「改动确实生效了」——最低要有一次改动之后的成功运行
_VERIFIED_FIX_CLAIM_PHRASES = (
    "已修复", "问题已解决", "已解决", "修好了", "可以正常使用",
    "运行正常", "已恢复正常", "现在可以正常", "能正常跑",
)
# 承诺「数据被加工成了什么样」。这批词单看不足以判错——描述新增节点的用途本来就要用它们，
# 所以只在本轮真的动了加工节点、且没有一次通过的审计时才当成越界断言
_DATA_EFFECT_CLAIM_PHRASES = (
    "去除", "去掉", "剔除", "清洗", "清理掉", "过滤掉", "筛掉",
    "只保留", "去重", "删掉重复", "噪声",
)


def _fabricated_write_receipt(text: str) -> bool:
    """把流程定义连同 revision 一起贴进回复，等于自己签发一张写入回执。

    revision 只由平台在写入成功时下发，模型手上没有这个数：文本里出现它就是编的。
    单看 flow_id 或 nodes 不算——讲解一段节点配置本来就要贴它们。
    """
    return '"revision"' in text and ('"flow_id"' in text or '"nodes"' in text)


def _overstated_result_claim(text: str, state: GuardState) -> str | None:
    """回复承诺的确定性超出了本会话拿到的证据。

    证据分三级，从根本往上：写入有没有落盘（流程是否真的存在），改动后有没有成功运行过
    （改动生效），运行返回的 acceptance_audit 有没有通过（产物内容可信）。后两级都由写入
    工具作废——流程一改，之前那次运行和审计针对的就不是这份定义了。每会话只纠正一次，
    否则模型改口后的回复会再次命中同一批词。
    """
    if state.result_claim_corrected:
        return None

    evidence_status = current_verification_status(state)
    audit_verified = evidence_status == "accepted" or (
        state.current_flow_revision is None and state.audit_passed
    )
    run_verified = evidence_status in {"run_verified", "accepted"} or (
        state.current_flow_revision is None and state.run_succeeded
    )

    # 最根本的一级：写入本身有没有发生。用户看到「流程已创建」却打开一张空画布时，
    # 下面两级证据谈不谈都没有意义，所以排在最前。
    #
    # 判据挂「流程里有没有业务节点」而不只挂「本会话写没写过」：current_flow_revision
    # 每次请求从零开始，续跑一轮说「流程已更新」指的是上一轮那次写入，那不是假话。
    # 空画布不同——空流程既没被创建也没被更新过，这句话怎么读都是假的。
    if (
        not state.flow_has_nodes
        and state.current_flow_revision is None
        and (
            any(phrase in text for phrase in _FLOW_SAVED_CLAIM_PHRASES)
            or _fabricated_write_receipt(text)
        )
    ):
        state.result_claim_corrected = True
        return (
            "你上一条回复已被撤回，用户没有看到，请完整重写整段回复（不要只补一句更正）。\n"
            "撤回原因：你说流程已经创建/保存好了，但没有任何一次写入成功，当前流程里没有一个业务节点——"
            "用户打开画布会是空的。你在回复里写的节点、验收契约、输出路径都不存在。\n"
            "二选一，重新给出回复：\n"
            "① 现在真的调用 create_flow / update_flow 把流程写进去，拿到返回里的 revision 之后再汇报；\n"
            "② 写不进去就据实说——工具被反复阻断、或缺少目标 URL、字段这类必要信息，"
            "就说明流程尚未保存、卡在哪一步、需要用户提供什么，不要描述一份不存在的流程。\n"
            "流程定义本身不是交付物：把 JSON 或节点清单贴进回复不等于写入，画布只认写入工具的返回。"
        )

    if any(phrase in text for phrase in _ACCEPTANCE_CLAIM_PHRASES) and not audit_verified:
        state.result_claim_corrected = True
        return (
            "你上一条回复已被撤回，用户没有看到，请完整重写整段回复（不要只补一句更正）。\n"
            "撤回原因：你下了验收通过的结论，但当前这份流程定义没有一次 acceptance_audit.passed=true 的运行。"
            "状态块里的静态诊断只看流程定义，不读运行产物，不能作为验收依据。\n"
            "二选一，重新给出回复：\n"
            "① 现在调用 run_flow，返回里的 acceptance_audit 就是平台算出的验收结论，passed 之后再下结论；\n"
            "② 不运行，就把结论改成「静态检查通过」，并明确写出未做运行验证、实际输出内容未经确认。\n"
            "不要保留「验收通过」这个说法。"
        )

    if any(phrase in text for phrase in _VERIFIED_FIX_CLAIM_PHRASES) and not run_verified:
        state.result_claim_corrected = True
        return (
            "你上一条回复已被撤回，用户没有看到，请完整重写整段回复（不要只补一句更正）。\n"
            "撤回原因：你说问题已修复，但本次改动之后没有成功运行过。改动是否真的生效只有运行结果能证明，"
            "静态检查和你对代码的阅读都不能。\n"
            "二选一，重新给出回复：\n"
            "① 现在调用 run_flow 验证，再据实汇报；\n"
            "② 不运行，就把说法改成「已按…修改，尚未运行验证」，并说明需要用户跑一次确认。\n"
            "「已修复」「问题已解决」这类说法在拿到运行结果之前不要用。"
        )

    # 清洗类需求的失败是静默的：脚本跑通、变量非空、产物照落，而一个字符都没删。
    # 用户只能从这段回复判断做没做成，所以在没有产物证据时不许把"打算怎么处理"写成"已经处理了"
    if (
        state.transform_node_touched
        and not audit_verified
        and any(phrase in text for phrase in _DATA_EFFECT_CLAIM_PHRASES)
    ):
        state.result_claim_corrected = True
        return (
            "你上一条回复已被撤回，用户没有看到，请完整重写整段回复（不要只补一句更正）。\n"
            "撤回原因：你描述了加工节点对数据的实际效果（去除了什么、留下了什么），"
            "但当前这份定义没有一次 acceptance_audit.passed=true 的运行。加工节点是否真的改变了数据，"
            "只有把输入和输出的实际内容摆在一起才知道——脚本能跑通、变量有值、产物有文件，"
            "和一个字符都没删完全共存。\n"
            "二选一，重新给出回复：\n"
            "① 先 run_flow（返回里带 acceptance_audit），再 get_run_output 读输入与输出的实际内容，"
            "然后用真实的前后体量说话；\n"
            "② 不运行，就只写你改了什么节点、按什么规则处理，并明确说明效果未经运行验证。\n"
            "不要用「已去除」「已清理」这类完成态描述一次没跑过的加工。"
        )

    return None


# 用户要一个判断（能用/不能用），静态检查给不出这个判断，只能靠跑一次
_VERIFICATION_REQUEST_PHRASES = (
    "验收", "验证", "测试一下", "测一下", "跑一下", "跑一次", "运行一下", "运行一次",
    "确认结果", "确认一下", "能不能用", "是否可用", "对不对",
)
_NO_RUN_REQUEST_PHRASES = ("不要运行", "不用运行", "别运行", "不要跑", "不用跑", "别跑", "只看结构", "不要执行")
# 模型已经点明了具体拦路条件，就不是"懒得跑"，不该再催
_RUN_BLOCKER_PHRASES = (
    "扩展未连接", "人工接管", "human_takeover", "variable.input", "等待您", "等待用户",
    "请先填写", "未填写", "没有默认值", "凭据为空", "账号密码",
)


def _unmet_verification_request(text: str, state: GuardState) -> str | None:
    """用户要的是验收结论，本轮却一次都没运行。

    降级措辞只解决了「别说谎」，没解决「用户什么也没拿到」：静态检查判断不了
    抓取内容对不对，而这正是用户问的。会话内只催一次，避免模型坚持不跑时空转。
    """
    if state.verification_nudged or state.run_attempted:
        return None
    request = str(state.latest_user_message or "")
    if not any(phrase in request for phrase in _VERIFICATION_REQUEST_PHRASES):
        return None
    if any(phrase in request for phrase in _NO_RUN_REQUEST_PHRASES):
        return None
    if any(phrase in text for phrase in _RUN_BLOCKER_PHRASES):
        return None

    state.verification_nudged = True
    return (
        "你上一条回复已被撤回，用户没有看到，请完整重写整段回复（不要只补一句更正）。\n"
        "撤回原因：用户要的是「这个流程到底能不能用」这个判断，你本轮一次都没有运行流程，"
        "只有静态诊断结果。静态检查读不到运行产物，"
        "回答不了用户问的问题；把措辞降级成「未做运行验证」诚实，但用户依然什么都没拿到。\n"
        "正确做法：现在就调用 run_flow，按返回里的 acceptance_audit 据实汇报。\n"
        "只有确实跑不了才可以不跑，且必须写明是哪一条挡住的："
        "用户说了不要运行 / 凭据变量没有值 / 流程含 variable.input 或 control.human_takeover 无法无人值守 / "
        "指定了扩展执行器但扩展未连接。以上都不成立就去运行。"
    )


_SESSION_REQUIREMENT_MAX_CHARS = 2000


# output_boundary 里那句「无关话题就这么回」的拒答模板。它本身没问题，
# 问题是模型会拿它去回绝职责范围内的请求：实测同一条「给了 URL 要建抓取流程」的需求，
# 第一次收到这句回绝、原样重发一次就干了 13 次工具调用的活。
# 提示词侧已经把这段从页面探测阶段摘掉（ai_prompts.render_page_discovery_prompt），
# 但完整 SYSTEM_PROMPT 里还留着，所以这里再补一道硬判定。
_REFUSAL_TEMPLATE_MARKERS = ("我只能协助处理 RPA 流程", "只能协助处理 RPA 流程的创建")


def _misapplied_refusal(text: str, state: GuardState) -> str | None:
    """用拒答模板回绝了一个已被判定为职责范围内的请求。

    只在本轮识别出建流程/修流程/要运行意图时才判——用户真问天气时这句回绝是对的。
    """
    if state.refusal_corrected or not state.turn_intent_actionable:
        return None
    if not any(marker in text for marker in _REFUSAL_TEMPLATE_MARKERS):
        return None

    state.refusal_corrected = True
    return (
        "你上一条回复已被撤回，用户没有看到，请完整重写整段回复（不要只补一句更正）。\n"
        "撤回原因：你用「我只能协助处理 RPA 流程…」回绝了这次请求，但编排层已经判定"
        "本轮请求就是流程的创建/修改/运行/审查——它在你的职责范围之内。那句话只用于话题真的无关时，"
        "用在这里等于把该干的活推掉。\n"
        "重写要求：不要再出现那句回绝。直接按需求动手调用工具；"
        "确实缺关键信息（目标网址、要抓什么）就只问缺的那一项，并说明你已经掌握了什么。"
    )


# 「审查验收」「修复」「继续」这类只是指令，不含任何对数据的要求。
# 混进 requirement_text 会被当成需求关键词拿去和抓取结果比对，比不中就误报内容不匹配。
_META_COMMAND_RE = re.compile(
    r"^[\s，。、!！?？~]*"
    r"((流程|帮我|你|请|再|重新|继续)?\s*"
    r"(审查|验收|校验|检查|修复|优化|运行|执行|测试|跑|看|确认|继续|重试|创建|已完成|完成|试试)"
    r"\s*(一下|一次|下|看|吧|了)?\s*[，。、!！?？~]*)+$"
)


def _session_requirement_text(messages: list[dict[str, Any]]) -> str:
    """本会话用户提出的数据要求。

    首条消息通常是需求，后续是纠正，两者都可能含约束，所以不只取一条；
    但纯指令句要剔除、重复句要去重，否则「流程审查验收」发六遍就成了六个需求关键词。
    """
    parts: list[str] = []
    seen: set[str] = set()
    first_user_text = ""
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        text = content.strip()
        if not first_user_text:
            first_user_text = text
        if text in seen or _META_COMMAND_RE.match(text):
            continue
        seen.add(text)
        parts.append(text)
    # 全被判成指令时不能返回空——空会让 requirement_text 接管失效，模型又能自己填需求了
    return ("\n".join(parts) or first_user_text)[:_SESSION_REQUIREMENT_MAX_CHARS]


def _latest_user_message(messages: list[dict[str, Any]]) -> str:
    """本轮用户到底要什么。

    「要不要跑一次」只看最后一句：几轮前说过的"验收"不该绑架现在这句"再加一列"。
    """
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:_SESSION_REQUIREMENT_MAX_CHARS]
    return ""


def _blocking_diagnostics(
    flow_state: FlowState, state: GuardState
) -> list[dict[str, Any]] | None:
    """本轮还挡着 run_flow 的诊断项。每轮开头重算一次，不累积。

    阻断级静态诊断由状态块单点供给：写入工具的返回里也有一份，但那是写入那一刻的，
    两份一旦不一致就无从判断该信哪个。运行期逃逸只能另算——它的前提正是静态扫描漏了
    它，等状态块报出来是永远等不到的。
    """
    blocking = [f for f in flow_state.findings if is_blocking_finding(f)]
    blocking += [
        f for f in (state.runtime_escape_findings or []) if f not in blocking
    ]
    return blocking or None


def _orchestrator_guard_before_tool(
    tool_name: str,
    args: dict[str, Any],
    state: GuardState,
) -> dict[str, Any] | None:
    """硬性护栏：prompt 规则只是建议，这里强制少数不能靠模型记忆遵守的规则
    （违反会导致昂贵或误导性的运行）。

    两层，顺序有讲究：

    1. `ai_guards.GUARDS` 判「这次调用本身不该发生」——凭据外泄、验收契约被改写、
       调用方的只读授权边界。这类判定与失败历史无关，任何阶段都不该被绕过。
    2. `ai_phases` 判「现在还不到做这件事的时候」——缺证据、流程还没建、诊断没修完、
       额度已耗尽。

    护栏在前：只读模式是调用方给的授权边界，让阶段先报一个「先去看页面」，
    等于把一个同样会被拒的动作推荐给模型。

    护栏拦截要过一遍 `note_guard_block` 记账：护栏自己不看历史，所以「同一条拦截
    第几次了」只能在这里数。不数它，护栏就是唯一没有上限的空转形态——
    实测有会话在同一条契约校验上连撞 11 次。
    """
    blocked = apply_pre_tool_guards(tool_name, args, state)
    if blocked is not None:
        return note_guard_block(state, tool_name, blocked)
    return apply_phase_gate(tool_name, args, state)


def _build_change_context(state: GuardState) -> _ChangeContext:
    """本轮的写入约束，随每次工具调用传给执行器。

    执行器是全进程单例，这些是每轮才知道的东西，不能挂在它身上。
    protected_node_ids 只在「保留执行通道」意图下非空：用户没在报原流程的问题时，
    删改主链路节点属于正常编辑。
    """
    protected: set[str] = set()
    if state.repair_intent == "preserve_execution_channel":
        protected = {str(nid) for nid in (state.browser_chain_node_ids or set())}
    return _ChangeContext(
        protected_node_ids=frozenset(protected),
        fresh_page_evidence=bool(state.fresh_page_evidence),
    )


def _run_failure_signature(result: dict[str, Any]) -> str:
    """把一次运行失败压成可比对的签名。

    数字全部折成 `#`：同一个超时每次的毫秒数、task_id、行号都不一样，不折的话
    「同一个失败又来一次」永远判不出来，重复计价那条规则就等于没有。
    """
    import re as _re

    text = str(result.get("error") or result.get("message") or "")
    normalized = _re.sub(r"\d+", "#", text)[:120]
    return f"run:{result.get('status') or 'error'}:{normalized}"


def _count_repair_cycle(
    state: GuardState,
    last_error: Any,
    *,
    kind: str,
    signature: str,
    charge_only_if_repeated: bool = False,
) -> None:
    """记一次「改了又跑、跑了又没成」。

    运行报错和质量审计不合格都算：对用户来说两者是同一件事——又白跑了一轮。
    签名区分「同一个失败又来一次」和「换了个失败」，前者按两份计价（见 ai_phases）。
    """
    note_failed_attempt(
        state,
        kind=kind,
        signature=signature,
        detail=str(last_error or "")[:200] or None,
        charge_only_if_repeated=charge_only_if_repeated,
    )


# ── 工具返回 → 事实写入 ────────────────────────────────────────────────────────
#
# 一个工具一个处理函数。原先是一条 270 行的平铺 if 链，同一个工具的写入散在三四处不
# 相邻的分支里（run_flow 写在三段，中间隔着别的工具），链中间还夹着两个提前 return：
# 要回答「这个键会不会被写」，得把整条链从头读到尾。这不只是读起来累——那两个 return
# 今天之所以没吞掉任何东西，靠的是「只有 inspect_page 会返回 blocked_challenge_page、
# 只有 run_flow 会返回 blocked_by_failure_budget」这个执行器侧的事实，而链上没有任何
# 地方写着这个前提；executor 里哪天多一个产出点，被静默跳过的就是别的工具的写入。
# 按工具分派之后，两个 return 各自缩回自己工具的函数里，作用域一眼可见。
#
# 两张表都要能枚举：新增一个工具却没人决定「它写不写事实」，会在元测试里红，
# 而不是等到线上某条义务凭空消失——写入侧的缺陷历来都是这样，没有任何症状。


def _note_page_evidence(tool_name: str, result: dict[str, Any], state: GuardState) -> None:
    """inspect_page / inspect_screenshot 探测成功后的共有记账。"""
    # 取证成功才记指纹：失败的探测没拿到任何东西，重试是正当的。
    note_evidence(state, tool_name, state._last_tool_args or {})
    # 新页面证据到手，解锁节点级 selector 熔断。
    # 落到登录页的检查看到的是登录表单，对目标页不构成证据，不能解锁。
    if not result.get("redirected_to_login"):
        state.fresh_page_evidence = True


def _after_inspect_page(result: dict[str, Any], state: GuardState) -> None:
    if result.get("status") == "blocked_challenge_page":
        # 刻意不进 _PERSISTED_KEYS：这道锁只在本轮有效。用户下一句往往正是
        # 「我过完验证了，再跑一次」，跨轮留着会把唯一的出路也锁死。
        state.challenge_page_lock = {
            "url": result.get("requested_url"),
            "label": result.get("challenge_label"),
        }
        # 拦截页是整页替换，页面上一条业务结构都没有，下面的取证一条也不成立
        return
    if result.get("error"):
        return
    _note_page_evidence("inspect_page", result, state)
    state.page_evidence_source = result.get("inspection_source") or "browser_dom"
    # 看过页面就算证据到手，不再区分是否落到了登录页：区分了会造出一个死局——
    # 站点一直重定向到登录页时，模型既写不了流程也走不到收尾，只能耗完轮次。
    # 「登录页不算目标页证据」这条判断由上面的 fresh_page_evidence 承担，它管的是
    # 节点级 selector 熔断，拦错了还有出路。
    state.page_evidence_done = True


def _after_inspect_screenshot(result: dict[str, Any], state: GuardState) -> None:
    # 刻意不设 page_evidence_done：DOM 取证义务要的是 selector 能落地的结构，看图看不出来
    if not result.get("error"):
        _note_page_evidence("inspect_screenshot", result, state)


def _note_acceptance_audit(audit: dict[str, Any], state: GuardState) -> None:
    if audit.get("passed"):
        state.audit_passed = True
        # 业务校验通过 = 问题已解决，之前的失败尝试不该再挡住后续正常编辑
        state.node_selector_fix_counts = {}
        state.node_field_history = {}
        note_verified(state)
        _repair_ledger.clear(state.flow_id)
        return
    # 质量审计不合格是这类空转循环的主要形态：跑得起来但交付不了，
    # 只盯 run_flow 的失败状态会完全数不到
    state.audit_findings = {
        "issues": audit.get("issues", []),
        "repair_plan": audit.get("repair_plan", []),
    }
    first = next((i for i in (audit.get("issues") or []) if isinstance(i, dict)), {})
    _count_repair_cycle(
        state,
        first.get("message"),
        kind="audit",
        signature=f"audit:{first.get('issue') or 'unknown'}",
    )


def _note_undefined_variable_escape(result: dict[str, Any], state: GuardState) -> None:
    """运行期「变量未定义」说明静态检查漏检。

    它不在静态诊断集里（漏网就是它的定义），所以单独记一处：状态块每轮重算，
    写进 blocking_diagnostics 会被下一轮直接冲掉。
    """
    err_msg = str(result.get("error", ""))
    if "变量未定义" not in err_msg:
        return
    escaped_var = (re.search(r"变量未定义[：:]\s*(\S+)", err_msg) or [None, err_msg])[1]
    escape_finding: dict[str, Any] = {
        "severity": "error",
        "issue": "undefined_variable_ref_runtime_escape",
        "message": (
            f"运行期捕获到未定义变量 `{escaped_var}`，说明静态扫描存在漏网。"
            "在节点列表里搜这个变量名定位引用点，再用 apply_node_fix 修复后重试。"
        ),
        "fix": (
            "在 input_variables 中声明该变量，"
            "或删除节点中对该变量的引用，"
            "或确认引用拼写与 input_variables 中的 name 完全一致（区分大小写）。"
        ),
        "escaped_variable": escaped_var,
    }
    state.runtime_escape_findings = (state.runtime_escape_findings or []) + [escape_finding]


def _after_run_flow(result: dict[str, Any], state: GuardState) -> None:
    status = str(result.get("status") or "")

    # acceptance_audit 只在 task.status == "success" 时才挂上（见 executor._run_flow 结尾），
    # 所以「审计不合格」与下面「运行失败」这两处计价，对同一次运行永远不会同时发生。
    audit = result.get("acceptance_audit")
    if isinstance(audit, dict):
        _note_acceptance_audit(audit, state)

    # 超时/暂停也算尝试过：这些是真拦路条件，不该再催模型去跑。
    # 起跑前被拒且模型自己能改的那几条不算——见 _RUN_REFUSED_MODEL_FIXABLE。
    if status not in _RUN_REFUSED_MODEL_FIXABLE:
        state.run_attempted = True

    if status == "success":
        # 只记跑通，不清零修复计数：质量审计不合格的运行 status 同样是 success，
        # 在这里清零会让「跑成功 → 审计不过 → 再改」的循环永远攒不满次数
        state.run_succeeded = True
    elif status not in _RUN_WAITING_STATUSES:
        # 停下来等人不是一次失败的修复：流程没跑完是因为轮到用户了，
        # 记进熔断计数会让「等一次人工接管」白白吃掉三分之一的修复预算
        never_started = status in _RUN_NOT_STARTED_STATUSES
        _count_repair_cycle(
            state,
            result.get("error") or result.get("message"),
            kind="run_refused" if never_started else "run_error",
            signature=_run_failure_signature(result),
            charge_only_if_repeated=never_started,
        )

    if status == "error":
        _note_undefined_variable_escape(result, state)

    if status == "blocked_by_failure_budget":
        state.failure_budget_lock = {
            "flow_id": result.get("flow_id"),
            "recent_failed_task_ids": result.get("recent_failed_task_ids", []),
            "recent_failed_nodes": result.get("recent_failed_nodes", []),
            "recent_failure_kinds": result.get("recent_failure_kinds", []),
            "message": result.get("message"),
        }


def _after_get_run_error(result: dict[str, Any], state: GuardState) -> None:
    # 带回失败现场截图也算新证据。
    if result.get("failure_screenshot_note"):
        state.fresh_page_evidence = True
    if not result.get("inspect_hint"):
        return

    last_url = result.get("last_browser_url")
    suggested: dict[str, Any] = {"reason": "run_failed_on_page_element"}
    if isinstance(last_url, str) and last_url:
        suggested["url"] = last_url
    suggested["wait_selector"] = "table, [role=grid], nav, main"
    # 报出 selector/可见性错误就要求真去看一次 DOM：静态诊断读不到页面，
    # 不看就改等于按上一次的想象再猜一遍。
    state.page_evidence_required = suggested
    state.page_evidence_done = False

    failed_node = result.get("failed_node_config") if isinstance(result.get("failed_node_config"), dict) else {}
    failed_node_id = str(result.get("failed_node_id") or failed_node.get("id") or "")
    failed_type = str(failed_node.get("type") or "")
    selector_text = str(failed_node.get("selector") or "")
    selector_diagnostic = result.get("selector_diagnostic") if isinstance(result.get("selector_diagnostic"), dict) else {}
    diagnostic_kind = str(selector_diagnostic.get("kind") or "")

    # 失败也要沉淀到站点档案：台账只按 flow 记，换个流程抓同一站点就等于没学过。
    # 写不进去不影响本轮对话，档案只是先验，不是判据。
    if selector_text:
        try:
            from app.services.site_knowledge import get_site_knowledge_store
            get_site_knowledge_store().record_selector_failure(
                last_url if isinstance(last_url, str) else None,
                selector_text,
                node_type=failed_type,
                diagnostic_kind=diagnostic_kind,
            )
        except Exception as exc:
            logger.debug("site_knowledge 失败沉淀跳过：%s", exc)

    is_navigation_failure = failed_type in {"browser.click", "browser.hover"} and (
        # 四类诊断都算，不做区分：熔断给的出路是「换 browser.open 直达 URL」，
        # 这条出路对点不动的任何一种成因都成立，反复失败本身就够构成判据。
        diagnostic_kind in SELECTOR_DIAGNOSTIC_KINDS
        or any(token in selector_text for token in (
            ":has-text", "text=", "[role=", "aria-", ".menu", ".nav", "router-link", "a[href"
        ))
    )
    if failed_node_id and is_navigation_failure:
        # 这次失败的费在 run_flow 那一刻就扣过了，这里只补上归类：
        # 导航类熔断后给的出路（换 browser.open 直达 URL、或向用户要目标 URL）
        # 和别的失败完全不同，不归类就丢了这条出路；再扣一次费则是同一次失败算两遍。
        reclassify_last_attempt(state, kind="navigation")
        state.navigation_failure_hint = {
            "node_id": failed_node_id,
            "node_type": failed_type,
            "selector_diagnostic": selector_diagnostic,
            "last_browser_url": last_url,
            "suggested_fix": (
                "不要继续盲改同一 selector；优先将该节点替换为已验证可达的 browser.open 目标页面 URL。"
                "目标 URL 可以是 path、query、hash 或站点允许的完整 URL。"
                "若未知目标 URL，先 inspect_page 当前应用可见导航/按钮结构，再只修复该单个导航节点。"
            ),
        }


def _after_flow_write(result: dict[str, Any], state: GuardState) -> None:
    """四个写工具共有：作废旧证据 + 按写入后那一版 lint 重算闸门。"""
    # 流程一被改动，之前那次运行和审计就不再针对当前这份定义，证据全部作废。
    state.run_succeeded = False
    state.audit_passed = False
    # 页面和运行结果都可能因为这次改动而不同，重探同一个目标不再算原地打转
    note_progress(state)
    # 取 changed_nodes 而不是调用参数：update_nodes 的 patch 里没有 type，
    # 只按参数判会漏掉「改的是已有加工节点」这一半
    flow_event = next((
        event for event in (result.get("events") or [])
        if isinstance(event, dict) and event.get("type") == "flow_written"
    ), None)
    if flow_event and any(
        isinstance(item, dict) and item.get("type") in TRANSFORM_NODE_TYPES
        for item in (flow_event.get("affected_nodes") or [])
    ):
        state.transform_node_touched = True

    # 写入返回里的 lint 针对的是写入之后那一版，比状态块（本轮开头那一版）更新，所以据它更新闸门。
    # 不更新的话，「改完接着在同一轮跑」会被上一版的诊断拦住，而拦它的理由已经不存在了——
    # 交回给模型的是一份已经修好的问题清单。
    #
    # 必须区分「lint 跑了且干净」与「这个工具压根不跑 lint」：后者返回里两个键都没有，
    # 当成干净会把闸门整个抹掉。所以只认 lint_clean 与 lint_findings 这两个显式信号。
    post_write: list[dict[str, Any]] | None = None
    if isinstance(result.get("lint_findings"), list):
        post_write = [f for f in result["lint_findings"] if isinstance(f, dict)]
    elif result.get("lint_clean") is True:
        post_write = []
    if post_write is not None:
        # 复用 _blocking_diagnostics：与轮次开头那条路径共用同一份判定，
        # 两边算法一旦分岔，阶段就会随「上次是谁更新的」而变
        state.blocking_diagnostics = _blocking_diagnostics(
            FlowState(findings=post_write), state
        )


def _after_node_edit(result: dict[str, Any], state: GuardState) -> None:
    """update_flow / apply_node_fix 共有：修复台账 + 解除各类失败标记。

    台账记本流程每个受跟踪字段的取值轨迹与 selector 修改次数，跨会话累计。取执行器返回的
    tracked_field_changes（写入前后的真实差分），不解析调用参数——参数记的是模型「想改成
    什么」，归一化、字段清理、被拒的写入都会让两者不一致，而写入期差分检查正是拿这份历史
    去判回摆的：记错一次，之后每一次判定都错。
    """
    changes = [c for c in (result.get("tracked_field_changes") or []) if isinstance(c, dict)]
    field_history: dict[str, list[str]] = state.node_field_history
    for change in changes:
        trail = field_history.setdefault(f"{change.get('node_id')}.{change.get('field')}", [])
        value = str(change.get("value"))
        if not trail or trail[-1] != value:
            trail.append(value)

    selector_nodes = {
        str(c.get("node_id")) for c in changes if c.get("field") == "selector"
    }
    if selector_nodes:
        fix_counts = state.node_selector_fix_counts
        for node_id in selector_nodes:
            fix_counts[node_id] = fix_counts.get(node_id, 0) + 1
        # 一次页面证据只够支撑一次改动：改完还没重新看过页面，下一次又是盲改
        state.fresh_page_evidence = False

    if changes:
        _repair_ledger.save(
            state.flow_id,
            node_field_history=field_history,
            node_selector_fix_counts=state.node_selector_fix_counts or {},
            sessions=int(state.repair_sessions or 1),
        )

    # 只有真实结构修复才能解除各类失败标记，下一次运行会重新审计。
    state.audit_findings = None
    state.navigation_failure_hint = None
    state.runtime_escape_findings = []


def _after_create_flow(result: dict[str, Any], state: GuardState) -> None:
    if result.get("error"):
        return
    _after_flow_write(result, state)
    # 本轮内就得脱离 BUILD：不然刚建完的流程在同一轮里仍被判成「还不存在」，
    # 模型接下来那次 run_flow 会被自己刚满足的前置门挡掉。
    state.flow_has_nodes = True


def _after_update_flow(result: dict[str, Any], state: GuardState) -> None:
    if result.get("error"):
        return
    _after_flow_write(result, state)
    _after_node_edit(result, state)


def _after_apply_node_fix(result: dict[str, Any], state: GuardState) -> None:
    if result.get("error"):
        return
    _after_flow_write(result, state)
    _after_node_edit(result, state)
    # 熔断之后唯一还放行的写工具：它成功落盘说明模型确实定位到了单个节点、不是又一次盲改，
    # 所以这把锁只能由它解除。update_flow 不行——整流程重写正是这把锁要拦的东西。
    state.failure_budget_lock = None


def _after_set_acceptance_contract(result: dict[str, Any], state: GuardState) -> None:
    if result.get("error"):
        return
    _after_flow_write(result, state)


# 工具名 → 写入函数。写工具那四项必须与 _FLOW_WRITE_TOOLS 一致（元测试守）。
_AFTER_TOOL_HANDLERS: dict[str, Callable[[dict[str, Any], GuardState], None]] = {
    "apply_node_fix": _after_apply_node_fix,
    "create_flow": _after_create_flow,
    "get_run_error": _after_get_run_error,
    "inspect_page": _after_inspect_page,
    "inspect_screenshot": _after_inspect_screenshot,
    "run_flow": _after_run_flow,
    "set_acceptance_contract": _after_set_acceptance_contract,
    "update_flow": _after_update_flow,
}

# 返回不改变任何事实的工具。要显式列出来，理由跟 GUARDS / _PER_ROUND_KEYS 一样：
# 新增工具时必须表态，漏一个不会报错，只会让它的返回静默不进 state。
#
# get_run_logs / get_run_output 在此列是个既有的不对称：它们同属 EVIDENCE_TOOLS，却从来
# 没记过取证指纹，于是「重复取同一份日志」这条判据对它们不生效。本次不改动，写在这里
# 是为了让它是一个明确的决定，而不是一处谁都没注意到的遗漏。
_AFTER_TOOL_NO_STATE_EFFECT = frozenset({
    "check_extension_connection",
    "create_schedule",
    "get_run_logs",
    "get_run_output",
    "list_node_types",
    "list_schedules",
    "publish_flow",
    "stop_run",
    "toggle_schedule",
})


def _orchestrator_guard_after_tool(tool_name: str, result: Any, state: GuardState) -> None:
    """把一次工具返回写进 guard_state。

    与工具无关的三件事在前，且顺序敏感；其余全部按工具分派。
    """
    if not isinstance(result, dict):
        return
    # 「改动已落盘，要不要运行归用户定」是编排层自己下的结论，不是工具输出，所以排在
    # 下面那条「阻断结果不影响 state」之前。只停本轮剩余并行调用不够：下一轮工具全开，
    # 模型会接着去翻已经看过的东西。这里只收工具，收尾正文仍由模型自己写——换成
    # terminal_response_only 会走模板回复，把「改了哪个节点的哪个字段」那段顶掉。
    if result.get("required_action") == "ask_user":
        state.closing_statement_only = True
    # 被阻断的调用不携带真实工具输出，不应影响 state
    if result.get("status") == "blocked_by_orchestrator_guard":
        return

    if _is_terminal_result(result):
        state.terminal_response_only = True

    handler = _AFTER_TOOL_HANDLERS.get(tool_name)
    if handler is not None:
        handler(result, state)
