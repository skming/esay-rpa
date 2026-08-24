"""唯一生产提示词及公开工具契约的回归测试。"""

import inspect
import json
import re

from app.services import ai_flow_state
from app.services.ai_guards import GUARDS, guard_contract_lines
from app.services.ai_prompts import (
    _PROMPT_ORDER,
    _SEC,
    SYSTEM_PROMPT,
    render_guard_contract,
)
from app.services.ai_tools import lint, lint_diff, lint_scenarios
from app.services.ai_tools.schemas import TOOL_SCHEMAS
from app.services.ai_tools.executor import RpaToolExecutor

# 只留给 ai_flow_state 每轮重建状态块用，不作为工具暴露给模型。
# audit_run 在这份名单里是设计本身：验收由平台在 run_flow 终态时自己算，
# 模型既不能发起也不能跳过——它一旦成为可调工具，「不调就等于验收通过」就又回来了。
PLATFORM_ONLY_TOOLS = frozenset({"get_flow", "lint_flow", "validate_flow", "get_run_status", "audit_run"})


def test_system_prompt_renders_without_missing_sections() -> None:
    assert SYSTEM_PROMPT.startswith("你是 NF2Flow RPA 流程助手")
    assert len(SYSTEM_PROMPT) > 10_000


def test_reply_contract_distinguishes_clarification_and_verification_states() -> None:
    text = SYSTEM_PROMPT
    assert "只问会改变流程结构或验收标准的缺失信息" in text
    assert "请补充更多信息" in text
    assert "已修改，尚未运行验证" in text
    assert "运行通过，但产物未通过验收" in text
    assert "验收通过" in text


def test_prompt_uses_secure_credential_and_default_output_policy() -> None:
    text = SYSTEM_PROMPT
    assert "绝不在对话中索取账号、密码、Token 等秘密值" in text
    assert "默认保存为 JSON，不为格式单独追问" in text
    assert "请提供登录账号和密码" not in text
    assert "问\"保存为 JSON 还是 Excel？\"" not in text


def test_tool_prompts_keep_the_contract_authoritative_and_bounded() -> None:
    functions = {item["function"]["name"]: item["function"] for item in TOOL_SCHEMAS}
    # 模型手上不存在任何「发起审计」的入口：验收结论只能随 run_flow 回来。
    # 一旦重新出现审计工具，它的判据参数就又归模型填，而那正是空转循环的来源。
    assert "assert_run_output" not in functions
    assert "audit_run" not in functions
    assert "acceptance_audit" in functions["run_flow"]["description"]
    assert set(functions["run_flow"]["parameters"]["properties"]) == {
        "flow_id", "variables", "browser_executor",
    }
    assert "不得把账号、密码或 Token 写入参数" in functions["create_flow"]["description"]
    catalog_params = functions["list_node_types"]["parameters"]
    assert catalog_params["required"] == ["types"]
    assert catalog_params["properties"]["types"]["maxItems"] == 8
    assert len(json.dumps(TOOL_SCHEMAS, ensure_ascii=False)) < 15_000


def test_tool_schema_names_match_the_executor_dispatch_table() -> None:
    """schema 是模型的全部「手」，dispatch 是平台的全部能力。

    两者不再相等：读当前状态的那几个入口只留给 ai_flow_state 调，不进 schema——
    它们回答的问题在每轮开头就已经答完了。但差集必须恰好是这一份白名单，
    多出任何一个都意味着有工具被误删或漏接。
    """
    schema_names = {item["function"]["name"] for item in TOOL_SCHEMAS}
    dispatch_source = inspect.getsource(RpaToolExecutor.execute)
    dispatch_names = set(re.findall(r'case "([a-z_]+)"', dispatch_source))

    assert schema_names <= dispatch_names, schema_names - dispatch_names
    assert dispatch_names - schema_names == PLATFORM_ONLY_TOOLS


def test_state_block_reads_go_through_the_executor_it_is_tested_against() -> None:
    """状态块绕过 executor 直接读库的话，脱敏与体积裁剪都得重写一遍。"""
    source = inspect.getsource(ai_flow_state)
    for tool in PLATFORM_ONLY_TOOLS:
        assert f'executor.execute("{tool}"' in source, tool


def test_prompt_omits_sections_superseded_by_live_tool_results() -> None:
    assert "## 错误诊断" not in SYSTEM_PROMPT
    assert "## foreach 循环拓扑" not in SYSTEM_PROMPT


def test_every_written_section_is_actually_rendered() -> None:
    """写了却没接进 _PROMPT_ORDER 的段落是最贵的一种死代码。

    它读起来像生效的规则，于是后续维护会继续改它、继续往里加约束，
    而模型一个字都看不到——两次「按提示词说的做了却没变化」就是这么来的。
    """
    rendered = set(_PROMPT_ORDER) | {"preamble", "reasoning_constraints"}
    assert set(_SEC) <= rendered, set(_SEC) - rendered


def test_prose_never_names_a_lint_issue() -> None:
    """提示词正文里出现 lint issue 名，就是同一条判据的第二份副本。

    诊断结论每轮由平台重算后放进状态块，带节点 id 和 `改法：`——比正文里
    「会报 xxx」精确得多，也不会随 lint 改名而过期。正文只讲怎么构建，
    不预告平台会报什么。名字要进提示词只能走 *_contract_lines() 那条自动生成的路。
    """
    names: set[str] = set()
    for module in (lint, lint_diff, lint_scenarios):
        names |= set(re.findall(r'"issue":\s*"([a-z_]+)"', inspect.getsource(module)))
    assert names, "取不到 lint issue 名，这条元测试会静默通过"

    leaked = {
        f"{section}:{name}"
        for section, text in _SEC.items()
        for name in names
        if name in text
    }
    assert not leaked, leaked


def test_guard_contract_block_covers_every_prompt_facing_guard() -> None:
    block = render_guard_contract()
    for guard in GUARDS:
        if guard.contract:
            assert guard.contract in block, guard.id


def test_prompt_states_hard_constraints_only_through_the_guard_table() -> None:
    """护栏规则在提示词里手抄一份，就会有一天跟护栏本身对不上。"""
    for line in guard_contract_lines():
        assert line in SYSTEM_PROMPT

    retired = (
        "未经 inspect_page 就调用 `create_flow` 会被编排层阻断",
        "编排层会拦截 `client_side_filter_masks_page_filter`",
    )
    for phrase in retired:
        assert phrase not in SYSTEM_PROMPT, phrase
