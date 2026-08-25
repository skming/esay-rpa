"""写入期差分判定的每条触发路径。

这三条判据（保执行通道、字段回摆、selector 预算）原来是 pre-tool 护栏，判据是
调用参数的形状。现在判的是 before/after 两份定义，所以用例也按定义写：构造改动
前后的流程，断言这次写入该不该被拒。

参数形状测不出来的两个洞正是搬迁的理由，这里各有一条用例守着：
- `update_nodes` 的 patch 里不带 type 时，节点其实已经是脚本类型（旧实现放行）；
- 只删边不删节点、且新连线接不回执行路径（旧实现要靠一套边推演才拦得住）。
"""
from __future__ import annotations

from app.services.ai_tools.lint_diff import (
    NODE_SELECTOR_FIX_BUDGET,
    ChangeContext,
    change_lint_contract_lines,
    inspect_change,
)


def _flow(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {"nodes": nodes, "edges": edges or []}


def _chain() -> dict:
    """start → open → wait → extract → end 的浏览器主链路。"""
    return _flow(
        [
            {"id": "start", "type": "start"},
            {"id": "n_open", "type": "browser.open", "targetUrl": "https://example.com"},
            {"id": "n_wait", "type": "browser.wait", "selector": "table"},
            {"id": "n_extract", "type": "browser.extract", "selector": "tbody tr"},
            {"id": "end", "type": "end"},
        ],
        [
            {"id": "e0", "source": "start", "target": "n_open"},
            {"id": "e1", "source": "n_open", "target": "n_wait"},
            {"id": "e2", "source": "n_wait", "target": "n_extract"},
            {"id": "e3", "source": "n_extract", "target": "end"},
        ],
    )


_PROTECTED = ChangeContext(protected_node_ids=frozenset({"n_open", "n_wait", "n_extract"}))


def _issues(report) -> set[str]:
    return {f["issue"] for f in report.findings}


def test_no_effective_change_is_refused_before_anything_else_judges_it() -> None:
    """改完等于没改：写下去只是白耗一轮，而且下游判据谁也说不出问题在哪。"""
    before = _chain()
    report = inspect_change(before, _flow(before["nodes"], before["edges"]))
    assert report.rejected
    assert _issues(report) == {"no_effective_change"}
    refusal = report.refusal()
    # 前端按 blocked_ 前缀渲染成"已阻断"；error 让编排层所有"写入成功"分支跳过
    assert refusal["status"] == "blocked_by_change_lint"
    assert refusal["error"]


def test_layout_only_rewrites_count_as_no_change() -> None:
    """每次写入都会重排坐标、运行会回写状态。把它们算进差分，这道闸就永不触发。"""
    before = _chain()
    after = _flow(
        [{**n, "position": {"x": 999, "y": 111}, "status": "success"} for n in before["nodes"]],
        before["edges"],
    )
    assert _issues(inspect_change(before, after)) == {"no_effective_change"}


def test_rename_only_call_is_not_treated_as_a_no_op() -> None:
    """update_flow 同时改流程名时，定义没变也是一次真实调用。"""
    before = _chain()
    report = inspect_change(
        before, _flow(before["nodes"], before["edges"]), allow_no_effective_change=True
    )
    assert not report.rejected


def test_deleting_a_protected_browser_chain_node_is_refused() -> None:
    before = _chain()
    after = _flow(
        [n for n in before["nodes"] if n["id"] != "n_wait"],
        [
            {"id": "e0", "source": "start", "target": "n_open"},
            {"id": "e_new", "source": "n_open", "target": "n_extract"},
            {"id": "e3", "source": "n_extract", "target": "end"},
        ],
    )
    report = inspect_change(before, after, context=_PROTECTED)
    assert "repair_removed_existing_nodes" in _issues(report)
    assert report.findings[0]["node_ids"] == ["n_wait"]


def test_cutting_every_edge_orphans_the_node_and_is_refused() -> None:
    """节点还在，但从起点走不到——运行时整段跳过，与删掉没有区别。

    旧实现要靠 remove_edge_ids/add_edges 反推一套边模型才判得出来；
    这里就是两次可达性比较。
    """
    before = _chain()
    after = _flow(
        before["nodes"],
        [
            {"id": "e0", "source": "start", "target": "n_open"},
            {"id": "e_bypass", "source": "n_open", "target": "n_extract"},
            {"id": "e3", "source": "n_extract", "target": "end"},
        ],
    )
    report = inspect_change(before, after, context=_PROTECTED)
    assert "repair_orphaned_browser_chain_node_via_edges" in _issues(report)
    assert report.findings[0]["node_ids"] == ["n_wait"]


def test_rewiring_that_keeps_the_node_on_the_path_is_allowed() -> None:
    """在主链路中间插一个循环节点是正当修复，不能因为动了边就拦。"""
    before = _chain()
    after = _flow(
        [*before["nodes"], {"id": "n_loop", "type": "control.foreach", "itemsVariable": "urls"}],
        [
            {"id": "e0", "source": "start", "target": "n_open"},
            {"id": "e_a", "source": "n_open", "target": "n_loop"},
            {"id": "e_b", "source": "n_loop", "target": "n_wait", "label": "body"},
            {"id": "e2", "source": "n_wait", "target": "n_extract"},
            {"id": "e3", "source": "n_extract", "target": "end"},
        ],
    )
    assert not inspect_change(before, after, context=_PROTECTED).rejected


def test_retyping_a_protected_node_to_script_is_refused_even_without_a_type_in_the_patch() -> None:
    """旧实现只看 patch 里的 type，patch 不带 type 就放行。

    但 after 的节点类型是事实：先用一次调用把节点改成 script.python，
    再用一次只改 code 的调用塞进 requests——第二次调用的参数里没有 type，
    旧判定看不见，差分看得见。
    """
    before = _flow(
        [
            {"id": "start", "type": "start"},
            {"id": "n_extract", "type": "script.python", "code": "print('placeholder')"},
            {"id": "end", "type": "end"},
        ],
        [
            {"id": "e0", "source": "start", "target": "n_extract"},
            {"id": "e1", "source": "n_extract", "target": "end"},
        ],
    )
    after = _flow(
        [
            {**n, "code": "import requests\nrequests.get('https://example.com')"}
            if n["id"] == "n_extract" else n
            for n in before["nodes"]
        ],
        before["edges"],
    )
    report = inspect_change(
        before, after, context=ChangeContext(protected_node_ids=frozenset({"n_extract"}))
    )
    assert _issues(report) == {"repair_replaced_node_with_script", "repair_uses_script_http_fetch"}


def test_script_http_fetch_is_refused_for_python_javascript_and_shell() -> None:
    before = _chain()
    for code in (
        "import urllib.request\nurllib.request.urlopen('https://example.com')",
        "const res = await fetch('https://example.com'); console.log(await res.text());",
        "curl -s https://example.com",
    ):
        after = _flow(
            [
                {"id": "n_extract", "type": "script.python", "code": code}
                if n["id"] == "n_extract" else n
                for n in before["nodes"]
            ],
            before["edges"],
        )
        report = inspect_change(before, after, context=_PROTECTED)
        assert _issues(report) == {
            "repair_replaced_node_with_script", "repair_uses_script_http_fetch",
        }, code


def test_a_new_script_node_that_bypasses_the_browser_chain_is_refused() -> None:
    """不改主链路、另起一个脚本节点抓同一个页面，绕过效果是一样的。"""
    before = _chain()
    after = _flow(
        [
            *before["nodes"],
            {"id": "n_side", "type": "script.python", "code": "import requests\nrequests.get(u)"},
        ],
        [*before["edges"], {"id": "e_side", "source": "n_extract", "target": "n_side"}],
    )
    assert "repair_uses_script_http_fetch" in _issues(
        inspect_change(before, after, context=_PROTECTED)
    )


def test_a_preexisting_script_fetch_node_does_not_block_unrelated_edits() -> None:
    """判据是"本次改动引入"。流程本来就有脚本抓取节点时，改别处不该被它连坐——
    否则这类流程一次都改不动。"""
    before = _flow(
        [
            {"id": "start", "type": "start"},
            {"id": "n_side", "type": "script.python", "code": "import requests\nrequests.get(u)"},
            {"id": "n_extract", "type": "browser.extract", "selector": "tbody tr"},
            {"id": "end", "type": "end"},
        ],
        [
            {"id": "e0", "source": "start", "target": "n_side"},
            {"id": "e1", "source": "n_side", "target": "n_extract"},
            {"id": "e2", "source": "n_extract", "target": "end"},
        ],
    )
    after = _flow(
        [{**n, "selector": "table tbody tr"} if n["id"] == "n_extract" else n for n in before["nodes"]],
        before["edges"],
    )
    assert not inspect_change(
        before, after, context=ChangeContext(protected_node_ids=frozenset({"n_extract"}))
    ).rejected


def test_editing_non_protected_nodes_is_ordinary_work() -> None:
    """删掉/改写从来不在主链路上的节点（遗留的调试脚本）是正常编辑。"""
    before = _flow(
        [
            {"id": "start", "type": "start"},
            {"id": "n_extract", "type": "browser.extract", "selector": "tbody tr"},
            {"id": "n_debug", "type": "script.python", "code": "print('marker')"},
            {"id": "end", "type": "end"},
        ],
        [
            {"id": "e0", "source": "start", "target": "n_extract"},
            {"id": "e1", "source": "n_extract", "target": "n_debug"},
            {"id": "e2", "source": "n_debug", "target": "end"},
        ],
    )
    after = _flow(
        [n for n in before["nodes"] if n["id"] != "n_debug"],
        [
            {"id": "e0", "source": "start", "target": "n_extract"},
            {"id": "e_new", "source": "n_extract", "target": "end"},
        ],
    )
    assert not inspect_change(
        before, after, context=ChangeContext(protected_node_ids=frozenset({"n_extract"}))
    ).rejected


def test_no_protected_nodes_means_no_channel_judgement_at_all() -> None:
    """用户没在报原流程的问题时，重建流程是正当的——这道闸不该挂着。"""
    before = _chain()
    after = _flow(
        [
            {"id": "start", "type": "start"},
            {"id": "n_extract", "type": "script.python", "code": "import requests\nrequests.get(u)"},
            {"id": "end", "type": "end"},
        ],
        [
            {"id": "e0", "source": "start", "target": "n_extract"},
            {"id": "e1", "source": "n_extract", "target": "end"},
        ],
    )
    assert not inspect_change(before, after).rejected


def _selector_change(old: str, new: str) -> tuple[dict, dict]:
    before = _flow([{"id": "n1", "type": "browser.extract", "selector": old}])
    after = _flow([{"id": "n1", "type": "browser.extract", "selector": new}])
    return before, after


def test_field_oscillation_refuses_reverting_to_a_value_this_flow_already_tried() -> None:
    ledger = {"node_field_history": {"n1.selector": [".a", ".b"]}}
    before, after = _selector_change(".b", ".a")
    report = inspect_change(before, after, ledger=ledger)
    assert _issues(report) == {"field_oscillation"}
    assert report.findings[0]["field_history"] == {"n1.selector": [".a", ".b"]}


def test_field_oscillation_allows_a_value_never_tried_before() -> None:
    ledger = {"node_field_history": {"n1.selector": [".a", ".b"]}}
    before, after = _selector_change(".b", ".c")
    assert not inspect_change(before, after, ledger=ledger).rejected


def test_field_oscillation_unlocks_on_fresh_page_evidence_not_on_insisting() -> None:
    """永久拒绝会在旧值确实正确时把人锁死；靠"再提交一次"解锁又等于只是一句提醒。
    唯一的解锁凭据是新页面证据。"""
    ledger = {"node_field_history": {"n1.selector": [".a", ".b"]}}
    before, after = _selector_change(".b", ".a")
    assert inspect_change(before, after, ledger=ledger).rejected
    assert not inspect_change(
        before, after, ledger=ledger, context=ChangeContext(fresh_page_evidence=True)
    ).rejected


def test_tracked_changes_report_what_actually_landed() -> None:
    """台账要记真正落盘的取值。参数里记的是模型想改成什么，两者会不一致。"""
    before, after = _selector_change(".b", ".c")
    report = inspect_change(before, after)
    assert not report.rejected
    assert report.tracked_field_changes == ({"node_id": "n1", "field": "selector", "value": ".c"},)

    # 没动 selector 的写入不该往台账里记一笔——否则预算会被无关改动吃掉
    plain = inspect_change(
        _flow([{"id": "n1", "type": "browser.extract", "selector": ".b"}]),
        _flow([{"id": "n1", "type": "browser.extract", "selector": ".b", "timeoutMs": 5000}]),
    )
    assert plain.tracked_field_changes == ()


def test_clearing_a_field_to_null_is_not_a_tracked_change() -> None:
    """把 selector 显式写成 null 不是「换一个取值再试」，没有可回摆的对象。

    记成一次改动会往回摆历史里塞进 "None"，并虚耗一格 selector 修复预算——两个下游判据
    都吃这份 tracked_changes。改到 null 仍是一次真实写入（signature 变了、不算空转），
    只是不该被这三条判据盯上。
    """
    before = _flow([{"id": "n1", "type": "browser.extract", "selector": ".b"}])
    after = _flow([{"id": "n1", "type": "browser.extract", "selector": None}])
    report = inspect_change(before, after)
    assert not report.rejected
    assert report.tracked_field_changes == ()


def test_selector_fix_budget_requires_new_evidence_after_enough_blind_attempts() -> None:
    ledger = {"node_selector_fix_counts": {"n1": NODE_SELECTOR_FIX_BUDGET}}
    before, after = _selector_change(".b", ".brand-new")
    report = inspect_change(before, after, ledger=ledger)
    assert _issues(report) == {"selector_fix_budget_exhausted"}
    assert report.findings[0]["node_ids"] == ["n1"]

    # 改别的字段不该挡：这道闸针对的是盲改 selector
    assert not inspect_change(
        _flow([{"id": "n1", "type": "browser.extract", "selector": ".b"}]),
        _flow([{"id": "n1", "type": "browser.extract", "selector": ".b", "timeoutMs": 9000}]),
        ledger=ledger,
    ).rejected

    assert not inspect_change(
        before, after, ledger=ledger, context=ChangeContext(fresh_page_evidence=True)
    ).rejected


def test_selector_budget_cannot_be_dodged_by_rewriting_the_selector_differently() -> None:
    """回摆判定只认历史取值，"换个写法"能绕过它——预算闸就是补这个洞的。"""
    ledger = {
        "node_field_history": {"n1.selector": [".a", ".b"]},
        "node_selector_fix_counts": {"n1": NODE_SELECTOR_FIX_BUDGET},
    }
    before, after = _selector_change(".b", "table > tbody > tr:nth-child(1)")
    assert _issues(inspect_change(before, after, ledger=ledger)) == {"selector_fix_budget_exhausted"}


def test_contract_lines_are_renderable_bullets() -> None:
    """契约行直接注入 system prompt，空 bullet 会变成一条没有内容的规则。"""
    lines = change_lint_contract_lines()
    assert lines
    assert all(line.startswith("- ") and len(line) > 10 for line in lines)
