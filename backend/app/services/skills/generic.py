"""未命中任何已知组件库时的通用日期控件识别。

按 class 指纹匹配的技能包只能覆盖写过配方的库（el-ui / ant-design）。页面用的是
Arco / Vant / iView / LayUI / TDesign 或自研组件时，`date_controls` 直接缺席，模型
只能凭空猜 selector 和交互方式——这正是历次「日期筛选修不好」的起点。

这里改用与组件库无关的证据来识别：输入框的 placeholder / label / 已有值里的日期
特征。识别出来后给的是同一套「键入文本 + Enter + 回读硬门控」主路线，因为这条路线
本身不依赖任何框架类名；点日历格那条路线依赖弹层结构，未知框架下必须先把面板打开
再探一次页面才能构建，所以只作为需要额外取证的备选写进 fallback_steps。
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


def _pick(inputs: list[dict[str, Any]], words: tuple[str, ...]) -> dict[str, Any] | None:
    for inp in inputs:
        if any(word in _blob(inp) for word in words):
            return inp
    return None


def _format_hint(inputs: list[dict[str, Any]]) -> str:
    for inp in inputs:
        match = _DATE_VALUE_RE.search(str(inp.get("value") or "") + " " + str(inp.get("placeholder") or ""))
        if match:
            return match.group(0)
    return "照抄输入框已有值或 placeholder 展示的格式"


def build_generic_date_recipe(inputs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从 inputs 里认出日期输入框并给出与框架无关的交互配方；认不出返回 None。"""
    date_inputs = [inp for inp in inputs if inp.get("selector") and _is_date_input(inp)]
    if not date_inputs:
        return None

    start = _pick(date_inputs, _START_WORDS)
    end = _pick(date_inputs, _END_WORDS)
    is_range = bool(start and end and start is not end)
    trigger = start or date_inputs[0]
    fmt = _format_hint(date_inputs)

    recipe: dict[str, Any] = {"trigger": trigger.get("selector")}
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
        "click trigger  [打开日期弹层，delayMs: 1000]",
        "**再调一次 inspect_page（不要带 url，直接探当前页）**  "
        "[这个组件库没有内置配方，弹层的面板/翻页按钮/日期格 class 只能从打开后的真实 DOM 里取；"
        "此时 page_layout 与 page_classes 才包含弹层结构]",
        "从上一步的真实 class 构建：面板标题（含当前年月）、上/下月按钮、日期单元格三个选择器",
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
    if any(inp.get("readonly") for inp in date_inputs):
        recipe["readonly_trigger"] = True

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
