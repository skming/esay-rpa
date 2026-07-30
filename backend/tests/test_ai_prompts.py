"""唯一生产提示词及公开工具契约的回归测试。"""

import inspect
import json
import re

from app.services.ai_guards import GUARDS, guard_contract_lines
from app.services.ai_prompts import (
    SYSTEM_PROMPT,
    render_guard_contract,
)
from app.services.ai_tools.schemas import TOOL_SCHEMAS
from app.services.ai_tools.executor import RpaToolExecutor


def test_system_prompt_renders_without_missing_sections() -> None:
    assert SYSTEM_PROMPT.startswith("你是 NF2Flow RPA 流程助手")
    assert len(SYSTEM_PROMPT) > 10_000


def test_reply_contract_distinguishes_clarification_and_verification_states() -> None:
    text = SYSTEM_PROMPT
    assert "只问会改变流程结构或验收标准的缺失信息" in text
    assert "请补充更多信息" in text
    assert "已修改，尚未运行验证" in text
    assert "运行通过，业务结果尚未验收" in text
    assert "验收通过" in text


def test_prompt_uses_secure_credential_and_default_output_policy() -> None:
    text = SYSTEM_PROMPT
    assert "绝不在对话中索取账号、密码、Token 等秘密值" in text
    assert "默认保存为 JSON，不为格式单独追问" in text
    assert "请提供登录账号和密码" not in text
    assert "问\"保存为 JSON 还是 Excel？\"" not in text


def test_tool_prompts_keep_the_contract_authoritative_and_bounded() -> None:
    functions = {item["function"]["name"]: item["function"] for item in TOOL_SCHEMAS}
    audit_properties = functions["assert_run_output"]["parameters"]["properties"]
    assert set(audit_properties) == {"task_id"}
    assert "冻结验收契约" in functions["assert_run_output"]["description"]
    assert "blocks_run" in functions["lint_flow"]["description"]
    assert "不得把账号、密码或 Token 写入参数" in functions["create_flow"]["description"]
    assert len(json.dumps(TOOL_SCHEMAS, ensure_ascii=False)) < 15_000


def test_tool_schema_names_match_the_executor_dispatch_table() -> None:
    schema_names = {item["function"]["name"] for item in TOOL_SCHEMAS}
    dispatch_source = inspect.getsource(RpaToolExecutor.execute)
    dispatch_names = set(re.findall(r'case "([a-z_]+)"', dispatch_source))
    assert schema_names == dispatch_names


def test_prompt_omits_sections_superseded_by_live_tool_results() -> None:
    assert "## 错误诊断" not in SYSTEM_PROMPT
    assert "## foreach 循环拓扑" not in SYSTEM_PROMPT


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
