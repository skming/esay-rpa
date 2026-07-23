from .._base import ComponentSkill

skill = ComponentSkill(
    library="el-ui",
    component="date-range-picker",
    description=(
        "Element UI DateRangePicker（区间）— 首选「往两个输入框键入日期文本 → Enter 提交 → 回读校验」；"
        "点日历格是备选，且必须先把面板翻到目标月份"
    ),
    fingerprints=["el-date-range-picker", "el-date-editor--daterange"],
    trigger_hints=["开始日期", "开始时间", "起始日期", "start date", "start"],
    slot_hints={"end_input": ["结束日期", "结束时间", "终止日期", "end date", "end"]},
    recipe_template={
        "trigger":     "input[placeholder='开始日期']",
        "end_input":   "input[placeholder='结束日期']",
        "panel":       ".el-date-range-picker",
        "panel_header": ".el-date-range-picker__header",
        "prev_month":  ".el-date-range-picker .el-picker-panel__icon-btn.el-icon-arrow-left",
        "next_month":  ".el-date-range-picker .el-picker-panel__icon-btn.el-icon-arrow-right",
        "prev_year":   ".el-date-range-picker .el-picker-panel__icon-btn.el-icon-d-arrow-left",
        "next_year":   ".el-date-range-picker .el-picker-panel__icon-btn.el-icon-d-arrow-right",
        "day_cell":    "td.available:not(.prev-month):not(.next-month) span:text-is('{day}')",
    },
    interaction_steps=[
        "browser.fill trigger    [inputValue = 开始日期文本，格式照抄输入框已有值/placeholder；fillMode: 'type'，delayMs: 500]",
        "browser.fill end_input  [inputValue = 结束日期文本，同上格式，delayMs: 500]",
        "browser.press Enter on end_input  [提交区间并关闭面板；Enter 必须打在日期输入框上，打在 body 上不会冒泡到组件的按键处理，delayMs: 800]",
        "browser.extract trigger attribute=value → 变量  [回读实际写入值]",
        "browser.extract end_input attribute=value → 变量",
        "script.python 校验  [回读值与目标不一致时 raise SystemExit；该段禁止 continueOnError]",
        "抓完数据后再加一个 script.python 断言：每行日期都必须落在目标范围内，越界即 raise SystemExit  [回读 value 只证明文本写进了输入框，证明不了组件已提交；抓回的数据才是筛选真的生效的证据。**只能断言、不能过滤**——删掉越界行会把页面筛选失效完全掩盖成绿灯]",
    ],
    fallback_steps=[
        "click trigger  [打开日期面板，delayMs: 1000]",
        "读 panel_header 文本拿到左面板当前年月  [面板默认停在「今天」所在月，与目标月份的差值随运行日期变化]",
        "control.repeat_until 把左面板翻到开始日期所在年月：循环体 = click prev_month/next_month + extract panel_header 到变量，"
        "condition = 该变量 == 目标年月  [次数由运行时面板状态决定，写死次数只在生成当天成立]",
        "click day_cell  [{day} 替换为开始日的数字，delayMs: 500]",
        "click day_cell  [{day} 替换为结束日的数字；跨月时先按上一步把面板翻过去，delayMs: 500]",
        "回读 + 校验  [同主路线最后三步]",
    ],
    notes=[
        "daterange 面板没有「确定」按钮，选完第二格即提交；不要构建 confirm 节点。",
        "输入框里出现了日期文本 ≠ 组件已提交：组件通常要等 Enter 才把文本解析进自己的值，"
        "而 Enter 必须打在该输入框上。回读校验不通过就改走 fallback_steps，不要靠加 delayMs 试。",
        ":text-is() 只有 playwright 执行器支持，且必须落在有直接文本节点的元素（span 可以，td 不行）；"
        "扩展执行器只支持 :has-text()，它是子串匹配，找 1 号会同时命中 1/11/21，必要时用 nth 或改走主路线。",
    ],
    priority=10,
)
