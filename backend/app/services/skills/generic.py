"""未命中任何已知组件库时的通用日期控件识别。

按 class 指纹匹配的技能包只能覆盖写过配方的库（el-ui / ant-design）。页面用的是
Arco / Vant / iView / LayUI / TDesign 或自研组件时，`date_controls` 直接缺席，模型
只能凭空猜 selector 和交互方式——这正是历次「日期筛选修不好」的起点。

这里改用与组件库无关的证据来识别：输入框的 placeholder / label / 已有值里的日期
特征。识别出来后给的是同一套「键入文本 + Enter + 回读硬门控」主路线，因为这条路线
本身不依赖任何框架类名；点日历格那条路线依赖弹层结构，未知框架下必须先把面板打开
再探一次页面才能构建，所以只作为需要额外取证的备选写进 fallback_steps。

识别的单位是「控件实例」不是「整页」：开始与结束日期必须来自同一个容器。没有配对证据就按
单日期交出，同一容器内出现多组候选则交出未解析信息，不挑第一个。
"""
from __future__ import annotations

import re
from typing import Any

# 日期文本的通用形态：2026-06-01 / 2026/6/1 / 2026年6月1日
_DATE_VALUE_RE = re.compile(r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}")
# placeholder/label 里的日期语义词，覆盖中英文
_DATE_WORDS = ("日期", "时间", "date", "time", "yyyy", "年月日")
_START_WORDS = ("开始", "起始", "start", "from", "自")
_END_WORDS = ("结束", "终止", "截止", "end", "to", "至")


def _blob(inp: dict[str, Any]) -> str:
    return " ".join(
        str(inp.get(key) or "") for key in ("placeholder", "label", "name", "id", "value")
    ).lower()


def _is_date_input(inp: dict[str, Any]) -> bool:
    if str(inp.get("type") or "").lower() in ("date", "datetime-local", "month"):
        return True
    blob = _blob(inp)
    if _DATE_VALUE_RE.search(blob):
        return True
    return any(word in blob for word in _DATE_WORDS)


def _has_word(inp: dict[str, Any], words: tuple[str, ...]) -> bool:
    blob = _blob(inp)
    return any(word in blob for word in words)


def _format_hint(inputs: list[dict[str, Any]]) -> str:
    for inp in inputs:
        match = _DATE_VALUE_RE.search(str(inp.get("value") or "") + " " + str(inp.get("placeholder") or ""))
        if match:
            return match.group(0)
    return "照抄输入框已有值或 placeholder 展示的格式"


def _group_by_container(date_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按结构证据把日期输入框分成控件实例：同一实例的框必须落在同一批容器里。

    实例键由两段组成，两段都相同才算同一个实例：
    - 共享层：祖先链上装着两个以上日期框的层的 uid。没有这一段，#search 里的开始日期会和
      #edit 里的结束日期配成一个区间——两次 fill 都成功、回读也对，查询条件只填了一半，
      页面照旧返回全量数据，没有任何一步报错。
    - 私有层：祖先链上只装着这一个日期框的层的身份（id 或类名集合）。组件常给每端各套一层
      自己的包装，两端包装的身份相同；而共享层里的 #search 与 #edit 身份不同。没有这一段，
      同一个外层容器里的两个独立区域会被并成一个控件。

    共同祖先一个都没有（只有 body 装得下这两个框）时按输入框各自成组：包装身份相同只说明
    两端长得像，证明不了它们属于同一个控件——同一套组件在查询区和导出区各用一次，两处的
    包装类名必然相同。这一段的判据是「有没有共同祖先」，不是「container 这个 selector 是不是
    空的」：探测端对无 id 无类名的祖先照样留 uid，只把 selector 记成 None，共同祖先存在而
    container 为空是常态。

    named_shared 交出「共同祖先有没有身份」：一个宽泛的无名 <form> 谁都装得下，与 body 一样
    不构成归属证据，调用方据此改交未解析结果而不是继续猜。
    """
    counts: dict[int, int] = {}
    for inp in date_inputs:
        for anc in inp.get("containers") or []:
            uid = anc.get("uid")
            if uid is not None:
                counts[uid] = counts.get(uid, 0) + 1

    groups: dict[Any, dict[str, Any]] = {}
    for index, inp in enumerate(date_inputs):
        shared: list[Any] = []
        private: list[str] = []
        container: str | None = None
        named_shared = False
        for anc in inp.get("containers") or []:
            if counts.get(anc.get("uid"), 0) >= 2:
                shared.append(anc.get("uid"))
                if anc.get("ident"):
                    named_shared = True
                if container is None and anc.get("selector"):
                    container = str(anc.get("selector"))
            elif anc.get("ident"):
                private.append(str(anc.get("ident")))
        key: Any = (tuple(shared), tuple(private)) if shared else ("no-shared-ancestor", index)
        group = groups.setdefault(
            key, {"container": container, "named_shared": named_shared, "inputs": []}
        )
        group["inputs"].append(inp)
    return list(groups.values())


def _build_control(
    trigger: dict[str, Any],
    end: dict[str, Any] | None,
    container: str | None,
    siblings: list[dict[str, Any]],
) -> dict[str, Any]:
    """给一个控件实例出配方。readonly 与格式提示只看这个实例自己的输入框。

    换成扫全页：另一个控件的 readonly 会把这条键入路线整条标成不可用，另一个控件的
    日期格式会让模型按 yyyy/MM/dd 填一个只认 yyyy-MM-dd 的框——两种都是回读校验反复
    失败、模型反复改 delayMs 的来源。
    """
    is_range = end is not None
    fmt = _format_hint([trigger, end] if end is not None else [trigger])

    recipe: dict[str, Any] = {"trigger": trigger.get("selector")}
    if container:
        recipe["container"] = container
    if is_range:
        recipe["end_input"] = end.get("selector")

    steps = [
        f"browser.fill trigger  [inputValue = {'开始' if is_range else '目标'}日期文本，格式照 {fmt}；fillMode: 'type'，delayMs: 500]",
    ]
    if is_range:
        steps.append("browser.fill end_input  [inputValue = 结束日期文本，格式同上，delayMs: 500]")
    steps += [
        f"browser.press Enter on {'end_input' if is_range else 'trigger'}  "
        "[提交并关闭弹层；Enter 必须打在日期输入框自身，打在 body 上不会冒泡到组件的按键处理，delayMs: 800]",
        "browser.extract trigger attribute=value → 变量  [回读实际写入值]",
    ]
    if is_range:
        steps.append("browser.extract end_input attribute=value → 变量")
    steps += [
        "script.python 校验  [回读值与目标不一致时 raise SystemExit；该段禁止 continueOnError]",
        "抓完数据后再加一个 script.python 断言：每行日期都必须落在目标范围内，越界即 raise SystemExit  "
        "[回读 value 只证明文本写进了输入框，证明不了组件已提交；抓回的数据才是筛选真的生效的证据。"
        "**只能断言、不能过滤**——删掉越界行会把页面筛选失效完全掩盖成绿灯]",
    ]
    recipe["steps"] = steps

    recipe["fallback_steps"] = [
        "**interact_page action='click' 点 trigger**  "
        "[这个组件库没有内置配方，弹层的面板/翻页按钮/日期格 class 只能从打开后的真实 DOM 里取。"
        "interact_page 点完会自动重新观察：面板打开看 action_effect.status=state_changed 或"
        "effect.diff 里的 layers；no_observable_change 是没有证据（先确认点的是不是 trigger 本身），"
        "不是「换目标」的信号，更不要加 delayMs；不带 url 的 inspect_page 也能再看当前状态，"
        "不会把弹层刷掉]",
        "从上一步 observation 的真实 class 构建：面板标题（含当前年月）、上/下月按钮、日期单元格三个选择器  "
        "[open_layers 已经把本次新出现的浮层单列出来了]",
        "流程节点里补回这一步：browser.click trigger  [打开日期弹层，delayMs: 1000]",
        "用 control.repeat_until 翻到目标月：循环体 = click 上/下月按钮 + extract 面板标题到变量，"
        "condition 写「面板标题变量 == 目标年月」  "
        "[次数由运行时的面板状态决定，不要写死；写死的次数只在生成当天成立]",
        "click 目标日期单元格 → 回读 + script.python 校验（同主路线最后两步）",
    ]

    recipe["notes"] = [
        "这份配方不是来自已知组件库的内置配方，而是根据输入框的日期特征推断的："
        "selector 是页面真实值可直接用，交互步骤属于通用做法，需用回读校验确认是否奏效。",
        "输入框里出现了日期文本 ≠ 组件已提交：多数日期组件要等 Enter 才把文本解析进自己的值。"
        "回读校验不通过就走 fallback_steps，不要靠加 delayMs 试。",
        "trigger 若是 readonly，键入路线可能整条不可用（组件不接受手输），直接走 fallback_steps。",
    ]
    if trigger.get("readonly"):
        recipe["readonly_trigger"] = True
    if siblings:
        others = ", ".join(str(inp.get("selector")) for inp in siblings)
        recipe["notes"].append(
            f"同容器里还有日期输入框：{others}。它们与本控件的关系没有结构证据，"
            "任务需要区间时先 interact_page 点开确认哪两个属于同一个控件，不要直接当成本控件的另一端。"
        )
    if not is_range and _has_word(trigger, _START_WORDS + _END_WORDS):
        recipe["notes"].append(
            "这个输入框的文案带「开始/结束」语义，但同容器里没有配对的另一端，只能按单日期交出。"
            "页面确实是区间就先 interact_page 找另一端，别把别的容器里的日期框当成它的 end_input。"
        )

    return {
        "type": "generic/date-range-input" if is_range else "generic/date-input",
        "library": "generic",
        "component": "date-range-input" if is_range else "date-input",
        "description": (
            "未匹配到已知组件库，按输入框的日期特征推断出的通用日期"
            + ("区间" if is_range else "")
            + "控件。selector 取自页面真实 DOM，交互步骤为通用做法。"
        ),
        "interaction_recipe": recipe,
    }


def _unresolved_control(
    members: list[dict[str, Any]],
    container: str | None,
    *,
    slot_reason: str = "同一容器内有多个开始或结束日期候选，无法判定哪两个属于同一个区间控件",
    note: str = "先 interact_page 点其中一个候选，观察哪些输入框属于同一个弹层/同一个控件，再重新 inspect_page 取配方。",
) -> dict[str, Any]:
    """归属判不出来时交出问题，不挑第一个。

    挑中的可能是被隐藏的历史控件或另一组区间的一端，流程照着跑不报错、填错字段、
    筛选结果全错。模型拿到未解析信息才会去 interact_page 看清楚再建流程。
    """
    recipe: dict[str, Any] = {
        "actionable": False,
        "candidates": [str(inp.get("selector")) for inp in members],
        "unresolved_slots": [f"trigger/end_input: {slot_reason}"],
        "required_action": "interact_page_then_inspect_page",
    }
    if container:
        recipe["container"] = container
    recipe["notes"] = [note]
    return {
        "type": "generic/date-unresolved",
        "library": "generic",
        "component": "date-unresolved",
        "description": "日期控件实例归属未能确定；候选见 candidates，需先交互取证。",
        "interaction_recipe": recipe,
    }


def build_generic_date_controls(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """认出页面上未被组件技能认领的日期控件实例，每个实例一份配方；认不出返回空列表。"""
    date_inputs = [inp for inp in inputs if inp.get("selector") and _is_date_input(inp)]
    controls: list[dict[str, Any]] = []
    for group in _group_by_container(date_inputs):
        members = group["inputs"]
        if len(members) > 1 and not group["named_shared"]:
            # 唯一的共同祖先没有 id 也没有类名：它跟 body 一样谁都装得下，配成区间和拆成
            # 两个单日期都拿不出证据。两种猜法各有一种不报错的错法（串槽 / 只填一半），
            # 所以这里交未解析，让模型点开看清楚。
            controls.append(_unresolved_control(
                members, group["container"],
                slot_reason=(
                    "这些日期框唯一的共同祖先没有 id 也没有类名，无法判断它们属于同一个区间控件"
                    "还是同一个宽泛容器里的多个独立区域"
                ),
                note=(
                    "先 interact_page 点其中一个候选：同一个控件的两端会一起进入同一个弹层。"
                    "确认归属后再重新 inspect_page 取配方，不要按类名相同直接配对。"
                ),
            ))
            continue
        controls.extend(_controls_for_group(members, group["container"]))
    return controls


def _controls_for_group(members: list[dict[str, Any]], container: str | None) -> list[dict[str, Any]]:
    starts = [inp for inp in members if _has_word(inp, _START_WORDS)]
    ends = [inp for inp in members if _has_word(inp, _END_WORDS)]

    if len(starts) > 1 or len(ends) > 1:
        return [_unresolved_control(members, container)]

    # 一个输入框同时带开始与结束语义（「开始至结束」写在同一个 placeholder 上）不是区间的两端，
    # 按单日期走：把它自己配成 trigger 和 end_input 会往同一个框里填两次。
    if len(starts) == 1 and len(ends) == 1 and starts[0] is not ends[0]:
        paired = [starts[0], ends[0]]
        rest = [inp for inp in members if inp not in paired]
        controls = [_build_control(starts[0], ends[0], container, rest)]
        controls += [_build_control(inp, None, container, [m for m in members if m is not inp]) for inp in rest]
        return controls

    return [_build_control(inp, None, container, [m for m in members if m is not inp]) for inp in members]
