"""在真实 Chrome 上把配方生成的流程回放一遍。

探测通了只证明「看得见」，证不了「照配方写出来的流程能把数据筛对」。这一组测试把
task_manager 的单节点执行契约（`_resolve_node_variables` → runner.run →
apply_*_variables，脚本非零退出码转 RuntimeError）原样搬过来，在真实浏览器上跑，
selector 全部取自 `inspect_page` 返回的 `interaction_recipe`，不手写。

它证明的是「运行时 + 配方 selector 能回放，换一组输入照样对」，**不证明模型能生成
这套流程**——后者只有真实模型评测能回答（evals/run_evals.py）。

装不到浏览器的机器上整组跳过——跳过要说清是环境缺件，不能让它看起来像通过了。
"""
from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest

from app.services.ai_tools import page_session
from app.services.ai_tools.executor import RpaToolExecutor
from app.services.browser_action_runner import (
    BrowserActionRunner,
    apply_browser_result_variables,
)
from app.services.runtime_variables import RuntimeVariableStore
from app.services.script_action_runner import (
    ScriptActionRunner,
    apply_script_result_variables,
    is_script_action_node,
)
from app.services.task_manager import _resolve_node_variables

PAGES = Path(__file__).parent / "pages"
NODE_TIMEOUT_MS = 15_000


def _url(name: str) -> str:
    return (PAGES / name).resolve().as_uri()


@pytest.fixture(autouse=True)
async def _close_probe_session() -> Any:
    yield
    await page_session.close_current("test_cleanup")


async def _recipe_for(page: str) -> dict[str, Any]:
    """取这一页的真实配方。selector 手写就变成了「测我写的 selector」，证不了配方可用。"""
    if find_spec("playwright") is None:
        pytest.skip("环境未安装 playwright")
    executor = RpaToolExecutor(flow_service=None, task_manager=None)  # type: ignore[arg-type]
    result = await executor._inspect_page_via_browser(url=_url(page))
    if "error" in result:
        pytest.skip(f"无法在本机拉起浏览器：{result['error']}")
    if "_browser_blocked" in result:
        pytest.skip(f"浏览器通道不可用：{result['_browser_blocked']}")
    generic = [c for c in result.get("date_controls") or [] if c.get("library") == "generic"]
    assert generic, result.get("date_controls")
    return generic[0]["interaction_recipe"]


@pytest.fixture
async def replay(tmp_path: Path) -> Any:
    """按 task_manager 的单节点契约回放节点表；profile 用 tmp_path，不碰探测会话那份。"""
    if find_spec("playwright") is None:
        pytest.skip("环境未安装 playwright")
    runner = BrowserActionRunner(str(tmp_path / "profile"))
    scripts = ScriptActionRunner()
    try:
        context = await runner.create_context(headless=True, owner="test_real_page_flow_replay")
    except Exception as exc:  # noqa: BLE001 - 环境缺件与实现缺陷要分开报
        pytest.skip(f"无法在本机拉起浏览器：{exc}")

    async def _run(nodes: list[dict[str, Any]], variables: dict[str, Any] | None = None) -> RuntimeVariableStore:
        store = RuntimeVariableStore.from_initial(variables or {})
        for node in nodes:
            resolved = _resolve_node_variables(node, store)
            if is_script_action_node(resolved):
                script_result = await scripts.run(resolved, store, timeout_ms=NODE_TIMEOUT_MS)
                # 非零退出码转异常是 task_manager 的行为，不搬过来的话硬门控形同不存在
                if script_result.exit_code != 0:
                    raise RuntimeError(f"脚本退出码 {script_result.exit_code}: {script_result.stderr or script_result.stdout}")
                apply_script_result_variables(node, script_result, store)
                continue
            browser_result = await runner.run(resolved, store, context, timeout_ms=NODE_TIMEOUT_MS)
            apply_browser_result_variables(node, browser_result, store)
            store.set("__last_detail", browser_result.detail)
        return store

    try:
        yield _run
    finally:
        await runner.close_context(context)


# 配方主路线的两段硬门控，按配方原文写：回读不符即 SystemExit，抓回的数据越界即 SystemExit。
# 第二段只断言不过滤——删掉越界行会把页面筛选失效完全掩盖成绿灯。
_READBACK_GATE = """
if _vars["start_readback"] != _vars["start_date"] or _vars["end_readback"] != _vars["end_date"]:
    raise SystemExit(f"回读不符: {_vars['start_readback']!r} {_vars['end_readback']!r}")
"""

_ROW_GATE = """
rows = _vars["rows"]
start = _vars["start_date"].replace("/", "-")
end = _vars["end_date"].replace("/", "-")
bad = [r for r in rows if not (start <= str(r["__DATE_KEY__"]).replace("/", "-") <= end)]
if bad:
    raise SystemExit(f"筛选未生效，越界行: {bad}")
if not rows:
    raise SystemExit("筛选后一行都没有，无法证明筛选生效")
"""


def _nodes(page: str, recipe: dict[str, Any], case: dict[str, Any], *, submit: bool) -> list[dict[str, Any]]:
    """按配方 steps 的顺序搭节点：fill → fill → press Enter → 回读 → 门控 → 抓表 → 门控。"""
    trigger = recipe["trigger"]
    end_input = recipe["end_input"]
    nodes: list[dict[str, Any]] = [
        {"id": "n1", "type": "browser.open", "targetUrl": _url(page)},
        {"id": "n2", "type": "browser.fill", "selector": trigger, "inputValue": "${var.start_date}", "delayMs": 200},
        {"id": "n3", "type": "browser.fill", "selector": end_input, "inputValue": "${var.end_date}", "delayMs": 200},
    ]
    if submit:
        nodes.append({"id": "n4", "type": "browser.press", "selector": end_input, "inputValue": "Enter", "delayMs": 300})
    nodes += [
        {"id": "n5", "type": "browser.extract", "selector": trigger, "extractMode": "attribute",
         "attribute": "value", "firstValueVariable": "start_readback"},
        {"id": "n6", "type": "browser.extract", "selector": end_input, "extractMode": "attribute",
         "attribute": "value", "firstValueVariable": "end_readback"},
        {"id": "n7", "type": "script.python", "code": _READBACK_GATE,
         "inputVariables": ["start_date", "end_date", "start_readback", "end_readback"]},
        {"id": "n8", "type": "browser.extract", "selector": case["rows_selector"],
         "extractMode": "table", "outputVariable": "rows"},
        {"id": "n9", "type": "script.python", "code": _ROW_GATE.replace("__DATE_KEY__", case["date_key"]),
         "inputVariables": ["rows", "start_date", "end_date"]},
    ]
    return nodes


# 两页的业务数据一样，提交时机不同：一页只在 keydown Enter 上筛，一页在 change（失焦）上筛。
# 同一份配方要在两种提交时机上都成立，否则「换个实现也能用」只是同一种页面测了两遍。
CASES: dict[str, dict[str, Any]] = {
    "filter_enter_commit.html": {
        "rows_selector": "#bill-body tr",
        "date_key": "日期",
        "id_key": "单号",
        "ranges": [
            ("2026-06-01", "2026-06-30", ["B-02", "B-03"]),
            ("2026-05-01", "2026-06-05", ["B-01", "B-02"]),
        ],
    },
    "filter_change_commit.html": {
        "rows_selector": "#voucher-body tr",
        "date_key": "记账日期",
        "id_key": "票据号",
        "ranges": [
            ("2026/06/01", "2026/06/30", ["V-02", "V-03"]),
            ("2026/05/01", "2026/06/05", ["V-01", "V-02"]),
        ],
    },
}


@pytest.mark.parametrize("page", list(CASES))
async def test_generic_date_recipe_replays_on_both_commit_styles_and_survives_new_dates(
    replay: Any, page: str
) -> None:
    """一份配方、两种提交时机、每页两组日期：全部回放通过才算配方可用。

    第二组日期是「换一组输入数据再跑一遍」：只跑一组，写死的 selector 与恰好对上的
    数据分不开——换个区间还对，才排除了「碰巧筛出来这几行」。
    """
    recipe = await _recipe_for(page)
    case = CASES[page]
    # 配方必须挑中筛选用的那两个框；filter_change_commit 上还有个「生日」框，挑错了这一轮就筛不动
    assert recipe["trigger"] != recipe["end_input"]
    assert "birthday" not in recipe["trigger"] and "birthday" not in recipe["end_input"]

    nodes = _nodes(page, recipe, case, submit=True)
    for start, end, expected_ids in case["ranges"]:
        store = await replay(nodes, {"start_date": start, "end_date": end})
        rows = store.get("rows")
        assert [row[case["id_key"]] for row in rows] == expected_ids, rows
        assert store.get("start_readback") == start and store.get("end_readback") == end


async def test_typed_text_alone_passes_readback_but_the_data_gate_still_catches_it(
    replay: Any,
) -> None:
    """去掉提交动作后回读照样通过，只有抓回的数据能证明筛选没生效。

    这是配方把「数据断言」列为必需步骤的全部理由：少了它，一次没提交的筛选会带着
    满页数据交上去，且回读值完全正常——看起来比真的成功还像成功。
    """
    page = "filter_enter_commit.html"
    recipe = await _recipe_for(page)
    nodes = _nodes(page, recipe, CASES[page], submit=False)

    with pytest.raises(RuntimeError) as excinfo:
        await replay(nodes, {"start_date": "2026-06-01", "end_date": "2026-06-30"})
    # 报的必须是数据门控；若是回读门控先炸，说明这一页连文本都没写进去，测的就不是这件事
    assert "筛选未生效" in str(excinfo.value), str(excinfo.value)
    assert "回读不符" not in str(excinfo.value)


async def test_pagination_sweep_accumulates_every_page_and_stops_by_page_state(
    replay: Any,
) -> None:
    """翻页的终止条件与页数上限都要能在运行时改：换 maxIterations 就换出不同的页数。"""
    def node(max_iterations: int) -> list[dict[str, Any]]:
        return [
            {"id": "p1", "type": "browser.open", "targetUrl": _url("pagination.html")},
            {"id": "p2", "type": "browser.paginateNext", "selector": ".next-page",
             "targetSelector": "#grid-body tr", "extractMode": "table", "maxIterations": max_iterations,
             "delayMs": 200, "outputVariable": "rows", "pageCountVariable": "pages"},
        ]

    capped = await replay(node(2))
    # 这张表只有一列，表头不足 2 列时 table 模式按 list[list] 交出（见 _TABLE_EXTRACT_SCRIPT）
    assert [row[0] for row in capped.get("rows")] == ["P1-A", "P2-A"]
    assert capped.get("pages") == 2
    assert "stop=max_iterations_reached" in str(capped.get("__last_detail"))

    full = await replay(node(20))
    assert [row[0] for row in full.get("rows")] == ["P1-A", "P2-A", "P3-A"]
    assert full.get("pages") == 3
    # 末页的判据是按钮置灰，不是「翻了 3 次」——次数写死的话页数一变就错
    assert "stop=next_button_disabled" in str(full.get("__last_detail"))


async def test_a_sidebar_sharing_the_row_class_only_stays_out_when_the_selector_is_scoped(
    replay: Any,
) -> None:
    """侧栏用同一个类名：不收在结果区里的选择器会多交两条，流程照样绿灯。

    这里刻意两条都断言。只断言收窄那条，诱饵哪天失效了测试还是绿的，评测里这一案例
    就变成白送分——而它要量的正是模型会不会圈错范围。
    """
    def nodes(selector: str, owner: str) -> list[dict[str, Any]]:
        return [
            {"id": "r1", "type": "browser.open", "targetUrl": _url("eval_repeat_list.html")},
            {"id": "r2", "type": "browser.fill", "selector": "#q-owner", "inputValue": owner},
            {"id": "r3", "type": "browser.click", "selector": "#q-submit"},
            {"id": "r4", "type": "browser.extract", "selector": selector,
             "extractMode": "text", "outputVariable": "rows"},
        ]

    scoped = await replay(nodes("#result-list .record .code", "张三"))
    assert scoped.get("rows") == ["R-01", "R-03"], scoped.get("rows")

    leaked = await replay(nodes(".record .code", "张三"))
    assert leaked.get("rows") == ["R-01", "R-03", "R-97", "R-98"], leaked.get("rows")


async def test_the_login_path_has_to_be_walked_before_the_target_table_exists(
    replay: Any,
) -> None:
    """目标页未登录时整页换成登录表单：直接抓表拿不到任何行，得先走完登录。

    会话状态在 URL 上，所以这条路径每次回放都重走一遍，不会因为浏览器里留了登录态
    而在第二次变成「直接就有表」。
    """
    direct = [
        {"id": "l1", "type": "browser.open", "targetUrl": _url("eval_login_redirect.html")},
        {"id": "l2", "type": "browser.extract", "selector": "#username",
         "extractMode": "count", "firstValueVariable": "login_inputs"},
    ]
    assert (await replay(direct)).get("login_inputs") == "1"

    full = [
        *direct[:1],
        {"id": "l3", "type": "browser.fill", "selector": "#username", "inputValue": "${var.username}"},
        {"id": "l4", "type": "browser.fill", "selector": "#password", "inputValue": "${var.password}"},
        {"id": "l5", "type": "browser.click", "selector": "#login-submit"},
        {"id": "l6", "type": "browser.waitFor", "selector": "#bill-table"},
        {"id": "l7", "type": "browser.fill", "selector": "#q-kind", "inputValue": "${var.kind}"},
        {"id": "l8", "type": "browser.click", "selector": "#q-submit"},
        {"id": "l9", "type": "browser.extract", "selector": "#bill-table",
         "extractMode": "table", "outputVariable": "rows"},
    ]
    store = await replay(full, {"username": "replay-user", "password": "replay-pass", "kind": "采购"})
    assert store.get("rows") == [{"单号": "T-04", "类型": "采购", "金额": "910"}], store.get("rows")


async def test_waiting_on_the_target_region_survives_both_the_first_paint_and_the_refill(
    replay: Any,
) -> None:
    """首屏只有加载态、筛选本身也异步：等的是目标区域和回填后的行，不是固定时长。

    提交后 tbody 先被清空再回填，所以第二次等的是行而不是表——表一直在，等它等不到
    任何东西，抓回的会是上一次的结果。
    """
    nodes = [
        {"id": "a1", "type": "browser.open", "targetUrl": _url("eval_async_table.html")},
        {"id": "a2", "type": "browser.waitFor", "selector": "#bill-table"},
        {"id": "a3", "type": "browser.fill", "selector": "#q-kind", "inputValue": "报销"},
        {"id": "a4", "type": "browser.click", "selector": "#q-submit"},
        {"id": "a5", "type": "browser.waitFor", "selector": "#bill-body tr"},
        {"id": "a6", "type": "browser.extract", "selector": "#bill-table",
         "extractMode": "table", "outputVariable": "rows"},
    ]
    store = await replay(nodes)
    assert store.get("rows") == [
        {"单号": "A-01", "类型": "报销", "金额": "1200"},
        {"单号": "A-03", "类型": "报销", "金额": "430"},
    ], store.get("rows")


async def test_a_row_selector_on_a_legit_empty_table_yields_no_rows_instead_of_timing_out(
    replay: Any,
) -> None:
    """合法空表（thead 在、tbody 清空）交 []：行选择器一个元素都命中不到，等的是区域不是行。

    这一条走的是最慢的路径——探到「有列标题、零数据行」还要等满 NODE_TIMEOUT_MS 才认，
    否则异步表格的首屏会被当成空结果。慢是判据的一部分，不是可以省掉的等待。
    """
    nodes = [
        {"id": "e1", "type": "browser.open", "targetUrl": _url("eval_native_table.html")},
        {"id": "e2", "type": "browser.fill", "selector": "#q-region", "inputValue": "西北"},
        {"id": "e3", "type": "browser.click", "selector": "#q-submit"},
        {"id": "e4", "type": "browser.extract", "selector": "#sales-body tr",
         "extractMode": "table", "outputVariable": "rows"},
    ]
    store = await replay(nodes)
    assert store.get("rows") == [], store.get("rows")


async def test_a_selector_that_frames_no_table_still_fails_instead_of_passing_as_empty(
    replay: Any,
) -> None:
    """圈到的元素里没有表格行：立刻报 no_rows_in_scope，不等、也不交空结果。

    与上一条的区别全在「圈到的是不是表格」：零行两种成因的出路相反，一个要收一个要改
    selector，混成同一个空结果就是让模型去改一个本来对的选择器。
    """
    nodes = [
        {"id": "b1", "type": "browser.open", "targetUrl": _url("eval_native_table.html")},
        {"id": "b2", "type": "browser.extract", "selector": ".query-bar",
         "extractMode": "table", "outputVariable": "rows"},
    ]
    with pytest.raises(Exception) as excinfo:
        await replay(nodes)
    assert "没有任何表格行" in str(excinfo.value), str(excinfo.value)


async def test_a_selector_pointing_at_no_such_table_reports_the_selector_not_the_empty_state(
    replay: Any,
) -> None:
    """连空表的列标题行都找不到：等满超时后报「分不清没加载还是选择器写错」，不能交 []。"""
    nodes = [
        {"id": "m1", "type": "browser.open", "targetUrl": _url("eval_native_table.html")},
        {"id": "m2", "type": "browser.extract", "selector": "#no-such-body tr",
         "extractMode": "table", "outputVariable": "rows"},
    ]
    with pytest.raises(Exception) as excinfo:
        await replay(nodes)
    message = str(excinfo.value)
    assert "一个元素都没命中" in message, message
    assert "#no-such-body tr" in message, message


async def test_the_async_first_paint_is_not_reported_as_an_empty_table(
    replay: Any,
) -> None:
    """首屏连表壳都还没有就发起提取：等出来的是 4 行，不是空表。

    这是「探到空状态就立刻收」的反例：本页 2.5s 后才渲染表格，提交后还要清空再回填
    600ms。两次都不等就抓，第一次交空结果、第二次交上一轮的数据，页面全程不报错。
    """
    nodes = [
        {"id": "f1", "type": "browser.open", "targetUrl": _url("eval_async_table.html")},
        {"id": "f2", "type": "browser.extract", "selector": "#bill-body tr",
         "extractMode": "table", "outputVariable": "first_paint"},
        {"id": "f3", "type": "browser.fill", "selector": "#q-kind", "inputValue": "报销"},
        {"id": "f4", "type": "browser.click", "selector": "#q-submit"},
        {"id": "f5", "type": "browser.extract", "selector": "#bill-body tr",
         "extractMode": "table", "outputVariable": "filtered"},
    ]
    store = await replay(nodes)
    assert [row["单号"] for row in store.get("first_paint")] == ["A-01", "A-02", "A-03", "A-04"], store.get("first_paint")
    assert [row["单号"] for row in store.get("filtered")] == ["A-01", "A-03"], store.get("filtered")


async def test_a_blank_header_column_keeps_its_slot_on_both_channels(replay: Any) -> None:
    """空表头列命名成 列1、列位不左移，预期值与扩展通道逐字相同。

    页面是 test_real_extension_page_channel 嵌进它那张页面的同一份片段，两条通道跑的
    是同一份 DOM、比的是同一份预期；期望值改在一边，另一边的断言会立刻红。
    """
    nodes = [
        {"id": "h1", "type": "browser.open", "targetUrl": _url("table_blank_header_col.html")},
        {"id": "h2", "type": "browser.extract", "selector": "#pricing tr",
         "extractMode": "table", "outputVariable": "rows"},
    ]
    store = await replay(nodes)
    assert store.get("rows") == [
        {"列1": "", "Model Name": "model-a", "Ratio": "1.5", "Price": "$3"},
        {"列1": "", "Model Name": "model-b", "Ratio": "3", "Price": "$6"},
    ], store.get("rows")
