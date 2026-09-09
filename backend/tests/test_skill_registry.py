"""组件库技能包的槽位解析：解析错的 selector 比解析不出更危险。

流程照着错的 selector 跑，点开的是另一个控件、填进的是另一个字段，而运行结果照样是绿的。
所以这里的断言全部围绕「宁可交出问题也不猜」：跨容器不许串、同容器歧义不许挑第一个、
模板占位选择器不许混进已观测结果。
"""

from __future__ import annotations

from app.services.ai_tools.page_observation import build_date_controls
from app.services.skills.registry import build_skill_recipe, discover_component_instances, match_skills


def _antd_range(uid: int, container: str, *, start_ph: str = "开始日期", end_ph: str = "结束日期") -> list[dict]:
    """一个 AntD RangePicker 实例：两个输入框共享同一个带 ant-picker-range 的容器祖先。"""
    ancestors = [{"uid": uid, "classes": ["ant-picker", "ant-picker-range"], "selector": container}]
    return [
        {"selector": f"{container} input:nth-of-type(1)", "placeholder": start_ph, "ancestors": ancestors},
        {"selector": f"{container} input:nth-of-type(2)", "placeholder": end_ph, "ancestors": ancestors},
    ]


PAGE_CLASSES = ["ant-picker", "ant-picker-range", "ant-picker-input"]


def test_two_range_pickers_resolve_slots_inside_their_own_container() -> None:
    """同页两个同款区间控件（查询区一套、编辑弹窗一套）必须各自解析。

    整页解析会把两个实例的 trigger 都指到页面上第一个「开始日期」输入框：编辑弹窗里的流程
    去改了查询条件，弹窗本身的值一直没变，页面不报错，数据却全是错的。
    """
    inputs = _antd_range(1, "#search-range") + _antd_range(2, "#edit-range")
    instances = discover_component_instances(inputs, PAGE_CLASSES)

    assert [i["type"] for i in instances] == ["ant-design/range-picker"] * 2
    assert all(i["match_scope"] == "container" for i in instances)

    for instance, container in zip(instances, ["#search-range", "#edit-range"]):
        recipe = instance["interaction_recipe"]
        assert recipe["container"] == container
        assert recipe["actionable"] is True
        assert recipe["trigger"] == f"{container} input:nth-of-type(1)"
        assert recipe["end_input"] == f"{container} input:nth-of-type(2)"


def test_ambiguous_slot_inside_one_container_is_reported_not_guessed() -> None:
    """同一容器里两个输入框都命中「开始日期」时不许挑第一个。

    挑中的那个可能是被隐藏的历史控件；交出问题，模型才会去 interact_page 看清楚。
    """
    ancestors = [{"uid": 7, "classes": ["ant-picker-range"], "selector": "#dup"}]
    inputs = [
        {"selector": "#dup input.a", "placeholder": "开始日期", "ancestors": ancestors},
        {"selector": "#dup input.b", "placeholder": "开始日期", "ancestors": ancestors},
    ]

    recipe = discover_component_instances(inputs, PAGE_CLASSES)[0]["interaction_recipe"]

    assert recipe["actionable"] is False
    assert "trigger" not in recipe
    assert any("trigger" in problem for problem in recipe["unresolved_slots"])
    assert any("end_input" in problem for problem in recipe["unresolved_slots"])
    assert recipe["required_action"] == "interact_page_then_inspect_page"


def test_unrelated_date_input_keeps_its_generic_recipe() -> None:
    """页面上有 AntD 区间控件，不代表另一个原生日期输入框也归它管。

    按「页面有没有日期组件」整体压制通用配方，那个原生输入框就一份配方都拿不到，
    模型只能凭空猜它怎么交互——而它其实是最好办的那一种。
    """
    native = {
        "selector": "#effective-date",
        "type": "date",
        "name": "生效日期",
        "placeholder": None,
        "ancestors": [{"uid": 9, "classes": ["form-item"], "selector": "#form-row-3"}],
    }
    controls = build_date_controls(_antd_range(1, "#search-range") + [native], PAGE_CLASSES)

    antd = [c for c in controls if c.get("type") == "ant-design/range-picker"]
    generic = [c for c in controls if c.get("match_scope") == "unclaimed_inputs"]
    assert len(antd) == 1
    assert len(generic) == 1
    # 认领过的输入框再多给一份通用键入路线，模型会在两套步骤之间挑，挑错就绕开了组件的提交时机
    assert generic[0]["type"] == "generic/date-input"
    assert generic[0]["interaction_recipe"]["trigger"] == "#effective-date"
    assert "claimed_selectors" not in antd[0]


def test_range_picker_container_does_not_also_light_up_the_single_date_skill() -> None:
    """RangePicker 根节点同时带 ant-picker 与 ant-picker-range 两个类名。

    整页指纹会把单日期 skill 也点亮，模型拿到两份步骤数不同的配方（单日期只填一个框），
    照单日期那份做就只填了开始日期，筛选范围少一半而且不报错。
    """
    assert {s.component for s in match_skills(PAGE_CLASSES)} == {"range-picker", "date-picker"}

    instances = discover_component_instances(_antd_range(1, "#search-range"), PAGE_CLASSES)
    assert [i["type"] for i in instances] == ["ant-design/range-picker"]


def test_template_panel_selectors_are_never_presented_as_observed() -> None:
    """面板、日期格只有点开控件后才存在，模板值必须单列在 panel_selectors_unverified 里。

    混在真实 selector 里交出去，模型无法分辨哪个来自 DOM、哪个来自模板，
    会把 .ant-picker-panel-container 直接写进流程节点——在别的站点上零命中。
    """
    skill = next(s for s in match_skills(PAGE_CLASSES) if s.component == "range-picker")
    recipe = build_skill_recipe(skill, _antd_range(1, "#search-range"), container_selector="#search-range")

    panel = recipe["panel_selectors_unverified"]
    assert set(panel) >= {"panel", "panel_header", "prev_month", "next_month", "day_cell"}
    observed = {k: v for k, v in recipe.items() if isinstance(v, str) and k != "container"}
    assert set(observed) == {"trigger", "end_input"}
    assert all(value.startswith("#search-range ") for value in observed.values())


def _in(uid: int, selector: str) -> list[dict]:
    """一层容器证据：探测端给每个输入框记从内到外的祖先 uid、身份和 selector。"""
    return [{"uid": uid, "ident": selector, "selector": selector}]


def test_generic_dates_in_two_containers_are_not_paired_into_one_range() -> None:
    """查询区只有开始日期、编辑区只有结束日期时，它们不是一个区间控件。

    配成一对后结束日期会填进编辑区那个框：两次 fill 都成功、回读也对，查询条件却只填了一半，
    页面照旧返回全量数据，没有任何一步报错。
    """
    controls = build_date_controls(
        [
            {"selector": "#search-start", "placeholder": "开始日期", "containers": _in(1, "#search")},
            {"selector": "#edit-end", "placeholder": "结束日期", "containers": _in(2, "#edit")},
        ],
        [],
    )

    assert [c["type"] for c in controls] == ["generic/date-input", "generic/date-input"]
    assert [c["interaction_recipe"]["trigger"] for c in controls] == ["#search-start", "#edit-end"]
    assert all("end_input" not in c["interaction_recipe"] for c in controls)
    # 没配上对不能沉默：文案带「开始」却找不到另一端，要让模型知道去哪补证据
    assert any("没有配对的另一端" in note for note in controls[0]["interaction_recipe"]["notes"])


def test_each_container_with_a_real_pair_gets_its_own_generic_range() -> None:
    """同页两套未知日期区间（查询区一套、编辑区一套）必须各出一份配方，且不互相取值。"""
    inputs = []
    for uid, prefix in ((1, "#search"), (2, "#edit")):
        inputs += [
            {"selector": f"{prefix}-start", "placeholder": "开始日期", "containers": _in(uid, prefix)},
            {"selector": f"{prefix}-end", "placeholder": "结束日期", "containers": _in(uid, prefix)},
        ]

    controls = build_date_controls(inputs, [])

    assert [c["type"] for c in controls] == ["generic/date-range-input"] * 2
    pairs = [(c["interaction_recipe"]["trigger"], c["interaction_recipe"]["end_input"]) for c in controls]
    assert pairs == [("#search-start", "#search-end"), ("#edit-start", "#edit-end")]
    assert [c["interaction_recipe"]["container"] for c in controls] == ["#search", "#edit"]


def test_per_input_component_wrappers_still_pair_inside_the_shared_container() -> None:
    """未知组件库常给每个输入框套一层自己的包装。按最近一层分组会把真区间拆成两个单日期。

    拆开之后模型只填开始日期，筛选范围少一半——和跨容器串槽一样不报错。
    """
    inputs = [
        {"selector": "#r-start", "placeholder": "开始日期",
         "containers": [{"uid": 11, "ident": ".x-field", "selector": ".x-field"},
                        {"uid": 1, "ident": "#range-box", "selector": "#range-box"}]},
        {"selector": "#r-end", "placeholder": "结束日期",
         "containers": [{"uid": 12, "ident": ".x-field", "selector": ".x-field"},
                        {"uid": 1, "ident": "#range-box", "selector": "#range-box"}]},
    ]

    control = build_date_controls(inputs, [])[0]

    assert control["type"] == "generic/date-range-input"
    assert control["interaction_recipe"]["container"] == "#range-box"
    assert control["interaction_recipe"]["end_input"] == "#r-end"


def test_same_wrapper_identity_alone_does_not_pair_across_two_regions() -> None:
    """两端各在自己的包装里、页面上没有任何层把它们装在一起：包装身份相同不构成配对证据。

    同一套组件在查询区和导出区各用一次，两处的包装类名必然相同。按类名相同配对，结束日期
    会填进另一个区域的框：两次 fill 都成功、回读也对，查询条件只填了一半，页面照旧返回全量
    数据，没有任何一步报错。真区间的证据是共同祖先，由 #range-box 那条用例守住。
    """
    controls = build_date_controls(
        [
            {"selector": "#left-start", "placeholder": "开始时间",
             "containers": [{"uid": 21, "ident": ".cell", "selector": ".cell"}]},
            {"selector": "#right-end", "placeholder": "结束时间",
             "containers": [{"uid": 22, "ident": ".cell", "selector": ".cell"}]},
        ],
        [],
    )

    assert [c["type"] for c in controls] == ["generic/date-input", "generic/date-input"]
    assert [c["interaction_recipe"]["trigger"] for c in controls] == ["#left-start", "#right-end"]
    assert all("end_input" not in c["interaction_recipe"] for c in controls)
    # 拆开不能沉默：文案带「开始/结束」却没配上对，要让模型知道去哪补证据
    assert any("没有配对的另一端" in note for note in controls[0]["interaction_recipe"]["notes"])


def test_an_anonymous_shared_ancestor_is_reported_unresolved_not_paired() -> None:
    """唯一的共同祖先没有 id 也没有类名（一个宽泛的 <form>）：配对与拆开都拿不出证据。

    探测端对无名祖先照样留 uid，只把 selector 记成 None。所以判据必须看有没有共同祖先本身，
    看 container 是不是空的会把这一类页面当成「压根没有共同祖先」而直接拆开。
    """
    anonymous = [{"uid": 5, "ident": None, "selector": None}]
    controls = build_date_controls(
        [
            {"selector": "#f-start", "placeholder": "开始日期", "containers": anonymous},
            {"selector": "#f-end", "placeholder": "结束日期", "containers": anonymous},
        ],
        [],
    )

    assert [c["type"] for c in controls] == ["generic/date-unresolved"]
    recipe = controls[0]["interaction_recipe"]
    assert recipe["actionable"] is False
    assert "trigger" not in recipe and "end_input" not in recipe and "container" not in recipe
    assert recipe["candidates"] == ["#f-start", "#f-end"]
    assert recipe["required_action"] == "interact_page_then_inspect_page"
    assert any("没有 id 也没有类名" in slot for slot in recipe["unresolved_slots"])


def test_a_named_outer_ancestor_above_an_anonymous_one_is_enough_to_pair() -> None:
    """无名层之上还有一层带身份的共同祖先：归属证据在，容器要报那一层带身份的。

    只看最内层共享祖先的 selector 会把 container 报成 None，模型就分不清这份配方属于哪个区域。
    """
    chain = [
        {"uid": 6, "ident": None, "selector": None},
        {"uid": 1, "ident": "#range-box", "selector": "#range-box"},
    ]
    controls = build_date_controls(
        [
            {"selector": "#g-start", "placeholder": "开始日期", "containers": list(chain)},
            {"selector": "#g-end", "placeholder": "结束日期", "containers": list(chain)},
        ],
        [],
    )

    assert [c["type"] for c in controls] == ["generic/date-range-input"]
    assert controls[0]["interaction_recipe"]["container"] == "#range-box"
    assert controls[0]["interaction_recipe"]["end_input"] == "#g-end"


def test_two_regions_inside_one_shared_container_are_still_told_apart() -> None:
    """外层容器装着查询区和编辑区两个区域，各自只有一个日期框：私有层身份不同即两个单日期。"""
    controls = build_date_controls(
        [
            {"selector": "#search-start", "placeholder": "开始日期",
             "containers": [{"uid": 11, "ident": "#search", "selector": "#search"},
                            {"uid": 1, "ident": "#page", "selector": "#page"}]},
            {"selector": "#edit-end", "placeholder": "结束日期",
             "containers": [{"uid": 12, "ident": "#edit", "selector": "#edit"},
                            {"uid": 1, "ident": "#page", "selector": "#page"}]},
        ],
        [],
    )

    assert [c["type"] for c in controls] == ["generic/date-input", "generic/date-input"]
    assert all("end_input" not in c["interaction_recipe"] for c in controls)


def test_two_pairs_inside_one_container_are_reported_unresolved() -> None:
    """同一个容器里有两组开始/结束时，没有任何结构证据能说明哪两个是一对。"""
    shared = _in(1, "#one-form")
    inputs = [
        {"selector": "#s1", "placeholder": "开始日期", "containers": shared},
        {"selector": "#e1", "placeholder": "结束日期", "containers": shared},
        {"selector": "#s2", "placeholder": "开始日期", "containers": shared},
        {"selector": "#e2", "placeholder": "结束日期", "containers": shared},
    ]

    controls = build_date_controls(inputs, [])

    assert [c["type"] for c in controls] == ["generic/date-unresolved"]
    recipe = controls[0]["interaction_recipe"]
    assert recipe["actionable"] is False
    assert "trigger" not in recipe and "end_input" not in recipe
    assert recipe["candidates"] == ["#s1", "#e1", "#s2", "#e2"]
    assert recipe["required_action"] == "interact_page_then_inspect_page"


def test_readonly_and_format_hint_come_from_the_selected_control_only() -> None:
    """另一个控件的 readonly 与日期格式不能污染本控件的配方。

    被污染的后果分别是：可用的键入路线被整条标成不可用；按 2026/06/01 去填一个只认
    2026-06-01 的输入框，回读校验反复失败，模型转而去加 delayMs。
    """
    inputs = [
        {"selector": "#a-start", "placeholder": "开始日期", "value": "2026-06-01", "containers": _in(1, "#a")},
        {"selector": "#a-end", "placeholder": "结束日期", "value": "2026-06-30", "containers": _in(1, "#a")},
        {"selector": "#b-day", "placeholder": "日期", "value": "2026/06/01", "readonly": True,
         "containers": _in(2, "#b")},
    ]

    controls = build_date_controls(inputs, [])
    ranged = next(c for c in controls if c["type"] == "generic/date-range-input")
    single = next(c for c in controls if c["type"] == "generic/date-input")

    assert "readonly_trigger" not in ranged["interaction_recipe"]
    assert single["interaction_recipe"]["readonly_trigger"] is True
    assert any("2026-06-01" in step for step in ranged["interaction_recipe"]["steps"])
    assert not any("2026/06/01" in step for step in ranged["interaction_recipe"]["steps"])


def test_unrelated_single_date_in_the_same_container_stays_single() -> None:
    """筛选栏里还放着一个「生日」日期框时，它既不能当区间的一端，也不能没有配方。

    被当成 end_input 就把生日填成了筛选条件；整组判成歧义则连能用的区间也没了。
    """
    shared = _in(1, ".cond")
    controls = build_date_controls(
        [
            {"selector": "#from-time", "placeholder": "起始时间", "containers": shared},
            {"selector": "#to-time", "placeholder": "截止时间", "containers": shared},
            {"selector": "#birthday", "type": "date", "label": "生日", "containers": shared},
        ],
        [],
    )

    assert [c["type"] for c in controls] == ["generic/date-range-input", "generic/date-input"]
    assert controls[0]["interaction_recipe"]["end_input"] == "#to-time"
    assert controls[1]["interaction_recipe"]["trigger"] == "#birthday"
    # 同容器里还有别的日期框这件事要写进 notes：模型要区间时才知道去哪取证
    assert any("#birthday" in note for note in controls[0]["interaction_recipe"]["notes"])
