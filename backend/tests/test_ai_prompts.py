"""提示词版本编排的回归测试。

提示词没有编译期，写坏了要等一次真实会话才暴露；而 v2 是以「片段替换」表达的，
一旦上游改了原文，替换会静默落空，A/B 就变成了拿 v1 跟 v1 比。这里把这些静默失效变成红灯。
"""

import pytest

from app.services.ai_guards import GUARDS, guard_contract_lines
from app.services.ai_prompts import (
    PROMPT_VERSIONS,
    Rewrite,
    get_system_prompt,
    render_guard_contract,
)


def test_every_version_renders_without_missing_sections() -> None:
    for name in PROMPT_VERSIONS:
        text = get_system_prompt(name)
        assert text.startswith("你是 NF2Flow RPA 流程助手")
        assert len(text) > 10_000


def test_unknown_version_fails_loudly() -> None:
    """静默回退到默认版本会让 A/B 报告出一个根本没跑过的版本名。"""
    with pytest.raises(ValueError):
        get_system_prompt("v99")


def test_guard_contract_block_covers_every_prompt_facing_guard() -> None:
    block = render_guard_contract()
    for guard in GUARDS:
        if guard.contract:
            assert guard.contract in block, guard.id


def test_v2_states_hard_constraints_only_through_the_guard_table() -> None:
    """护栏规则在提示词里手抄一份，就会有一天跟护栏本身对不上。"""
    v2 = get_system_prompt("v2")
    for line in guard_contract_lines():
        assert line in v2

    # 这些是被 guard contract 取代掉的原文，v2 里不该再出现第二份表述
    retired = (
        "未经 inspect_page 就调用 `create_flow` 会被编排层阻断",
        "编排层会拦截 `client_side_filter_masks_page_filter`",
    )
    v1 = get_system_prompt("v1")
    for phrase in retired:
        assert phrase in v1, f"基线里没有这句话，说明断言写的是过期原文：{phrase}"
        assert phrase not in v2, phrase


def test_v1_keeps_no_guard_block_so_the_ab_difference_is_only_the_rewrite() -> None:
    assert "## 系统硬约束" not in get_system_prompt("v1")


def test_rewrite_refuses_to_silently_no_op() -> None:
    with pytest.raises(ValueError):
        Rewrite((("原文里并不存在的片段", "新片段"),)).apply("一些正文")


def test_active_version_follows_the_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPA_AI_PROMPT_VERSION", "v1")
    assert get_system_prompt() == get_system_prompt("v1")
    monkeypatch.setenv("RPA_AI_PROMPT_VERSION", "v2")
    assert get_system_prompt() == get_system_prompt("v2")


def test_orchestrator_reads_the_prompt_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """模块常量是导入期取的值；换版本必须对已经导入的编排器立即生效。"""
    from app.services.ai_orchestrator import _build_system_message

    monkeypatch.setenv("RPA_AI_PROMPT_VERSION", "v1")
    assert _build_system_message("gpt-4o", relayed=True)["content"] == get_system_prompt("v1")
    monkeypatch.setenv("RPA_AI_PROMPT_VERSION", "v2")
    assert _build_system_message("gpt-4o", relayed=True)["content"] == get_system_prompt("v2")
