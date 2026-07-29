"""LiteLLM-based AI orchestrator with tool-call loop and SSE streaming."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field as dc_field
from typing import Any, NamedTuple

from app.services import ai_repair_ledger as _repair_ledger
from app.services import ai_session_checkpoint as _session_checkpoint
from app.services.ai_config_service import AiConfigService
from app.services.ai_guards import (
    MAX_REPAIR_CYCLES as _MAX_REPAIR_CYCLES,
    NAV_FAILURE_BUDGET as _NAV_FAILURE_BUDGET,
    PARALLEL_SAFE_TOOLS as _PARALLEL_SAFE_TOOLS,
    apply_pre_tool_guards,
    node_field_changes as _node_field_changes,
    selector_change_node_ids as _selector_change_node_ids,
)
from app.services.ai_prompts import get_system_prompt
from app.services.ai_tools import TOOL_SCHEMAS, RpaToolExecutor
from app.services.ai_tools.diagnostics import CONTENT_MISMATCH_ISSUES, SELECTOR_DIAGNOSTIC_KINDS
from app.services.ai_tools.lint import is_blocking_finding

logger = logging.getLogger(__name__)

# 防止上游卡死导致前端一直停在"正在思考…"
LLM_REQUEST_TIMEOUT = 90       # 连接/读取超时（秒）
FIRST_TOKEN_TIMEOUT = 60       # 等待首个 chunk 超时（秒）
STALL_TIMEOUT = 60             # 相邻 chunk 间隔超时（秒）


_VISION_ERROR_HINTS = (
    "does not support image",
    "not support vision",
    "vision is not supported",
    "image input is not supported",
    "multimodal",
    "image_url",
    "images are not",
    "does not support images",
    "doesn't support image",
    "unsupported content type",
    "Invalid content type",
    "image content",
)

_BALANCE_ERROR_HINTS = (
    "insufficient balance",
    "insufficient_balance",
    "insufficient quota",
    "insufficientquota",
    "credit balance is too low",
    "you exceeded your current quota",
    "exceeded your current quota",
    "account balance",
    "billing",
    "payment required",
    "402",
    "余额不足",
    "账户余额",
    "balance is insufficient",
    "no balance",
    "out of credits",
    "out of quota",
    "low balance",
)

_AUTH_ERROR_HINTS = (
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "api key is invalid",
    "api key not found",
    "unauthorized",
    "authentication failed",
    "invalid authentication",
    "invalid credentials",
)


def _is_vision_error(msg: str) -> bool:
    lower = msg.lower()
    return any(hint in lower for hint in _VISION_ERROR_HINTS)


def _is_balance_error(msg: str) -> bool:
    lower = msg.lower()
    return any(hint in lower for hint in _BALANCE_ERROR_HINTS)


def _is_auth_error(msg: str) -> bool:
    lower = msg.lower()
    return any(hint in lower for hint in _AUTH_ERROR_HINTS)


_LITELLM_PREFIXES = (
    "litellm.MidStreamFallbackError: ",
    "litellm.APIConnectionError: ",
    "litellm.InternalServerError: ",
    "litellm.APIError: APIError: ",
    "litellm.AuthenticationError: ",
    "litellm.BadRequestError: ",
    "litellm.RateLimitError: ",
    "litellm.ServiceUnavailableError: ",
    "litellm.Timeout: ",
    "litellm.ContextWindowExceededError: ",
)


def _clean_litellm_error(msg: str) -> str:
    """剥离 LiteLLM 异常前缀，提取可读错误信息。"""
    import re

    # 部分服务商把余额错误包装成 AuthenticationError，需在剥离前先按异常类型判断
    is_balance_by_type = "AuthenticationError" in msg and (
        "402" in msg or "balance" in msg.lower() or "quota" in msg.lower() or "credit" in msg.lower()
    )

    changed = True
    while changed:
        changed = False
        for prefix in _LITELLM_PREFIXES:
            if msg.startswith(prefix):
                msg = msg[len(prefix):]
                changed = True
                break

    original_idx = msg.find(" Original exception:")
    if original_idx != -1:
        msg = msg[:original_idx].strip()

    # 多层异常类前缀需循环剥离（如 APIConnectionError: OpenAIException - ...）
    changed = True
    while changed:
        new_msg = re.sub(r'^[A-Za-z]+(?:Exception|Error)\s*[-:]\s*', '', msg)
        changed = new_msg != msg
        msg = new_msg

    # 兼容双引号 JSON 和 Python dict repr（单引号）两种错误体格式
    m = re.search(r'["\']message["\']\s*:\s*["\']([^"\']+)["\']', msg)
    if m:
        msg = m.group(1)

    if _is_vision_error(msg):
        return "当前模型不支持图片输入，请切换到支持视觉的模型（如 Claude、GPT-4、Gemini）后重试。"

    if _is_balance_error(msg) or is_balance_by_type:
        return "模型账户余额不足，请前往服务商平台充值后重试。"

    if _is_auth_error(msg):
        return "API Key 无效或已过期，请在设置页重新配置正确的 API Key。"

    lower = msg.lower()
    if "concurrency limit" in lower or "too many requests" in lower or "rate limit" in lower:
        return "请求并发或频率超限，请稍后重试。"

    return msg[:300] if len(msg) > 300 else msg


# 正文按段存放在 ai_prompts，版本由 RPA_AI_PROMPT_VERSION 选。
# 这个常量是导入期取的值，只当默认版本的快照用；真正发出去的提示词在
# _build_system_message 里按版本重取，否则 evals 中途换版本对已导入的编排器不生效。
SYSTEM_PROMPT = get_system_prompt()

MAX_TOOL_ROUNDS = 30  # strong 模型的轮次上限；weak/standard 模型用下方 tier 分级覆盖更小的值

# 场景化 guidance，按事件注入，避免每轮携带全量指令

# 创建流程后：lint → validate → (凭据检查) → run
_GUIDANCE_AFTER_CREATE = (
    "流程已创建（无需确认）。请勿再调用 create_flow。下一步：\n"
    "1. 检查上方 lint_findings，用 apply_node_fix/update_flow 修复所有 severity=error 问题。\n"
    "2. 调用 validate_flow 确认 is_valid=true。\n"
    "3. 若 input_variables 含空凭据字段（账号/密码）→ 告知用户先填写，不要自动运行。\n"
    "4. 否则调用 run_flow。"
)

# 修复节点后：validate → run（lint 已在修复时内联）
_GUIDANCE_AFTER_FIX = (
    "变更已写入（无需确认）。下一步：调用 validate_flow 确认变量引用无误，再调用 run_flow。"
)

# 运行成功后：get_run_output → 抓取类流程须 assert_run_output
_GUIDANCE_AFTER_RUN_SUCCESS = (
    "运行成功。调用 get_run_output 查看产物；"
    "抓取/筛选/导出类流程还须调用 assert_run_output(task_id, requirement_text=用户原始需求)，"
    "审计通过后才能向用户汇报完成。"
)

# 运行失败后：get_run_error → 按错误类型修复
_GUIDANCE_AFTER_RUN_ERROR = (
    "运行失败。调用 get_run_error 获取 failed_node_id 和 error_logs，按类型处理：\n"
    "• inspect_hint 非空 → 编排层已锁定，必须先调 inspect_page(url=last_browser_url)，禁止直接改 selector。\n"
    "• Timeout / selector 定位失败 → 调 inspect_page 取真实 DOM，用 apply_node_fix 更新 selector。\n"
    "• ModuleNotFoundError → 用内置库重写 script 节点 code，删除第三方 import。\n"
    "• 变量未定义 → validate_flow 确认引用链，apply_node_fix 补填 outputVariable。\n"
    "• 其他 → 按错误信息修复后重新 run_flow。"
)

# 质量审计失败后：按 repair_plan 修复结构
_GUIDANCE_AFTER_AUDIT_FAIL = (
    "质量审计未通过（passed=false）。编排层已锁定下次 run_flow。\n"
    "必须按返回的 repair_plan 修复流程结构，再重新 run_flow → get_run_output → assert_run_output。"
)

# 这些拦截意味着「工具走不下去了，得让用户拿主意」，与那些只是改道的拦截不同
_TERMINAL_GUARD_ACTIONS = frozenset({
    "report_to_user_and_stop",
    "needs_user_navigation_target",
})

_GUIDANCE_AFTER_TERMINAL_BLOCK = (
    "本轮到此结束：编排层判定继续调用工具不会推进任务，需要用户参与。\n"
    "接下来只输出面向用户的自然语言收尾，不要再调用任何工具，内容包含：\n"
    "1. 已经做了什么、卡在哪一步；\n"
    "2. 你判断的根因；\n"
    "3. 需要用户提供什么信息或做什么决定才能继续。\n"
    "工具结果里的 user_message 是给用户看的原话，可直接引用或改写。"
)

# 用户提出修复意图时注入，引导模型走诊断优先路径
_GUIDANCE_BEFORE_REPAIR = (
    "用户要求修复流程。强制执行顺序：\n"
    "1. lint_flow → 确认 issue 类型\n"
    "2. inspect_page(scope_selector=相关区域) → 获取 interaction_recipe 和真实 selector\n"
    "   （若 lint 无 browser/selector 类问题可跳过步骤 2）\n"
    "3. 按 repair_plan + interaction_recipe 修复节点\n"
    "直接调用 apply_node_fix / update_flow 会被编排层阻断，直到以上诊断步骤完成。\n"
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

# 判定"当前流程已有浏览器采集主链路"的节点类型
_BROWSER_MAIN_CHAIN_TYPES = frozenset({
    "browser.open", "browser.extract", "ui.extract", "browser.fetch",
})

# 画布骨架节点：只有这两种节点的流程等同于空流程
_SCAFFOLD_NODE_TYPES = frozenset({"start", "end"})

# 用户消息中表明创建新流程意图的关键字（配合 URL 检测激活 pre_create_inspect_gate）
_CREATE_INTENT_KEYWORDS = frozenset({
    "创建", "新建", "生成流程", "生成一个", "帮我做", "做一个", "建一个", "写一个",
    "帮我创", "帮我生成", "自动化", "爬取", "抓取", "登录", "流程",
    "create", "make", "build", "generate", "scrape", "automate",
})

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


# strong: 顶级模型，完整 prompt + 30 轮
# standard: 主流模型，完整 prompt + 20 轮
# weak: 小/快模型，精简 prompt + 12 轮
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
        {"id": "n17c",    "type": "script.python",     "title": "校验日期筛选生效",   "kind": "script",  "status": "pending", "position": {"x": 100, "y": 1900}, "timeoutMs": 10000, "description": "日期没写进组件时页面会返回全量数据，这里硬失败，不允许 continueOnError", "code": "import json, os\n_vars = json.loads(os.environ.get('RPA_VARIABLES_JSON', '{}'))\nexpected = (_vars.get('date_start', ''), _vars.get('date_end', ''))\nactual = (_vars.get('selected_start_date', ''), _vars.get('selected_end_date', ''))\nif actual != expected:\n    raise SystemExit(f'日期筛选未生效：期望 {expected}，实际 {actual}')\nprint(json.dumps({'date_filter': f'{actual[0]}~{actual[1]}'}, ensure_ascii=False))\n"},
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
        {"id": "e_n7_n8fill", "source": "n7",      "target": "n8_fill"},
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
        {"name": "username", "type": "String", "value": "demo_user", "category": "credential"},
        {"name": "password", "type": "String", "value": "demo_pass", "category": "credential", "sensitive": True},
        {"name": "captcha",  "type": "String", "value": "8888",      "category": "credential"},
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
    _create_result   = json.dumps({"flow_id": _flow_id, "name": "项目列表抓取-筛选", "status": "draft", "lint_findings": []}, ensure_ascii=False)
    _validate_result = json.dumps({
        "flow_id": _flow_id, "flow_name": "项目列表抓取-筛选",
        "input_variables": ["username", "password", "captcha", "date_start", "date_end"],
        "defined_variables": ["login_status", "selected_start_date", "selected_end_date", "project_data", "project_page_count", "project_table_count"],
        "issues": [], "is_valid": True, "fix_hint": None,
    }, ensure_ascii=False)
    _run_result    = json.dumps({"task_id": _task_id, "status": "success", "flow_id": _flow_id, "progress": {"current_step": 26, "total_steps": 26, "percent": 100, "elapsed_ms": 41200}}, ensure_ascii=False)
    _output_result = json.dumps({
        "task_id": _task_id, "status": "success",
        "summary": "运行成功，共输出 6 个变量、1 个产物文件。",
        "variables": {"login_status": "logged_in", "selected_start_date": "2026-06-01", "selected_end_date": "2026-06-24", "project_page_count": 2, "project_table_count": 6, "project_data": _rows},
        "artifacts": [{"filename": "project-list-20260624-101500.json", "type": "json"}],
    }, ensure_ascii=False)
    _assert_result = json.dumps({
        "task_id": _task_id, "passed": True,
        "selected_variable": "project_data", "row_count": 6,
        "headers": ["项目名称", "创建时间", "项目进度", "负责人"],
        "resolved_constraints": {"date_field": "创建时间", "start_date": "2026-06-01", "end_date": "2026-06-24", "enum_field": "项目进度", "allowed_values": ["项目通过", "待尽调"]},
        "issues": [], "sample_rows": _rows[:3],
        "message": "行数、日期范围、枚举约束全部通过，抽取结果为结构化行。",
    }, ensure_ascii=False)

    return [
        {"role": "user", "content": "帮我抓取 https://erp.demo-rpa.test/ 项目列表。筛选创建时间 2026-06-01 至 2026-06-24，项目进度为「项目通过/待尽调」。需要判断登录态；账号 demo_user，密码 demo_pass，验证码 8888。"},
        # 每个要交互的页面都单独探一次：登录页给出表单 selector，数据页给出 row_selector 与日期控件配方
        {"role": "assistant", "content": None, "tool_calls": [{"id": "fs_t1", "type": "function", "function": {"name": "inspect_page", "arguments": json.dumps({"url": "https://erp.demo-rpa.test/", "wait_selector": "input[type='password'], nav"})}}]},
        {"role": "tool", "tool_call_id": "fs_t1", "content": _inspect_login},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "fs_t2", "type": "function", "function": {"name": "inspect_page", "arguments": json.dumps({"url": "https://erp.demo-rpa.test/#/project/list", "wait_selector": "table, .el-table"})}}]},
        {"role": "tool", "tool_call_id": "fs_t2", "content": _inspect_list},
        # 示例站点恰好是 Element UI，下面的 el- selector 全部来自这次 inspect_page 的 page_classes。
        # 不写明出处的话，模型会把它们当成通用模板抄到 Arco/Vant/自研站点上去。
        {"role": "assistant", "content": "`page_classes` 里是 el- 前缀，说明这个站点用的是 Element UI，所以下面的表格/下拉/翻页 selector 按 Element UI 的类名取。换一个站点这些类名一律不成立，必须重新 `inspect_page` 拿它自己的 `page_classes` 和 `tables[].row_selector`。", "tool_calls": [{"id": "fs_t3", "type": "function", "function": {"name": "create_flow", "arguments": json.dumps({"name": "项目列表抓取-筛选", "description": "登录后筛选日期范围+项目进度，翻页提取项目列表", "input_variables": _ivs, "nodes": _nodes, "edges": _edges}, ensure_ascii=False)}}]},
        {"role": "tool", "tool_call_id": "fs_t3", "content": _create_result},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "fs_t4", "type": "function", "function": {"name": "validate_flow", "arguments": json.dumps({"flow_id": _flow_id})}}]},
        {"role": "tool", "tool_call_id": "fs_t4", "content": _validate_result},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "fs_t5", "type": "function", "function": {"name": "run_flow", "arguments": json.dumps({"flow_id": _flow_id, "variables": {"username": "demo_user", "password": "demo_pass", "captcha": "8888"}})}}]},
        {"role": "tool", "tool_call_id": "fs_t5", "content": _run_result},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "fs_t6", "type": "function", "function": {"name": "get_run_output", "arguments": json.dumps({"task_id": _task_id})}}]},
        {"role": "tool", "tool_call_id": "fs_t6", "content": _output_result},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "fs_t7", "type": "function", "function": {"name": "assert_run_output", "arguments": json.dumps({"task_id": _task_id, "requirement_text": "筛选创建时间 2026-06-01 至今天，项目进度为项目通过/待尽调", "date_field": "创建时间", "start_date": "2026-06-01", "end_date": "2026-06-24", "enum_field": "项目进度", "allowed_values": ["项目通过", "待尽调"]}, ensure_ascii=False)}}]},
        {"role": "tool", "tool_call_id": "fs_t7", "content": _assert_result},
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
    return any(signal in user_text for signal in _FEW_SHOT_CREATE_SIGNALS) and any(signal in user_text for signal in _FEW_SHOT_WEB_SIGNALS)


# 弱模型额外注入的开篇提示，让模型在有限轮次内尽量聚焦
_WEAK_MODEL_PREAMBLE = (
    "【精简模式·轮次有限】严格按顺序操作，不要跳步或重复调用同一工具：\n"
    "① inspect_page 获取真实 selector\n"
    "② create_flow / update_flow 构建或修改流程\n"
    "③ lint_flow + validate_flow 修复所有 error\n"
    "④ run_flow → get_run_output → assert_run_output\n"
    "字段不确定时调用 list_node_types；selector 失效时调用 inspect_page。禁止盲猜。"
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


async def _resolve_relay_model(model: str, base_url: str, api_key: str) -> str:
    """在中转 base_url 下从其实际提供的模型中匹配最接近的一个，按 openai/ 前缀路由。"""
    import httpx

    cache_key = f"{base_url}|{api_key}"
    cached = _relay_models_cache.get(cache_key)
    if cached and time.monotonic() - cached[1] < _RELAY_CACHE_TTL:
        relay_models = cached[0]
    else:
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


# 多轮工具循环里 inspect_page / get_flow 等结果动辄上万字符，旧结果对后续决策
# 只剩摘要价值。每轮请求前压缩「除最近 N 条外」的大体积 tool 消息，避免长会话
# 撑爆上下文窗口或拖慢每轮请求。
_KEEP_FULL_TOOL_RESULTS = 2          # 最近 N 条 tool 消息保留完整内容
_TOOL_COMPACT_THRESHOLD = 3_000      # 超过该字符数的旧 tool 消息才压缩
_COMPACTED_MARK = '"_compacted": true'

_DEFAULT_CONTEXT_WINDOW = 200_000
_CHARS_PER_TOKEN = 1.5               # 中英混排保守估计：纯 ASCII 约 4，CJK 约 1
_CONTEXT_USABLE_RATIO = 0.7          # 余量留给本轮输出与静态前缀
_MAX_CONTEXT_CHARS = 400_000         # 大窗口模型的实用上限：百万窗口塞满纯属烧钱


class _ModelCaps(NamedTuple):
    tier: str
    context_window: int
    supports_vision: bool
    supports_cache_control: bool


def _model_caps(model_id: str) -> _ModelCaps:
    """模型能力差异的唯一查询入口。

    分级、上下文窗口、视觉、提示词缓存原先各扫一遍目录、各写一套兜底，加一个模型
    要记得改四处；漏掉任一处的表现都是静默降级——图片被丢、缓存不生效、按错误的
    窗口裁剪历史——而不是报错。
    """
    from app.services.ai_config_service import AI_MODEL_CATALOG
    entry = next((e for e in AI_MODEL_CATALOG if e.get("id") == model_id), None)
    if entry is None:
        return _ModelCaps(
            tier="standard",
            context_window=_DEFAULT_CONTEXT_WINDOW,
            # 未知模型（自定义/中转透传）视觉乐观放行，被拒时靠 mid-stream fallback 兜底
            supports_vision=True,
            supports_cache_control=model_id.startswith(("claude-", "anthropic/")),
        )
    return _ModelCaps(
        tier=str(entry.get("tier") or "standard"),
        context_window=int(entry.get("context_window") or 0) or _DEFAULT_CONTEXT_WINDOW,
        supports_vision=not bool(entry.get("no_vision")),
        supports_cache_control=entry.get("provider") == "anthropic",
    )


def _context_char_budget(model: str) -> int:
    """按模型上下文窗口推算字符预算。

    原先对所有模型写死 40 万字符。对 Claude 那种百万窗口是合理上限，但目录里还有
    131k 窗口的 qwen、200k 的 glm——静态前缀就占掉 6 万字符，
    40 万的阈值对它们等于毫无保护，超窗只会以 API 报错收场。
    """
    derived = _model_caps(model).context_window * _CONTEXT_USABLE_RATIO * _CHARS_PER_TOKEN
    return int(min(derived, _MAX_CONTEXT_CHARS))


def _summarize_tool_json(content: str) -> str:
    """压缩大体积工具结果 JSON：保留标量，列表/字典折叠为数量。"""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content[:800] + f"…（已截断，原始 {len(content)} 字符）"
    if not isinstance(data, dict):
        return content[:800] + f"…（已截断，原始 {len(content)} 字符）"

    summary: dict[str, Any] = {"_compacted": True}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value if not isinstance(value, str) or len(value) <= 300 else value[:300] + "…"
        elif isinstance(value, list):
            summary[key] = f"<list[{len(value)}] 已压缩>"
        elif isinstance(value, dict):
            summary[key] = f"<dict[{len(value)}键] 已压缩>"
    summary["_note"] = "此为历史工具结果摘要；如需完整数据请重新调用该工具。"
    return json.dumps(summary, ensure_ascii=False)


_INTERRUPTED_TOOL_RESULT = '{"status": "interrupted", "note": "该工具调用被用户中止，结果未知"}'


def _expand_history_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把前端历史里的 toolCalls 还原成 assistant.tool_calls + tool 消息对。

    前端只发 role/content 时，纯工具回合会退化成 content 为空的 assistant 消息：
    模型看不到自己上一轮跑过什么工具，且空 content 消息被部分厂商判为非法输入。
    还原成原生形态后，历史工具结果也一并落进 _compact_tool_messages 的压缩预算。
    """
    expanded: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            expanded.append(msg)
            continue

        content = msg.get("content")
        raw_calls = msg.get("toolCalls") or msg.get("tool_calls") or []
        calls: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        for idx, call in enumerate(raw_calls):
            if not isinstance(call, dict) or not call.get("tool"):
                continue
            # id 只需在本次请求内唯一；前端的 nanoid 可能因重放历史而重复
            call_id = f"hist_{len(expanded)}_{idx}"
            calls.append({
                "id": call_id,
                "type": "function",
                "function": {"name": str(call["tool"]), "arguments": str(call.get("args") or "{}")},
            })
            result = call.get("result")
            results.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": str(call["tool"]),
                "content": json.dumps(result, ensure_ascii=False) if result is not None else _INTERRUPTED_TOOL_RESULT,
            })

        if calls:
            expanded.append({"role": "assistant", "content": content or None, "tool_calls": calls})
            expanded.extend(results)
        elif content:
            expanded.append({"role": "assistant", "content": content})
    return expanded


_OLD_SCREENSHOT_PLACEHOLDER = "[历史截图已移除以控制上下文，如需查看请重新调用 inspect_screenshot]"


_DROPPED_HISTORY_MARK = "[上下文超限，已丢弃最早的"

_KEPT_CONSTRAINT_MARK = "【用户此前提出的硬性要求】"

# 用户的约束通常只说一次，且几乎总在会话最早那几轮——正好是超预算时最先被丢掉的部分。
# 整轮丢弃后模型会重新按自己的默认做法来，用户只能再说一遍，且往往察觉不到是上下文丢了。
_CONSTRAINT_MARKERS = (
    "必须", "一定要", "务必", "不要", "不能", "别再", "禁止", "只能", "只用", "记住",
    "注意", "始终", "每次", "千万", "不许", "不可以", "改成", "改为", "换成",
)
_MAX_KEPT_CONSTRAINTS = 8
_MAX_CONSTRAINT_CHARS = 120
_SENTENCE_SPLIT_RE = re.compile(r"[。！\n；;]+")


def _extract_user_constraints(text: str) -> list[str]:
    """从用户原话里摘出带约束语气的句子。

    只取**原句**不做改写：改写过的约束就是模型自己的话，没有任何东西能校验它，
    而这一层的全部价值恰恰在于它来自用户而非模型。
    """
    found: list[str] = []
    for raw in _SENTENCE_SPLIT_RE.split(text or ""):
        s = raw.strip()
        if not s or len(s) > _MAX_CONSTRAINT_CHARS:
            continue
        if any(marker in s for marker in _CONSTRAINT_MARKERS):
            found.append(s)
    return found


def _total_content_chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages)


def _drop_oldest_turns(messages: list[dict[str, Any]], protect_prefix: int, budget: int) -> int:
    """丢弃最老的完整对话轮次，返回丢弃条数。

    压缩是单调的——所有 tool 消息都压过一遍后就再也缩不动了，此时若仍超预算，
    唯一的出路是整轮丢弃，否则下一轮直接撞模型窗口报错。
    """
    prefix_chars = _total_content_chars(messages[:protect_prefix])
    if prefix_chars > budget:
        # 丢历史救不了：提示词本身就超预算。给出可行动的诊断，而不是把历史清空了事
        logger.warning(
            "静态前缀 %s 字符已超出预算 %s，该模型窗口对当前提示词过小", prefix_chars, budget
        )

    dropped = 0
    kept_constraints: list[str] = []
    while _total_content_chars(messages) > budget:
        # 只在 user 消息处切：保证不会留下没有 assistant.tool_calls 配对的 tool 消息，
        # 也保证最后一轮（没有后继 user）永远留着
        cut = next(
            (i for i in range(protect_prefix + 1, len(messages)) if messages[i].get("role") == "user"),
            None,
        )
        if cut is None:
            break
        # 丢之前先把要求捞出来：这一段里可能有上一次丢弃时留下的摘要，它同样会被删掉
        for doomed in messages[protect_prefix:cut]:
            content = str(doomed.get("content") or "")
            if doomed.get("role") == "user":
                kept_constraints.extend(_extract_user_constraints(content))
            elif content.startswith(_KEPT_CONSTRAINT_MARK):
                kept_constraints.extend(
                    line.lstrip("- ").strip() for line in content.splitlines()[1:] if line.strip()
                )
        del messages[protect_prefix:cut]
        dropped += cut - protect_prefix

    if kept_constraints:
        deduped = list(dict.fromkeys(kept_constraints))[-_MAX_KEPT_CONSTRAINTS:]
        messages.insert(protect_prefix, {
            "role": "system",
            "content": (
                f"{_KEPT_CONSTRAINT_MARK}以下是用户在已丢弃的早期对话里的原话，"
                "现在依然有效，不要因为看不到上文就退回默认做法：\n"
                + "\n".join(f"- {c}" for c in deduped)
            ),
        })

    if dropped:
        note = {
            "role": "system",
            "content": f"{_DROPPED_HISTORY_MARK} {dropped} 条历史消息；如需早期细节请重新调用对应工具]",
        }
        # 同一次会话可能反复触发，替换旧提示而不是层层叠加
        if protect_prefix < len(messages) and str(
            messages[protect_prefix].get("content") or ""
        ).startswith(_DROPPED_HISTORY_MARK):
            messages[protect_prefix] = note
        else:
            messages.insert(protect_prefix, note)
        logger.warning("上下文超预算 %s 字符，已丢弃最早 %s 条历史消息", budget, dropped)
    return dropped


def _compact_tool_messages(
    messages: list[dict[str, Any]], budget: int = _DEFAULT_CONTEXT_WINDOW, protect_prefix: int = 0
) -> None:
    """原地压缩较旧的超大 tool 消息，最近几条保留完整内容；压不动仍超预算则整轮丢弃。"""
    tool_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "tool" and isinstance(m.get("content"), str)
    ]
    if tool_indices:
        total_chars = _total_content_chars(messages)
        keep_full = 1 if total_chars > budget else _KEEP_FULL_TOOL_RESULTS
        for i in tool_indices[:-keep_full] if keep_full else tool_indices:
            content = messages[i]["content"]
            if len(content) > _TOOL_COMPACT_THRESHOLD and _COMPACTED_MARK not in content:
                messages[i]["content"] = _summarize_tool_json(content)

    # 截图 vision 消息单张就有几十万字符 base64，且永不因上文压缩而缩小；
    # 只保留最新一张，更早的替换为文本占位，防止多截图会话上下文只增不减。
    image_indices = [
        i for i, m in enumerate(messages)
        if isinstance(m.get("content"), list)
        and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
    ]
    for i in image_indices[:-1]:
        texts = [
            str(p.get("text") or "")
            for p in messages[i]["content"]
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        messages[i]["content"] = ("\n".join(t for t in texts if t) or "") + "\n" + _OLD_SCREENSHOT_PLACEHOLDER

    _drop_oldest_turns(messages, protect_prefix, budget)


_ELIDE_MIN_CHARS = 2_000  # 小结果重发比指回去更省，指针本身也要占字符


def _elide_repeated_result(
    tool_name: str, arguments: str, result: Any, seen: dict[tuple[str, str], str]
) -> str:
    """同参数同结果的工具调用只送一次全文，之后送一句指回原文的话。

    inspect_page 一次一万七千字符，同一页反复探是常态。这里是「已执行、逐字比对后确认相同」
    才折叠，不是跳过调用——页面被点击改变过就不会相等，也就不会折叠，不存在读到旧状态的风险。
    """
    payload = json.dumps(result, ensure_ascii=False)
    key = (tool_name, arguments)
    previous = seen.get(key)
    if previous is not None and previous == payload and len(payload) >= _ELIDE_MIN_CHARS:
        return json.dumps({
            "_unchanged": True,
            "message": f"本次 {tool_name} 的返回与上一次同参数调用逐字相同，内容见上文，未重复输出。",
        }, ensure_ascii=False)
    seen[key] = payload
    return payload


def _stable_prefix_end(messages: list[dict[str, Any]]) -> int:
    """返回第一条「后续轮次还可能被改写」的消息下标，其之前的内容逐字不变。

    压缩与截图占位都是一次性的（`_COMPACTED_MARK` / 内容已不是 image_url 就不再改），
    所以改写前沿只会前进不会回头；前沿之前是可缓存的稳定前缀。
    """
    tool_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "tool" and isinstance(m.get("content"), str)
    ]
    end = tool_indices[-_KEEP_FULL_TOOL_RESULTS] if len(tool_indices) > _KEEP_FULL_TOOL_RESULTS else 0
    image_indices = [
        i for i, m in enumerate(messages)
        if isinstance(m.get("content"), list)
        and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
    ]
    if image_indices:
        # 最新一张截图会在下一张到来时被替换成占位符，不能划进稳定区
        end = min(end, image_indices[-1])
    return max(end, 0)


def _mark_history_cache_anchor(messages: list[dict[str, Any]], model: str, relayed: bool) -> None:
    """在稳定前缀的末尾打第三个缓存断点，让历史对话也走缓存读。

    system 与 few-shot 的断点只覆盖静态前缀；真正随轮次膨胀的是工具结果，28 轮能到十万字符，
    没有断点就每轮原价重发。断点必须打在改写前沿之前，否则一次改写让整段缓存作废。
    锚点打在 tool 消息上：litellm 只对 role=tool 读取消息顶层的 cache_control。
    """
    if relayed or not _model_caps(model).supports_cache_control:
        return
    # Anthropic 断点上限 4 个，system/few-shot 已占 2 个，旧锚点必须先撤
    for message in messages:
        if message.get("role") == "tool":
            message.pop("cache_control", None)
    for index in range(_stable_prefix_end(messages) - 1, -1, -1):
        if messages[index].get("role") == "tool":
            messages[index]["cache_control"] = {"type": "ephemeral"}
            return


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


def _build_system_message(model: str, relayed: bool) -> dict[str, Any]:
    """构造系统消息，Anthropic 原生端点额外打一个提示词缓存断点。

    Anthropic 的缓存前缀按 tools → system → messages 累积，断点打在 system 上等于
    把 TOOL_SCHEMAS 一起缓进去，合计 4.4 万字符。OpenAI/DeepSeek 自动缓存，不需要
    标记；中转端点是否透传 cache_control 不可知，按普通字符串发。
    """
    prompt = get_system_prompt()
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
    "lint_flow": "正在处理静态检查结果…",
    "validate_flow": "正在处理变量校验结果…",
    "run_flow": "正在分析运行结果…",
    "get_run_status": "正在等待运行完成…",
    "get_run_error": "正在定位失败原因…",
    "get_run_output": "正在核对运行产物…",
    "get_run_logs": "正在阅读运行日志…",
    "assert_run_output": "正在核对业务质量…",
    "inspect_page": "正在解读页面结构…",
    "inspect_screenshot": "正在查看页面截图…",
    "publish_flow": "正在完成发布…",
    "list_flows": "正在整理流程列表…",
    "list_node_types": "正在查询可用节点…",
    "get_flow": "正在读取流程结构…",
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
    "assert_run_output": "正在读取运行产物做质量审计…",
    "get_run_output": "正在读取输出变量与产物…",
    "get_run_logs": "正在拉取运行日志…",
    "create_flow": "正在写入流程…",
    "update_flow": "正在写入变更…",
    "apply_node_fix": "正在修改节点…",
    "lint_flow": "正在做静态检查…",
    "validate_flow": "正在校验变量引用…",
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


@dataclass
class _FlowContext:
    """本轮开始时当前流程的形态，供意图判断与 guard 使用。"""

    context_message: dict[str, Any] | None = None
    # 已有浏览器主链路 → 需要"保留执行通道"guard
    browser_chain_node_ids: set[str] = dc_field(default_factory=set)
    # Studio 里"新建流程"一落地就带 flow_id 存库、画布只有 start→end，需与存量流程区分
    is_blank: bool = False
    # edge_id -> (source, target)，用于识别"改边绕过"：AI 可能保留受保护节点本身，
    # 却通过 remove_edge_ids/add_edges 切断其所有连接使其静默孤立
    edges_by_id: dict[str, tuple[str, str]] = dc_field(default_factory=dict)

    @property
    def has_browser_chain(self) -> bool:
        return bool(self.browser_chain_node_ids)


_STRIP_NODE_FIELDS = frozenset({"position", "status", "kind"})


async def _load_flow_context(executor: RpaToolExecutor, flow_id: str) -> _FlowContext:
    """读取当前流程并剥离 position/status/kind 等画布字段降低 token 开销。"""
    ctx = _FlowContext()
    try:
        flow = await executor.execute("get_flow", {"flow_id": flow_id})
        if isinstance(flow.get("definition"), dict):
            raw_nodes = flow["definition"].get("nodes", [])
            raw_edges = flow["definition"].get("edges", [])
            ctx.browser_chain_node_ids = {
                str(n["id"]) for n in raw_nodes
                if isinstance(n, dict) and n.get("type") in _BROWSER_MAIN_CHAIN_TYPES and "id" in n
            }
            ctx.is_blank = not any(
                isinstance(n, dict) and n.get("type") not in _SCAFFOLD_NODE_TYPES
                for n in raw_nodes
            )
            ctx.edges_by_id = {
                str(e["id"]): (str(e["source"]), str(e["target"])) for e in raw_edges
                if isinstance(e, dict) and "id" in e and "source" in e and "target" in e
            }
            flow["definition"]["nodes"] = [
                {k: v for k, v in n.items() if k not in _STRIP_NODE_FIELDS}
                if isinstance(n, dict) else n
                for n in raw_nodes
            ]
        ctx.context_message = {
            "role": "system",
            "content": f"当前打开的流程：\n```json\n{json.dumps(flow, ensure_ascii=False, indent=2)}\n```",
        }
    except Exception:
        # 注入失败不阻断对话，但必须留痕——否则"AI 看不到当前流程"完全无法排查。
        logger.warning("流程上下文注入失败（flow_id=%s），本轮对话将没有当前流程信息", flow_id, exc_info=True)
    return ctx


def _site_knowledge_message(
    messages: list[dict[str, Any]], flow_ctx: _FlowContext
) -> dict[str, Any] | None:
    """用户消息或当前流程含已知域名时，注入该站点沉淀的 selector/框架/登录特征与已证伪的写法。"""
    try:
        from app.services.site_knowledge import get_site_knowledge_store
        store = get_site_knowledge_store()
        text = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")
        if flow_ctx.context_message:
            text += "\n" + str(flow_ctx.context_message["content"])
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
    # 非 None 即检测到创建意图，值为需求里的首个 URL
    create_url: str | None = None


def _detect_turn_intents(
    messages: list[dict[str, Any]], flow_id: str | None, flow_ctx: _FlowContext
) -> _TurnIntents:
    intents = _TurnIntents()
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not last_user:
        return intents
    user_text = str(last_user.get("content") or "")
    user_text_lower = user_text.lower()

    if flow_id and any(kw in user_text_lower for kw in _REPAIR_INTENT_KEYWORDS):
        intents.repair = True
    # 结构性 guard，故意不靠关键字门控：用户描述问题的措辞（"抓不全"/"内容少了一半"等）
    # 是关键字列表永远无法穷举的集合。
    if flow_id and flow_ctx.has_browser_chain and not _is_explicit_channel_switch_request(user_text_lower):
        intents.preserve_execution_channel = True
    # 只看 `not flow_id` 会漏掉「Studio 新建流程后再对 AI 提需求」这个最常见入口。
    if (not flow_id or flow_ctx.is_blank) and not intents.repair:
        urls = _URL_IN_TEXT_RE.findall(user_text)
        if urls and any(kw in user_text_lower for kw in _CREATE_INTENT_KEYWORDS):
            intents.create_url = urls[0]
    return intents


def _after_tool_guidance(tool_name: str, result: Any) -> tuple[str | None, bool]:
    """返回 (要注入的系统引导, 是否跳过本轮剩余的并行调用)。"""
    if not isinstance(result, dict):
        return None, False
    if (
        result.get("status") == "blocked_by_orchestrator_guard"
        and result.get("required_action") in _TERMINAL_GUARD_ACTIONS
    ):
        # 拦截本身不产出正文。不明确要求收尾，模型常常直接空轮结束，
        # 用户只看到一个空气泡，既不知道被挡住了也不知道该给什么。
        return _GUIDANCE_AFTER_TERMINAL_BLOCK, True
    if tool_name == "create_flow" and _tool_call_succeeded(result) and result.get("flow_id"):
        return _GUIDANCE_AFTER_CREATE, True
    if tool_name == "update_flow" and _tool_call_succeeded(result):
        return _GUIDANCE_AFTER_FIX, True
    if tool_name == "apply_node_fix" and _tool_call_succeeded(result):
        return _GUIDANCE_AFTER_FIX, False
    if tool_name == "run_flow":
        status = result.get("status")
        if status == "success":
            return _GUIDANCE_AFTER_RUN_SUCCESS, False
        if status == "error":
            return _GUIDANCE_AFTER_RUN_ERROR, False
    if tool_name == "assert_run_output" and result.get("passed") is False:
        return _GUIDANCE_AFTER_AUDIT_FAIL, False
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

        flow_ctx = _FlowContext()
        if flow_id:
            yield {"type": "status", "delta": "正在读取流程…"}
            flow_ctx = await _load_flow_context(self._executor, flow_id)
            if flow_ctx.context_message:
                full_messages.append(flow_ctx.context_message)

        # 静态前缀边界：超预算丢弃历史时，system/few-shot/流程上下文不能被丢掉
        protect_prefix = len(full_messages)
        context_budget = _context_char_budget(model)

        full_messages.extend(_expand_history_tool_calls(messages))

        site_knowledge = _site_knowledge_message(messages, flow_ctx)
        if site_knowledge:
            full_messages.append(site_knowledge)

        intents = _detect_turn_intents(messages, flow_id, flow_ctx)

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

        guard_state: dict[str, Any] = {
            "flow_id": flow_id,
            "repair_sessions": int(ledger.get("sessions") or 0) + 1,
            "node_field_history": dict(ledger.get("node_field_history") or {}),
            "node_selector_fix_counts": dict(ledger.get("node_selector_fix_counts") or {}),
            "requires_inspect_page": None,
            "requires_quality_fix": None,
            "requires_lint_fix": None,
            "navigation_failure_counts": {},
            "navigation_budget_lock": None,
            "challenge_page_lock": None,
            "quality_issue_counts": {},
            "quality_budget_lock": None,
            "pending_repair_gate": None,   # {lint_done, inspect_done} — set on repair intent
            "repair_autorun_lock": None,
            "pre_create_inspect_gate": None,  # {inspect_done, suggested_url} — set on create intent
            "read_only_tools": read_only,     # 自愈诊断模式：阻断所有写入类工具
            "model_no_vision": not _model_caps(model).supports_vision,  # 阻断 inspect_screenshot
            # full_messages 里混着 few-shot 那轮虚构的 user 消息
            "user_requirement_text": _session_requirement_text(messages),
            "latest_user_message": _latest_user_message(messages),
        }

        # 上一轮被中断（用户点停止、断流、关窗）时留下的预算与未了结义务。
        # 放在初始化之后覆盖：默认值是"这轮从零开始"，检查点存在才说明不是。
        checkpoint = _session_checkpoint.load(flow_id)
        if checkpoint:
            guard_state.update(checkpoint)
            resume_note = _session_checkpoint.summarize(checkpoint)
            if resume_note:
                full_messages.insert(protect_prefix, {"role": "system", "content": resume_note})
                protect_prefix += 1

        if intents.repair:
            guard_state["pending_repair_gate"] = {"lint_done": False, "inspect_done": False}
            # 刻意不进 _PERSISTED_KEYS：只锁本轮。用户下一句往往就是「跑一下看看」，
            # 那时 repair 关键词不再出现，锁自然不会重新挂上。
            guard_state["repair_autorun_lock"] = True
            full_messages.append({"role": "system", "content": _GUIDANCE_BEFORE_REPAIR})
        if intents.preserve_execution_channel:
            guard_state["repair_intent"] = "preserve_execution_channel"
            guard_state["browser_chain_node_ids"] = flow_ctx.browser_chain_node_ids
            guard_state["browser_chain_edges_by_id"] = flow_ctx.edges_by_id
            full_messages.append({"role": "system", "content": _GUIDANCE_PRESERVE_EXECUTION_CHANNEL})

        if intents.create_url:
            # 空白流程已有 flow_id，该走 update_flow 落节点而不是再建一个
            build_tool = "update_flow" if flow_id else "create_flow"
            guard_state["pre_create_inspect_gate"] = {
                "inspect_done": False,
                "suggested_url": intents.create_url,
                "build_tool": build_tool,
            }
            full_messages.append({"role": "system", "content": _build_guidance_before_create(build_tool)})

        vision_fallback_done = False
        last_tool_name: str | None = None
        consecutive_empty_rounds = 0
        meter = _SessionMeter()
        repeated_results: dict[tuple[str, str], str] = {}

        for round_num in range(effective_max_rounds):
            if round_num == 0:
                yield {"type": "status", "delta": "正在思考…"}
            else:
                yield {"type": "status", "delta": _next_round_status_text(last_tool_name)}

            # 每轮请求前压缩旧的大体积工具结果，控制上下文规模。
            _compact_tool_messages(full_messages, context_budget, protect_prefix)
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
                response = await litellm.acompletion(
                    model=effective_model,
                    messages=full_messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto",
                    stream=True,
                    # 流式响应默认不带 usage，缺了它缓存命中就没有任何数据可查
                    stream_options={"include_usage": True},
                    # 不认识上述参数的厂商交给 litellm 丢弃，而不是整轮 400 失败
                    drop_params=True,
                    timeout=LLM_REQUEST_TIMEOUT,
                    **extra,
                )
            except _MissingApiKeyError as key_exc:
                yield {"type": "error", "message": str(key_exc)}
                yield {"type": "done"}
                return
            except Exception as exc:
                # 视觉降级：模型拒绝图片输入时剥离截图块并重试一次。
                if not vision_fallback_done and _is_vision_error(str(exc)) and _strip_image_messages(full_messages):
                    vision_fallback_done = True
                    yield {"type": "status", "delta": "当前模型不支持图片，已移除截图重试…"}
                    continue
                yield {"type": "error", "message": _clean_litellm_error(str(exc))}
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
                if not vision_fallback_done and _is_vision_error(str(stream_exc)) and _strip_image_messages(full_messages):
                    # 已 yield 的本轮部分文本无法撤回，重试后可能出现重复段落——
                    # 视觉错误几乎总在首 token 前抛出（请求校验阶段），实际影响可忽略。
                    vision_fallback_done = True
                    yield {"type": "status", "delta": "当前模型不支持图片，已移除截图重试…"}
                    continue
                yield {"type": "error", "message": _clean_litellm_error(str(stream_exc))}
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
                # 先纠越界结论，再补没做的验证：说法不实比交付不全严重
                claim_correction = _overstated_result_claim(final_text, guard_state) or _unmet_verification_request(final_text, guard_state)
                if claim_correction is not None:
                    full_messages.append({"role": "assistant", "content": final_text})
                    full_messages.append({"role": "system", "content": claim_correction})
                    # 越界结论已经流式吐给前端了，只追加更正会留下一段自相矛盾的回复，
                    # 让前端丢弃本条已渲染正文，由下一轮重写。
                    yield {"type": "retract", "reason": "结论超出已有证据，正在重写"}
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
                        guard_state["_last_tool_args"] = args
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
                                    self._executor.execute(tool_name, args, progress_sink)
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
                meter.tool_calls += 1
                if isinstance(result, dict) and str(result.get("status", "")).startswith("blocked_"):
                    meter.blocked_calls += 1
                _orchestrator_guard_after_tool(tool_name, result, guard_state)
                _session_checkpoint.save(flow_id, guard_state, rounds=meter.rounds)

                yield {"type": "tool_result", "tool": tool_name, "result": result, "call_id": tc["call_id"]}

                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": _elide_repeated_result(tool_name, tc["arguments"], result, repeated_results),
                })

                if _image_b64 and not guard_state.get("model_no_vision"):
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

                guidance, stop_round = _after_tool_guidance(tool_name, result)
                if guidance:
                    full_messages.append({"role": "system", "content": guidance})
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


# run_flow 停在这些状态是「轮到用户了」，不是流程没修好
_RUN_WAITING_STATUSES = frozenset({"paused_for_human", "waiting_for_user_input"})


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


_FLOW_WRITE_TOOLS = ("create_flow", "update_flow", "apply_node_fix")

# 承诺「数据质量没问题」——只有 assert_run_output 读过产物才配得上
_ACCEPTANCE_CLAIM_PHRASES = ("验收通过", "通过验收", "可以验收", "已验收", "验收结论：通过", "验收：通过")
# 承诺「改动确实生效了」——最低要有一次改动之后的成功运行
_VERIFIED_FIX_CLAIM_PHRASES = (
    "已修复", "问题已解决", "已解决", "修好了", "可以正常使用",
    "运行正常", "已恢复正常", "现在可以正常", "能正常跑",
)


def _overstated_result_claim(text: str, state: dict[str, Any]) -> str | None:
    """回复承诺的确定性超出了本会话拿到的证据。

    证据分两级：改动后成功运行过（改动生效），以及 assert_run_output 通过（产物内容可信）。
    两级都由写入工具作废——流程一改，之前那次运行和审计针对的就不是这份定义了。
    每会话只纠正一次，否则模型改口后的回复会再次命中同一批词。
    """
    if state.get("result_claim_corrected"):
        return None

    if any(phrase in text for phrase in _ACCEPTANCE_CLAIM_PHRASES) and not state.get("audit_passed"):
        state["result_claim_corrected"] = True
        return (
            "你上一条回复已被撤回，用户没有看到，请完整重写整段回复（不要只补一句更正）。\n"
            "撤回原因：你下了验收通过的结论，但当前这份流程定义没有一次通过的 assert_run_output。"
            "lint_flow 与 validate_flow 只读流程定义，不读运行产物，不能作为验收依据。\n"
            "二选一，重新给出回复：\n"
            "① 现在调用 run_flow，再用 assert_run_output 审计产物，拿到 passed 后再下结论；\n"
            "② 不运行，就把结论改成「静态检查通过」，并明确写出未做运行验证、实际输出内容未经确认。\n"
            "不要保留「验收通过」这个说法。"
        )

    if any(phrase in text for phrase in _VERIFIED_FIX_CLAIM_PHRASES) and not state.get("run_succeeded"):
        state["result_claim_corrected"] = True
        return (
            "你上一条回复已被撤回，用户没有看到，请完整重写整段回复（不要只补一句更正）。\n"
            "撤回原因：你说问题已修复，但本次改动之后没有成功运行过。改动是否真的生效只有运行结果能证明，"
            "静态检查和你对代码的阅读都不能。\n"
            "二选一，重新给出回复：\n"
            "① 现在调用 run_flow 验证，再据实汇报；\n"
            "② 不运行，就把说法改成「已按…修改，尚未运行验证」，并说明需要用户跑一次确认。\n"
            "「已修复」「问题已解决」这类说法在拿到运行结果之前不要用。"
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


def _unmet_verification_request(text: str, state: dict[str, Any]) -> str | None:
    """用户要的是验收结论，本轮却一次都没运行。

    降级措辞只解决了「别说谎」，没解决「用户什么也没拿到」：静态检查判断不了
    抓取内容对不对，而这正是用户问的。会话内只催一次，避免模型坚持不跑时空转。
    """
    if state.get("verification_nudged") or state.get("run_attempted"):
        return None
    request = str(state.get("latest_user_message") or "")
    if not any(phrase in request for phrase in _VERIFICATION_REQUEST_PHRASES):
        return None
    if any(phrase in request for phrase in _NO_RUN_REQUEST_PHRASES):
        return None
    if any(phrase in text for phrase in _RUN_BLOCKER_PHRASES):
        return None

    state["verification_nudged"] = True
    return (
        "你上一条回复已被撤回，用户没有看到，请完整重写整段回复（不要只补一句更正）。\n"
        "撤回原因：用户要的是「这个流程到底能不能用」这个判断，你本轮一次都没有运行流程，"
        "只给了 lint_flow / validate_flow 的静态结果。静态检查读不到运行产物，"
        "回答不了用户问的问题；把措辞降级成「未做运行验证」诚实，但用户依然什么都没拿到。\n"
        "正确做法：现在就调用 run_flow，成功后 get_run_output + assert_run_output，再据实汇报。\n"
        "只有确实跑不了才可以不跑，且必须写明是哪一条挡住的："
        "用户说了不要运行 / 凭据变量没有值 / 流程含 variable.input 或 control.human_takeover 无法无人值守 / "
        "指定了扩展执行器但扩展未连接。以上都不成立就去运行。"
    )


_SESSION_REQUIREMENT_MAX_CHARS = 2000


# 「审查验收」「修复」「继续」这类只是指令，不含任何对数据的要求。
# 混进 requirement_text 会被当成需求关键词拿去和抓取结果比对，比不中就误报内容不匹配。
_META_COMMAND_RE = re.compile(
    r"^[\s，。、!！?？~]*"
    r"((流程|帮我|你|请|再|重新|继续)?\s*"
    r"(审查|验收|校验|检查|修复|优化|运行|执行|测试|跑|看|确认|继续|试试)"
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


def _orchestrator_guard_before_tool(
    tool_name: str,
    args: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """硬性护栏：prompt 规则只是建议，这里强制少数不能靠模型记忆遵守的规则
    （违反会导致昂贵或误导性的运行）。

    判定逻辑与优先级都在 ai_guards.GUARDS 里，这里只是编排循环的调用点。
    """
    return apply_pre_tool_guards(tool_name, args, state)


def _count_repair_cycle(state: dict[str, Any], last_error: Any) -> None:
    """记一次「改了又跑、跑了又没成」，到上限就上锁。

    运行报错和质量审计不合格都算：对用户来说两者是同一件事——又白跑了一轮。
    """
    cycles = int(state.get("failed_run_cycles") or 0) + 1
    state["failed_run_cycles"] = cycles
    if cycles >= _MAX_REPAIR_CYCLES:
        state["repair_cycle_lock"] = {
            "cycles": cycles,
            "last_error": str(last_error or "")[:400],
        }


def _orchestrator_guard_after_tool(tool_name: str, result: Any, state: dict[str, Any]) -> None:
    if not isinstance(result, dict):
        return
    # 被阻断的调用不携带真实工具输出，不应影响 state
    if result.get("status") == "blocked_by_orchestrator_guard":
        return

    if tool_name in {"inspect_page", "inspect_screenshot"}:
        if result.get("error"):
            state["consecutive_inspect_page_count"] = 0
        else:
            # 截图与 DOM 探测共用连续计数，防止「反复看页面不干活」的循环。
            state["consecutive_inspect_page_count"] = int(state.get("consecutive_inspect_page_count") or 0) + 1
            # 新页面证据到手，解锁节点级 selector 熔断。
            # 落到登录页的检查看到的是登录表单，对目标页不构成证据，不能解锁。
            if not result.get("redirected_to_login"):
                state["fresh_page_evidence"] = True
            if tool_name == "inspect_page" and state.get("pre_create_inspect_gate") is not None:
                state["pre_create_inspect_gate"]["inspect_done"] = True
    elif tool_name in {"create_flow", "update_flow", "apply_node_fix", "run_flow"}:
        state["consecutive_inspect_page_count"] = 0

    if tool_name == "assert_run_output":
        if result.get("passed"):
            state["audit_passed"] = True
            # 业务校验通过 = 问题已解决，之前的失败尝试不该再挡住后续正常编辑
            state["node_selector_fix_counts"] = {}
            state["node_field_history"] = {}
            state["failed_run_cycles"] = 0
            _repair_ledger.clear(state.get("flow_id"))
        else:
            # 质量审计不合格是这类空转循环的主要形态：跑得起来但交付不了，
            # 只盯 run_flow 的失败状态会完全数不到
            first = next((i for i in (result.get("issues") or []) if isinstance(i, dict)), {})
            _count_repair_cycle(state, first.get("message"))
        issues = result.get("issues") or []
        if any(
            isinstance(item, dict) and item.get("issue") in CONTENT_MISMATCH_ISSUES
            for item in issues
        ):
            state["content_mismatch_reported"] = True
        if state.pop("requirement_text_overridden", False):
            result["requirement_text_source"] = (
                "已用本会话用户原话替换你传入的 requirement_text：需求以用户说的为准，不以你的复述为准。"
            )
        if state.pop("content_match_confirm_stripped", False):
            result["content_match_confirmed_ignored"] = (
                "content_match_confirmed 已被忽略并按 false 处理："
                "本会话尚未出现内容不匹配问题，该确认位只在工具报出该问题后才作数。"
            )

    # get_run_error 带回失败现场截图也算新证据。
    if tool_name == "get_run_error" and result.get("failure_screenshot_note"):
        state["fresh_page_evidence"] = True

    # 流程一被改动，之前那次运行和审计就不再针对当前这份定义，证据全部作废。
    if tool_name in _FLOW_WRITE_TOOLS and not result.get("error"):
        state["run_succeeded"] = False
        state["audit_passed"] = False
    elif tool_name == "run_flow":
        # 超时/暂停/扩展未连接也算尝试过：这些是真拦路条件，不该再催模型去跑
        state["run_attempted"] = True
        if result.get("status") == "success":
            # 只记跑通，不清零修复计数：质量审计不合格的运行 status 同样是 success，
            # 在这里清零会让「跑成功 → 审计不过 → 再改」的循环永远攒不满次数
            state["run_succeeded"] = True
        elif result.get("status") not in _RUN_WAITING_STATUSES:
            # 停下来等人不是一次失败的修复：流程没跑完是因为轮到用户了，
            # 记进熔断计数会让「等一次人工接管」白白吃掉三分之一的修复预算
            _count_repair_cycle(state, result.get("error") or result.get("message"))

    # 记录本会话内每个节点的 selector 修改次数；每次修改消耗一次页面证据。
    if tool_name in {"update_flow", "apply_node_fix"} and not result.get("error"):
        field_history: dict[str, list[str]] = state.setdefault("node_field_history", {})
        for node_id, field, value in _node_field_changes(tool_name, state.get("_last_tool_args") or {}):
            trail = field_history.setdefault(f"{node_id}.{field}", [])
            if not trail or trail[-1] != value:
                trail.append(value)

        changed = _selector_change_node_ids(tool_name, state.get("_last_tool_args") or {})
        if changed:
            fix_counts = state.setdefault("node_selector_fix_counts", {})
            for node_id in changed:
                fix_counts[node_id] = fix_counts.get(node_id, 0) + 1
            state["fresh_page_evidence"] = False

        _repair_ledger.save(
            state.get("flow_id"),
            node_field_history=field_history,
            node_selector_fix_counts=state.get("node_selector_fix_counts") or {},
            sessions=int(state.get("repair_sessions") or 1),
        )

    if tool_name == "create_flow" and not result.get("error"):
        state["pre_create_inspect_gate"] = None

    # 运行期"变量未定义"说明静态检查漏检，包装成阻断级 lint finding 逼迫先修复再重跑
    if tool_name == "run_flow" and result.get("status") == "error":
        err_msg = str(result.get("error", ""))
        if "变量未定义" in err_msg:
            import re as _re
            escaped_var = (_re.search(r"变量未定义[：:]\s*(\S+)", err_msg) or [None, err_msg])[1]
            escape_finding: dict[str, Any] = {
                "severity": "error",
                "issue": "undefined_variable_ref_runtime_escape",
                "message": (
                    f"运行期捕获到未定义变量 `{escaped_var}`，说明静态扫描存在漏网。"
                    "请用 validate_flow 定位引用该变量的节点，再用 apply_node_fix 修复后重试。"
                ),
                "fix": (
                    "在 input_variables 中声明该变量，"
                    "或删除节点中对该变量的引用，"
                    "或确认引用拼写与 input_variables 中的 name 完全一致（区分大小写）。"
                ),
                "escaped_variable": escaped_var,
            }
            existing = state.get("requires_lint_fix") or []
            state["requires_lint_fix"] = existing + [escape_finding]

    if result.get("status") == "blocked_challenge_page":
        # 刻意不进 _PERSISTED_KEYS：这道锁只在本轮有效。用户下一句往往正是
        # 「我过完验证了，再跑一次」，跨轮留着会把唯一的出路也锁死。
        state["challenge_page_lock"] = {
            "url": result.get("requested_url"),
            "label": result.get("challenge_label"),
        }
        return

    if result.get("status") == "blocked_by_failure_budget":
        state["failure_budget_lock"] = {
            "flow_id": result.get("flow_id"),
            "recent_failed_task_ids": result.get("recent_failed_task_ids", []),
            "recent_failed_nodes": result.get("recent_failed_nodes", []),
            "recent_failure_kinds": result.get("recent_failure_kinds", []),
            "message": result.get("message"),
        }
        return

    if tool_name == "inspect_page" and not result.get("error"):
        state["requires_inspect_page"] = None

    if tool_name == "get_run_error" and result.get("inspect_hint"):
        suggested: dict[str, Any] = {}
        last_url = result.get("last_browser_url")
        if isinstance(last_url, str) and last_url:
            suggested["url"] = last_url
        suggested["wait_selector"] = "table, [role=grid], nav, main"
        state["requires_inspect_page"] = suggested

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

        is_navigation_failure = (
            failed_type in {"browser.click", "browser.hover"}
            and bool(result.get("inspect_hint"))
            and (
                # 四类诊断都算，不做区分：熔断给的出路是「换 browser.open 直达 URL」，
                # 这条出路对点不动的任何一种成因都成立，反复失败本身就够构成判据。
                diagnostic_kind in SELECTOR_DIAGNOSTIC_KINDS
                or any(token in selector_text for token in (
                    ":has-text", "text=", "[role=", "aria-", ".menu", ".nav", "router-link", "a[href"
                ))
            )
        )
        if failed_node_id and is_navigation_failure:
            key = f"{failed_node_id}:{failed_type}:{diagnostic_kind or 'selector_error'}"
            counts: dict[str, int] = state.setdefault("navigation_failure_counts", {})
            counts[key] = counts.get(key, 0) + 1
            if counts[key] >= _NAV_FAILURE_BUDGET:
                state["navigation_budget_lock"] = {
                    "node_id": failed_node_id,
                    "node_type": failed_type,
                    "count": counts[key],
                    "selector_diagnostic": selector_diagnostic,
                    "last_browser_url": last_url,
                    "suggested_fix": (
                        "不要继续盲改同一 selector；优先将该节点替换为已验证可达的 browser.open 目标页面 URL。"
                        "目标 URL 可以是 path、query、hash 或站点允许的完整 URL。"
                        "若未知目标 URL，先 inspect_page 当前应用可见导航/按钮结构，再只修复该单个导航节点。"
                    ),
                }

    if tool_name == "assert_run_output":
        if result.get("passed") is False:
            state["requires_quality_fix"] = {
                "issues": result.get("issues", []),
                "repair_plan": result.get("repair_plan", []),
            }
            counts: dict[str, int] = state.setdefault("quality_issue_counts", {})
            for iss in result.get("issues", []):
                issue_type = str(iss.get("issue", "unknown"))
                counts[issue_type] = counts.get(issue_type, 0) + 1
                if counts[issue_type] >= 2 and not state.get("quality_budget_lock"):
                    state["quality_budget_lock"] = {
                        "issue": issue_type,
                        "count": counts[issue_type],
                    }
        elif result.get("passed") is True:
            state["requires_quality_fix"] = None
            state["quality_issue_counts"] = {}
            state["quality_budget_lock"] = None

    if tool_name in {"apply_node_fix", "update_flow", "create_flow", "lint_flow"} and not result.get("error"):
        # lint_flow 交回的键是 findings，写工具（create/update/apply）才叫 lint_findings。
        # 取错键读到的恒是空列表，于是下面这行把阻断标记清成 None：模型被拦下后随手
        # 调一次 lint_flow，什么都没修，锁就没了。失败的调用同样不能清——上面的 error
        # 判断挡的就是「lint 报错 = 流程干净」这个更荒谬的推论。
        raw = result.get("findings") if tool_name == "lint_flow" else result.get("lint_findings")
        blocking = _blocking_lint_findings(raw or [])
        # 运行期逃逸 finding（如 undefined_variable_ref_runtime_escape）存在的前提
        # 就是静态扫描漏网——一次通过的 lint_flow 不能把它冲掉，
        # 只有真实的结构性修复（update_flow / apply_node_fix 成功）才允许清除。
        if tool_name == "lint_flow":
            escaped = [
                f for f in (state.get("requires_lint_fix") or [])
                if isinstance(f, dict) and f.get("issue") == "undefined_variable_ref_runtime_escape"
            ]
            blocking = blocking + [f for f in escaped if f not in blocking]
        state["requires_lint_fix"] = blocking or None
        if tool_name in {"apply_node_fix", "update_flow"}:
            # 只有真实结构修复才能解除质量审计失败标记，下次运行会重新审计
            state["requires_quality_fix"] = None
            state["quality_issue_counts"] = {}
            state["quality_budget_lock"] = None
            state["navigation_failure_counts"] = {}
            state["navigation_budget_lock"] = None
        if tool_name == "apply_node_fix":
            state["failure_budget_lock"] = None

    if state.get("pending_repair_gate") is not None:
        gate = state["pending_repair_gate"]
        # get_run_error 报出 selector/可见性错误时打标，防止 lint 自动跳过 inspect_done
        if tool_name == "get_run_error" and result.get("inspect_hint"):
            gate["runtime_selector_error"] = True
        if tool_name == "lint_flow" and not result.get("error"):
            gate["lint_done"] = True
            # lint_flow 交回来的键是 findings；写工具（create/update/apply）才叫 lint_findings。
            # 取错键不会报错，只会让下面这段恒为「没发现问题」，inspect_page 于是无条件被跳过。
            findings = result.get("findings", [])
            has_browser_issue = any(
                f.get("issue") in _BROWSER_SELECTOR_ISSUES
                for f in findings if isinstance(f, dict)
            )
            # 仅当 lint 未发现浏览器/selector 问题且运行错误本身也非 selector 失败时才跳过
            if not has_browser_issue and not gate.get("runtime_selector_error"):
                gate["inspect_done"] = True
        if tool_name == "inspect_page" and not result.get("error"):
            gate["inspect_done"] = True

    if tool_name in {"apply_node_fix", "update_flow"} and not result.get("error"):
        state["pending_repair_gate"] = None


def _blocking_lint_findings(findings: Any) -> list[dict[str, Any]]:
    if not isinstance(findings, list):
        return []
    return [f for f in findings if is_blocking_finding(f)]
