"""站点档案的失败沉淀回归测试。

成功沉淀早就有了，缺的是相反的那一半：同一个 selector 在 A 流程里挂过，
换到 B 流程抓同一个站点时又被原样写一遍。这里钉住三件事——
失败要记得住、跑通要能撤销、不该记的诊断类型一条都不能进来。
"""

import json
import time
from pathlib import Path
from typing import Any

import pytest

from app.services.site_knowledge import SiteKnowledgeStore


@pytest.fixture
def store(tmp_path: Path) -> SiteKnowledgeStore:
    return SiteKnowledgeStore(path=str(tmp_path / "site_knowledge.json"))


def _flow(selector: str, url: str = "https://shop.test/orders") -> dict[str, Any]:
    return {"nodes": [
        {"id": "n1", "type": "browser.open", "targetUrl": url},
        {"id": "n2", "type": "browser.click", "selector": selector, "targetUrl": url},
    ]}


def test_failure_is_remembered_across_flows(store: SiteKnowledgeStore) -> None:
    store.record_selector_failure(
        "https://shop.test/orders", ".btn-export",
        node_type="browser.click", diagnostic_kind="selector_zero_match",
    )
    profile = store.get_profile("shop.test")
    assert profile is not None
    assert profile["failed_selectors"][0]["selector"] == ".btn-export"

    msg = SiteKnowledgeStore.build_context_message([profile])
    assert ".btn-export" in msg and "已证伪" in msg


def test_repeated_failures_accumulate_instead_of_piling_up(store: SiteKnowledgeStore) -> None:
    """重踩次数本身是最强的信号；堆成多条只会挤掉别的记录。"""
    for _ in range(3):
        store.record_selector_failure(
            "https://shop.test/", ".btn-export", diagnostic_kind="selector_zero_match",
        )
    failures = store.get_profile("shop.test")["failed_selectors"]
    assert len(failures) == 1
    assert failures[0]["count"] == 3
    assert "已踩 3 次" in SiteKnowledgeStore.build_context_message([store.get_profile("shop.test")])


@pytest.mark.parametrize("kind", ["selector_match_not_visible", "selector_match_hidden_or_not_visible"])
def test_visibility_failures_do_not_falsify_the_selector(store: SiteKnowledgeStore, kind: str) -> None:
    """元素存在但不可见时改 selector 是无效修法，记成禁令会把模型推向错误方向。"""
    store.record_selector_failure("https://shop.test/", ".btn-export", diagnostic_kind=kind)
    assert store.get_profile("shop.test") is None


def test_unknown_or_missing_diagnostic_is_not_recorded(store: SiteKnowledgeStore) -> None:
    """没有失败现场就不是证据——超时/网络错误跟 selector 写得对不对无关。"""
    store.record_selector_failure("https://shop.test/", ".btn-export", diagnostic_kind="")
    store.record_selector_failure("https://shop.test/", ".btn-export", diagnostic_kind="timeout")
    assert store.get_profile("shop.test") is None


def test_a_later_success_revokes_the_ban(store: SiteKnowledgeStore) -> None:
    """当时挂了可能是时序或登录态；跑通一次就说明 selector 本身没问题。"""
    store.record_selector_failure(
        "https://shop.test/orders", ".btn-export", diagnostic_kind="selector_zero_match",
    )
    store.record_flow_success(_flow(".btn-export"))
    profile = store.get_profile("shop.test")
    assert profile["failed_selectors"] == []
    assert ".btn-export" in profile["selectors"]["browser.click"]


def test_success_of_one_selector_keeps_other_bans(store: SiteKnowledgeStore) -> None:
    store.record_selector_failure("https://shop.test/", ".old-btn", diagnostic_kind="selector_zero_match")
    store.record_flow_success(_flow(".new-btn"))
    assert [f["selector"] for f in store.get_profile("shop.test")["failed_selectors"]] == [".old-btn"]


def test_stale_bans_expire(store: SiteKnowledgeStore, tmp_path: Path) -> None:
    """两周前的失败多半对应已经改过的页面，留着会挡住正确答案。"""
    store.record_selector_failure("https://shop.test/", ".btn", diagnostic_kind="selector_zero_match")
    path = tmp_path / "site_knowledge.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["shop.test"]["failed_selectors"][0]["at_ts"] = time.time() - 15 * 24 * 3600
    path.write_text(json.dumps(data), encoding="utf-8")

    profile = store.get_profile("shop.test")
    assert ".btn" not in SiteKnowledgeStore.build_context_message([profile])


def test_orchestrator_sediments_the_real_failure_scene(
    store: SiteKnowledgeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """光有 record_selector_failure 没人调用，等于这层根本不存在。"""
    from app.services import site_knowledge
    from app.services.ai_orchestrator import _orchestrator_guard_after_tool

    monkeypatch.setattr(site_knowledge, "_default_store", store)

    _orchestrator_guard_after_tool("get_run_error", {
        "inspect_hint": True,
        "last_browser_url": "https://shop.test/orders",
        "failed_node_id": "n2",
        "failed_node_config": {"id": "n2", "type": "browser.click", "selector": ".btn-export"},
        "selector_diagnostic": {"kind": "selector_zero_match"},
    }, {})

    assert store.get_profile("shop.test")["failed_selectors"][0]["selector"] == ".btn-export"


def test_missing_domain_is_a_no_op(store: SiteKnowledgeStore) -> None:
    store.record_selector_failure(None, ".btn", diagnostic_kind="selector_zero_match")
    store.record_selector_failure("not-a-url", ".btn", diagnostic_kind="selector_zero_match")
    assert store.get_profile("shop.test") is None
