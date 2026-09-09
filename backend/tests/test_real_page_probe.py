"""真实 Chrome 上的观察与交互回归。

假对象能让最关键的几条判据全部「测试里通、页面上不通」：`matches` 到底数出几个、
开放 shadow root 有没有真的穿透、点一下弹层是不是真的打开了、SPA 加载态认不认得出来。
这些只有真实 DOM 能回答，所以这一组测试宁可慢也必须走 `_inspect_page_via_browser`
与 `_interact_page` 本身，不替换任何一层。

装不到浏览器的机器上整组跳过——跳过要说清是环境缺件，不能让它看起来像通过了。
"""
from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest

from app.services.ai_tools import page_session
from app.services.ai_tools.executor import RpaToolExecutor

PAGES = Path(__file__).parent / "pages"


def _url(name: str) -> str:
    return (PAGES / name).resolve().as_uri()


@pytest.fixture(scope="module")
def executor() -> RpaToolExecutor:
    if find_spec("playwright") is None:
        pytest.skip("环境未安装 playwright")
    return RpaToolExecutor(flow_service=None, task_manager=None)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
async def _close_session() -> Any:
    yield
    await page_session.close_current("test_cleanup")


async def _observe(executor: RpaToolExecutor, name: str, **kwargs: Any) -> dict[str, Any]:
    result = await executor._inspect_page_via_browser(url=_url(name), **kwargs)
    if "error" in result:
        pytest.skip(f"无法在本机拉起浏览器：{result['error']}")
    if "_browser_blocked" in result:
        pytest.skip(f"浏览器通道不可用：{result['_browser_blocked']}")
    return result


def _by_selector(items: list[dict[str, Any]], selector: str) -> dict[str, Any] | None:
    return next((i for i in items if i.get("selector") == selector), None)


def _active_page(result: dict[str, Any]) -> str | None:
    """当前页码：页码控件的「选中」只写在 class 上，没有任何 ARIA 语义。"""
    for item in result.get("buttons") or []:
        if "active" in str(item.get("cls") or ""):
            return item.get("text")
    return None


async def test_native_date_page_gives_a_range_recipe_and_flags_duplicate_buttons(
    executor: RpaToolExecutor,
) -> None:
    """原生 input[type=date]：没有任何组件库指纹，必须由通用识别兜住，
    否则模型对着最常见的一种日期筛选也只能凭空猜交互方式。"""
    result = await _observe(executor, "native_date.html")

    controls = result.get("date_controls") or []
    generic = [c for c in controls if c.get("library") == "generic"]
    assert generic, f"原生日期输入框没有拿到通用配方：{controls}"
    recipe = generic[0]["interaction_recipe"]
    assert generic[0]["type"] == "generic/date-range-input"
    assert recipe["trigger"] == "#start-date" and recipe["end_input"] == "#end-date"

    # 页面上有三个「查询」：筛选栏一个 + 每行一个。selector 必须逐个唯一，并带 same_text_count；
    # 少了这个数字，模型会写按文本匹配的 selector，运行时点到的是第一行而不是它想点的那行。
    searches = [b for b in result.get("buttons") or [] if b.get("text") == "查询"]
    assert len(searches) == 3, result.get("buttons")
    assert all(b.get("same_text_count") == 3 for b in searches), searches
    assert len({b["selector"] for b in searches}) == 3, searches
    row_scoped = [b for b in searches if "tbody" in b["selector"]]
    assert len(row_scoped) == 2 and all("tr:nth-of-type" in b["selector"] for b in row_scoped), row_scoped


async def test_duplicate_button_selector_is_refused_instead_of_hitting_the_first(
    executor: RpaToolExecutor,
) -> None:
    await _observe(executor, "native_date.html")
    refused = await executor._interact_page(action="click", selector=".btn-link")
    assert refused["status"] == "ambiguous_selector"
    assert refused["matches"] == 4  # 两行 × 查询/编辑
    assert refused["required_action"] == "narrow_selector_or_use_element_ref"

    # 只收窄到「第二行的 .btn-link」仍是两个（查询 + 编辑），差一点也算歧义
    still = await executor._interact_page(action="click", selector="#order-table tbody tr:nth-child(2) .btn-link")
    assert still["status"] == "ambiguous_selector" and still["matches"] == 2

    ok = await executor._interact_page(
        action="click", selector="#order-table tbody tr:nth-child(2) .btn-link:nth-of-type(1)"
    )
    assert ok["status"] == "ok"


async def test_element_ui_panel_only_exists_after_the_click_in_the_same_session(
    executor: RpaToolExecutor,
) -> None:
    """日期弹层的真实结构只在点开后存在。这条链断了，模型只能照抄模板 selector——
    模板 selector 是「哪个库」级别的猜测，不是这个页面的事实。"""
    first = await _observe(executor, "element_ui_range.html")
    assert not any(".el-picker-panel" in str(b.get("selector")) for b in first.get("buttons") or [])

    opened = await executor._interact_page(action="click", selector="#range-editor", wait_selector=".el-picker-panel")
    assert opened["status"] == "ok"
    assert opened["effect"]["changed"] is True, opened
    classes = opened["observation"].get("page_classes") or []
    assert "el-picker-panel" in classes, "点开后的观察里没有面板，说明观察没有落在同一次交互后的页面上"
    assert "warning" not in opened


async def test_two_range_pickers_on_a_real_page_resolve_inside_their_own_container(
    executor: RpaToolExecutor,
) -> None:
    result = await _observe(executor, "antd_range.html")
    controls = [c for c in result.get("date_controls") or [] if c.get("library") == "ant-design"]
    assert len(controls) == 2, f"两个 RangePicker 应该各自成一个实例：{controls}"
    triggers = {c["interaction_recipe"].get("trigger") for c in controls}
    assert all(t and ("#search-range" in t or "#edit-range" in t) for t in triggers), triggers
    assert len({t.split(" ")[0] for t in triggers if t}) == 2, f"两个实例落在同一个容器里：{triggers}"


async def test_spa_still_loading_is_reported_then_observed_again_without_navigating(
    executor: RpaToolExecutor,
) -> None:
    """带 url 那次固定等 3s，页面 4.5s 才渲染：先如实报加载中，再在同一页面上等出内容。

    没有「不导航地再看一次」这条路，模型只能重新 goto，SPA 的计时器跟着重置，
    它会永远停在加载态里改流程拓扑。
    """
    first = await _observe(executor, "spa_async.html")
    assert first["spa_loading"] is True
    assert "warning" in first
    session = page_session.get_session()
    assert session is not None
    assert await session.probe_version() == first["observation_version"]

    second = await executor._inspect_page_via_browser(wait_selector="#report-table")
    assert "requested_url" not in second, "第二次观察不该再导航"
    assert second["spa_loading"] is False
    tables = [t for t in second.get("tables") or [] if t.get("container_selector") == "#report-table"]
    assert tables and tables[0]["headers"] == ["项目", "金额"], second.get("tables")
    assert second["observation_version"] > first["observation_version"]
    assert second["url"] == first["url"]


async def test_readonly_calendar_recipe_marks_typing_unusable_and_the_panel_opens(
    executor: RpaToolExecutor,
) -> None:
    """readonly 的触发框不接受手输，键入路线整条不可用。识别不出来，
    模型会写「fill + Enter」然后拿回读校验反复失败，永远走不到点格子那条路。"""
    result = await _observe(executor, "readonly_calendar.html")
    generic = [c for c in result.get("date_controls") or [] if c.get("library") == "generic"]
    assert generic, result.get("date_controls")
    assert generic[0]["interaction_recipe"].get("readonly_trigger") is True

    opened = await executor._interact_page(action="click", selector="#pick-date", wait_selector=".cal-title")
    assert opened["status"] == "ok" and opened["effect"]["changed"] is True
    assert "layers" in opened["effect"]["diff"], opened["effect"]
    observation = opened["observation"]
    # 面板是 div/a 拼的，没有 role=gridcell/li/td：日期格必须照样带 ref 和 selector 回来，
    # 否则模型只能照类名猜格子、再盲选一个 nth——「点开之后还是在猜」。
    layers = observation.get("open_layers") or []
    assert any("cal-panel" in (layer.get("classes") or []) for layer in layers), layers
    days = [o for o in observation.get("visible_options") or [] if o["text"] in ("1", "2", "3")]
    assert len(days) == 3, observation.get("visible_options")
    assert all(d.get("ref") and d.get("selector") for d in days), days
    # 翻月按钮与面板标题是 repeat_until 翻到目标月的两个必需件
    turns = [b for b in observation.get("buttons") or [] if b.get("text") in ("上月", "下月")]
    assert len(turns) == 2 and all(b.get("selector") for b in turns), observation.get("buttons")
    assert "cal-title" in (observation.get("page_classes") or [])


async def test_iframe_content_is_only_visible_after_switching_into_it(
    executor: RpaToolExecutor,
) -> None:
    host = await _observe(executor, "iframe_host.html")
    assert _by_selector(host.get("inputs") or [], "#host-keyword") is not None
    assert _by_selector(host.get("inputs") or [], "#child-keyword") is None

    inside = await executor._inspect_page_via_browser(frame_selector="#biz-frame")
    assert inside["frame_selector"] == "#biz-frame"
    assert _by_selector(inside.get("inputs") or [], "#child-keyword") is not None
    # 子文档里的日期输入框同样要拿到配方：iframe 里的页面不比主文档低一等
    assert [c for c in inside.get("date_controls") or [] if c.get("library") == "generic"]

    back = await executor._inspect_page_via_browser(frame_selector="main")
    assert "frame_selector" not in back
    assert _by_selector(back.get("inputs") or [], "#host-keyword") is not None


async def test_open_shadow_root_is_pierced_and_closed_one_is_only_flagged(
    executor: RpaToolExecutor,
) -> None:
    result = await _observe(executor, "shadow_dom.html")
    shadow_inputs = [i for i in result.get("inputs") or [] if i.get("shadow")]
    assert shadow_inputs, f"开放 shadow root 里的输入框没被穿透：{result.get('inputs')}"
    assert result.get("shadow_open_roots") == 1
    # 闭合 root 里的东西一个字都不能出现在观察里，只能把宿主元素报成「探测不到」
    assert not any("hidden-input" in str(i.get("selector")) for i in result.get("inputs") or [])
    assert "closed-widget" in (result.get("undetectable_custom_elements") or [])


async def test_virtual_list_grows_on_scroll_and_stops_without_a_hardcoded_count(
    executor: RpaToolExecutor,
) -> None:
    """滚动加载要有运行时可判定的终止条件：每滚一次长出新记录，滚不出新记录时 changed=false。

    「滚几次」不能写死——写死的次数只在生成当天成立。页尾那句「没有更多了」只有 5 个字，
    够不上 page_layout 的文本门槛，观察里看不到它，终止判据只能用记录增长本身。
    """
    first = await _observe(executor, "virtual_list.html")
    assert any(t.get("container_selector") == "#rows" for t in first.get("tables") or [])

    effects: list[dict[str, Any]] = []
    for _ in range(5):
        stepped = await executor._interact_page(action="scroll", selector="#scroller", wait_ms=300)
        assert stepped["status"] == "ok", stepped
        effects.append(stepped["effect"])
        if not stepped["effect"]["changed"]:
            break

    assert len(effects) >= 2, effects
    grew = [e["diff"]["options"] for e in effects if e["changed"] and "options" in e["diff"]]
    assert grew and all(pair[1] > pair[0] for pair in grew), effects
    assert effects[-1]["changed"] is False, effects


async def test_pagination_turns_the_page_and_reports_it_as_an_observable_change(
    executor: RpaToolExecutor,
) -> None:
    """翻页只换一行内容：元素数/浮层数/选项数全都不变，只有可见文字变了。

    这一条不报出来，一次成功的翻页会被当成「没有可观测变化」，模型接着去换元素、加等待。
    """
    result = await _observe(executor, "pagination.html")
    assert _active_page(result) == "1"
    pager = [b for b in result.get("buttons") or [] if "page-num" in str(b.get("cls") or "")]
    assert len(pager) == 3 and len({b["selector"] for b in pager}) == 3, pager

    stepped = await executor._interact_page(action="click", selector=".next-page", wait_ms=300)
    assert stepped["status"] == "ok" and stepped["effect"]["changed"] is True
    assert stepped["effect"]["diff"].get("visible_text") == "changed", stepped["effect"]
    assert _active_page(stepped["observation"]) == "2"
    assert "warning" not in stepped, stepped.get("warning")

    # 直接点页码同样要认得出来：它连 activeElement 都不改，只有文字指纹能证明换了页
    jumped = await executor._interact_page(action="click", selector=".page-num:nth-of-type(3)", wait_ms=300)
    assert jumped["effect"]["diff"].get("visible_text") == "changed", jumped["effect"]
    assert _active_page(jumped["observation"]) == "3"
    # 末页才置灰：disabled 只是结果，判终止的依据是页码不再变
    nexts = [b for b in jumped["observation"].get("buttons") or [] if b.get("text") == "下一页"]
    assert nexts and nexts[0].get("disabled") is True, nexts


@pytest.mark.parametrize(
    ("page", "kind", "readonly"),
    [
        ("holdout/custom_daterange.html", "generic/date-range-input", False),
        ("holdout/tdesign_like.html", "generic/date-input", True),
        # 这一页的两个日期框在 body 以下没有任何共同祖先（各自两层带身份的包装，uid 互不相交）。
        # 配成区间只能拿「两端包装的类名相同」当证据，而同一套组件在两个独立区域各用一次时
        # 类名必然也相同——那条依据会把导出区的框当成查询区的另一端。所以这一页按单日期交出，
        # 两端各自能用，并在 notes 里说明去哪找另一端。
        ("holdout/vant_range.html", "generic/date-input", True),
    ],
)
async def test_holdout_pages_get_a_usable_recipe_without_any_new_class_whitelist(
    executor: RpaToolExecutor, page: str, kind: str, readonly: bool
) -> None:
    """三个页面从未参与调配方：配方只按输入框的日期特征推断，换实现照样得给出能用的一套。

    按 class 指纹加白名单能让调过的页面全绿，却证不了「换一个组件库还行」——
    这三页就是用来把这种自证挡回去的，任何一条挂了都不许靠加类名修。
    """
    result = await _observe(executor, page)
    generic = [c for c in result.get("date_controls") or [] if c.get("library") == "generic"]
    assert generic, result.get("date_controls")
    control = generic[0]
    assert control["type"] == kind, control
    # 拆成多份配方时每一份都得各自能用：少一份就是有一个输入框连键入路线都没拿到
    for other in generic:
        other_trigger = other["interaction_recipe"].get("trigger")
        if other_trigger is None:
            continue
        probe = _by_selector(result.get("inputs") or [], other_trigger)
        assert probe is not None and probe.get("matches") is None, other
    recipe = control["interaction_recipe"]
    # selector 必须是这一页真实存在的，且唯一命中——照抄模板的 selector 一律不算
    trigger = _by_selector(result.get("inputs") or [], recipe["trigger"])
    assert trigger is not None and trigger.get("matches") is None, recipe["trigger"]
    if kind.endswith("range-input"):
        end = _by_selector(result.get("inputs") or [], recipe["end_input"])
        assert end is not None and end["selector"] != recipe["trigger"], recipe
        if page == "holdout/custom_daterange.html":
            assert recipe.get("container") == "#range-box", recipe
    assert recipe.get("readonly_trigger", False) is readonly, recipe
    assert any("script.python" in step for step in recipe["steps"]), recipe["steps"]
    assert recipe.get("fallback_steps"), recipe


async def test_two_forms_on_one_page_each_get_their_own_generic_range(
    executor: RpaToolExecutor,
) -> None:
    """查询区与编辑区各一套未知日期区间：配方必须各自成对，指向自己容器里的字段。

    跨容器配对在页面上不会报任何错——两个 fill 都成功、回读也对，查询条件只填了一半，
    页面照旧返回全量数据。所以这一条要在真实 DOM 上验证容器证据确实取到了：
    单元测试里的 containers 是手写的，真页面上它由探测脚本现算。
    """
    result = await _observe(executor, "two_range_forms.html")
    generic = [c for c in result.get("date_controls") or [] if c.get("library") == "generic"]
    ranges = [c for c in generic if c["type"] == "generic/date-range-input"]
    singles = [c for c in generic if c["type"] == "generic/date-input"]

    pairs = {(c["interaction_recipe"]["trigger"], c["interaction_recipe"]["end_input"]) for c in ranges}
    assert pairs == {("#search-start", "#search-end"), ("#edit-start", "#edit-end")}, generic
    # 生日框与筛选无关，既不能被配成区间的一端，也不能因此丢掉自己的单日期配方
    assert [c["interaction_recipe"]["trigger"] for c in singles] == ["#edit-birthday"], generic
    # 容器要如实报出来：模型据此判断这份配方属于查询区还是编辑区
    assert {c["interaction_recipe"].get("container") for c in ranges} == {"#search-form", "#edit-drawer"}


async def test_control_attribution_needs_a_named_shared_ancestor_on_a_real_page(
    executor: RpaToolExecutor,
) -> None:
    """归属证据必须在真实 DOM 上成立：单元测试里的 containers 是手写的。

    同一页上两种没有归属证据的形态：复用同一套包装类名的两个独立区域（配对会把导出区的框
    当成查询区的另一端），和唯一装着两端的无名 <form>（配对与拆开都拿不出证据）。
    """
    result = await _observe(executor, "date_attribution.html")
    generic = [c for c in result.get("date_controls") or [] if c.get("library") == "generic"]

    triggers = {c["interaction_recipe"].get("trigger") for c in generic if c["interaction_recipe"].get("trigger")}
    assert triggers == {"#query-start", "#export-end"}, generic
    assert all("end_input" not in c["interaction_recipe"] for c in generic), generic

    unresolved = [c for c in generic if c["type"] == "generic/date-unresolved"]
    assert len(unresolved) == 1, generic
    recipe = unresolved[0]["interaction_recipe"]
    assert recipe["actionable"] is False
    assert set(recipe["candidates"]) == {"#anon-start", "#anon-end"}, recipe
    assert any("没有 id 也没有类名" in slot for slot in recipe["unresolved_slots"]), recipe


async def test_focused_fill_lands_even_though_the_page_fingerprint_stays_put(
    executor: RpaToolExecutor,
) -> None:
    """已聚焦的输入框再填一次：整页指纹一动不动，值确实写进去了。

    只看指纹就会把它报成「没生效，换目标」。这条判据只能在真实浏览器上验证——
    「填第二次时焦点不再变化」是浏览器的行为，假对象里的 active 是我自己写的返回值。
    """
    await _observe(executor, "interaction_effects.html")

    first = await executor._interact_page(action="fill", selector="#keyword", value="订单", wait_ms=200)
    assert first["status"] == "ok", first
    assert first["action_effect"]["status"] == "target_reached", first
    assert first["input_value_after"] == "订单"

    again = await executor._interact_page(action="fill", selector="#keyword", value="发票", wait_ms=200)
    assert again["status"] == "ok", again
    assert again["effect"]["changed"] is False, again  # 焦点已在框里，DOM 没动，指纹无从变化
    assert again["action_effect"]["status"] == "target_reached", again
    assert "warning" not in again, again
    # 值写进去了也只到这一层：业务后置条件（筛选真的生效）仍未验证
    assert "business_check" in again

    same = await executor._interact_page(action="fill", selector="#keyword", value="发票", wait_ms=200)
    assert same["action_effect"]["status"] == "already_in_target_state", same
    assert "warning" not in same, same


async def test_native_select_switch_and_repeat_are_told_apart(
    executor: RpaToolExecutor,
) -> None:
    """原生 select 换选项不新增任何 DOM。重复设成同一项是幂等，不能被要求盲目换目标。"""
    await _observe(executor, "interaction_effects.html")

    await executor._interact_page(action="select_option", selector="#city", value="上海", wait_ms=200)
    switched = await executor._interact_page(action="select_option", selector="#city", value="广州", wait_ms=200)
    assert switched["status"] == "ok", switched
    assert switched["effect"]["changed"] is False, switched
    assert switched["action_effect"]["status"] == "target_reached", switched
    assert switched["action_effect"]["target_state"]["selected"] == ["广州"], switched

    repeated = await executor._interact_page(action="select_option", selector="#city", value="广州", wait_ms=200)
    assert repeated["action_effect"]["status"] == "already_in_target_state", repeated
    assert "warning" not in repeated, repeated


async def test_pure_scroll_is_reached_then_idempotent_at_the_bottom(
    executor: RpaToolExecutor,
) -> None:
    """静态长列表：滚动不长出新记录，指纹全程不变，但滚动位置确实变了。

    虚拟列表那条测的是「滚动能不能触发加载」；这条测的是「加载不了的时候能不能说清
    是滚到底了，还是选择器指错了容器」——两者的处置完全不同。
    """
    await _observe(executor, "interaction_effects.html")

    moved = await executor._interact_page(action="scroll", selector="#static-scroller", wait_ms=200)
    assert moved["status"] == "ok", moved
    assert moved["effect"]["changed"] is False, moved
    assert moved["action_effect"]["status"] == "target_reached", moved

    at_end = await executor._interact_page(action="scroll", selector="#static-scroller", wait_ms=200)
    assert at_end["action_effect"]["status"] == "already_in_target_state", at_end
    assert "换翻页方式" in at_end["action_effect"]["note"], at_end
    assert "warning" not in at_end, at_end  # 滚到底不是失败，不能报警告让模型去换目标


async def test_a_dead_button_and_an_inert_label_are_reported_differently(
    executor: RpaToolExecutor,
) -> None:
    """点没绑事件的按钮 = 只拿到了焦点；点不可聚焦的文本 = 什么证据都没有。

    两者都不能报成「状态已变化」：按钮会让整页指纹的 active 变，靠指纹判决时它正好会被判成
    面板已打开，模型接着照这个建流程。
    """
    await _observe(executor, "interaction_effects.html")

    dead = await executor._interact_page(action="click", selector="#dead-btn", wait_ms=200)
    assert dead["status"] == "ok", dead
    assert dead["action_effect"]["status"] == "focus_only", dead
    assert "warning" in dead and "business_check" not in dead, dead

    inert = await executor._interact_page(action="click", selector="#inert-label", wait_ms=200)
    assert inert["action_effect"]["status"] == "no_observable_change", inert
    assert "不等于失败" in inert["warning"], inert


@pytest.mark.parametrize(("name", "expected"), [
    ("same_class_date_regions.html", ["generic/date-input", "generic/date-input"]),
    ("anonymous_date_regions.html", ["generic/date-unresolved"]),
])
async def test_same_class_does_not_prove_date_range_ownership(executor, name, expected):
    result = await _observe(executor, name)
    controls = result["date_controls"]
    assert [control["type"] for control in controls] == expected
    assert all("end_input" not in control["interaction_recipe"] for control in controls)
    if expected == ["generic/date-unresolved"]:
        assert controls[0]["interaction_recipe"]["actionable"] is False
