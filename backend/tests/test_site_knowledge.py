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
    from app.services.ai_guard_state import GuardState
    from app.services.ai_orchestrator import _orchestrator_guard_after_tool

    monkeypatch.setattr(site_knowledge, "_default_store", store)

    _orchestrator_guard_after_tool("get_run_error", {
        "inspect_hint": True,
        "last_browser_url": "https://shop.test/orders",
        "failed_node_id": "n2",
        "failed_node_config": {"id": "n2", "type": "browser.click", "selector": ".btn-export"},
        "selector_diagnostic": {"kind": "selector_zero_match"},
    }, GuardState())

    assert store.get_profile("shop.test")["failed_selectors"][0]["selector"] == ".btn-export"


def test_missing_domain_is_a_no_op(store: SiteKnowledgeStore) -> None:
    store.record_selector_failure(None, ".btn", diagnostic_kind="selector_zero_match")
    store.record_selector_failure("not-a-url", ".btn", diagnostic_kind="selector_zero_match")
    assert store.get_profile("shop.test") is None


def _two_page_flow() -> dict[str, Any]:
    """一条流程先在订单页点导出、再导航到报表页点查询：两个 selector 属于两个页面。"""
    return {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "n1", "type": "browser.open", "targetUrl": "https://shop.test/orders"},
            {"id": "n2", "type": "browser.click", "selector": ".order-export"},
            {"id": "n3", "type": "browser.open", "targetUrl": "https://shop.test/reports"},
            {"id": "n4", "type": "browser.click", "selector": ".report-search"},
        ],
        "edges": [
            {"source": "start", "target": "n1"},
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
            {"source": "n3", "target": "n4"},
        ],
    }


def test_selector_verified_on_one_page_is_not_presented_as_current_page_knowledge(
    store: SiteKnowledgeStore,
) -> None:
    """同域不同页面最容易踩：两页的按钮 class 常常同名而结构不同，
    把订单页跑通的 selector 当成报表页「已验证」，运行时只报元素超时，看不出经验本来就用错了页面。"""
    store.record_flow_success(_two_page_flow(), flow_name="订单与报表")
    profile = store.get_profile("shop.test")
    assert profile is not None
    pages = profile["pages"]
    assert pages["shop.test/orders"]["selectors"]["browser.click"] == [".order-export"]
    assert pages["shop.test/reports"]["selectors"]["browser.click"] == [".report-search"]

    msg = SiteKnowledgeStore.build_context_message([profile], ["https://shop.test/orders"])
    current, other = msg.split("其它页面", 1)
    assert ".order-export" in current and ".order-export" not in other
    assert ".report-search" in other and ".report-search" not in current
    assert "未必适用当前页" in other


def test_hash_route_pages_are_kept_apart(store: SiteKnowledgeStore) -> None:
    """SPA 后台的路由在 hash 里，只按 path 分组会把整个后台当成同一个页面。"""
    store.record_flow_success(_flow(".a-btn", url="https://admin.test/#/order/list"))
    store.record_flow_success(_flow(".b-btn", url="https://admin.test/#/report/daily"))
    profile = store.get_profile("admin.test")
    assert profile is not None
    assert set(profile["pages"]) == {"admin.test/#/order/list", "admin.test/#/report/daily"}
    assert profile["pages"]["admin.test/#/order/list"]["selectors"]["browser.click"] == [".a-btn"]


def test_selector_after_a_branch_merge_falls_back_to_domain_scope(store: SiteKnowledgeStore) -> None:
    """两条分支停在不同页面、汇合后再点：这个 selector 属于哪个页面无从判断，
    只能退回域名级并如实标注，不能挑一条分支说它已在那个页面验证过。"""
    flow = {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "n_if", "type": "control.condition"},
            {"id": "n_a", "type": "browser.open", "targetUrl": "https://shop.test/orders"},
            {"id": "n_b", "type": "browser.open", "targetUrl": "https://shop.test/reports"},
            {"id": "n_merge", "type": "browser.click", "selector": ".shared-export"},
        ],
        "edges": [
            {"source": "start", "target": "n_if"},
            {"source": "n_if", "target": "n_a", "sourceHandle": "true"},
            {"source": "n_if", "target": "n_b", "sourceHandle": "false"},
            {"source": "n_a", "target": "n_merge"},
            {"source": "n_b", "target": "n_merge"},
        ],
    }
    store.record_flow_success(flow)
    profile = store.get_profile("shop.test")
    assert profile is not None
    assert all(
        ".shared-export" not in sels
        for page in profile["pages"].values()
        for sels in page["selectors"].values()
    )
    assert profile["selectors"]["browser.click"] == [".shared-export"]

    msg = SiteKnowledgeStore.build_context_message([profile], ["https://shop.test/orders"])
    assert "归属不到具体页面" in msg and ".shared-export" in msg


def test_context_message_without_urls_never_claims_a_current_page(store: SiteKnowledgeStore) -> None:
    """对话里没出现 URL 时不知道模型在看哪个页面，一条都不能标成「当前页面已验证」。"""
    store.record_flow_success(_two_page_flow())
    profile = store.get_profile("shop.test")
    assert profile is not None
    msg = SiteKnowledgeStore.build_context_message([profile])
    assert "**当前页面**" not in msg  # 只钉住声称句式，正文里的「确认它在当前页面存在」是提醒不是声称
    assert ".order-export" in msg and ".report-search" in msg
