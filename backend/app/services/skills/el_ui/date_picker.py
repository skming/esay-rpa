from .._base import ComponentSkill

skill = ComponentSkill(
    library="el-ui",
    component="date-picker",
    description=(
        "Element UI DatePicker（单日期）— 首选「往输入框键入日期文本 → Enter 提交 → 回读校验」；"
        "点日历格是备选，且必须先把面板翻到目标月份"
    ),
    fingerprints=["el-date-picker", "el-date-editor--date"],
    trigger_hints=["选择日期", "日期", "date", "时间"],
    recipe_template={
        "trigger":      "input[placeholder='选择日期']",
        "panel":        ".el-date-picker",
        "panel_header": ".el-date-picker__header-label",
        "prev_month":   ".el-date-picker .el-picker-panel__icon-btn.el-icon-arrow-left",
        "next_month":   ".el-date-picker .el-picker-panel__icon-btn.el-icon-arrow-right",
        "prev_year":    ".el-date-picker .el-picker-panel__icon-btn.el-icon-d-arrow-left",
        "next_year":    ".el-date-picker .el-picker-panel__icon-btn.el-icon-d-arrow-right",
        "day_cell":     "td.available:not(.prev-month):not(.next-month) span:text-is('{day}')",
    },
    interaction_steps=[
        "browser.fill trigger  [inputValue = 目标日期文本，格式照抄输入框已有值/placeholder；fillMode: 'type'，delayMs: 500]",
        "browser.press Enter on trigger  [提交并关闭面板；Enter 必须打在日期输入框上，打在 body 上不会冒泡到组件的按键处理，delayMs: 800]",
        "browser.extract trigger attribute=value → 变量  [回读实际写入值]",
        "script.python 校验  [回读值与目标不一致时 raise SystemExit；该段禁止 continueOnError]",
        "抓完数据后再加一个 script.python 断言：每行日期都必须落在目标范围内，越界即 raise SystemExit  [回读 value 只证明文本写进了输入框，证明不了组件已提交。**只能断言、不能过滤**]",
    ],
    fallback_steps=[
        "click trigger  [打开日期面板，delayMs: 1000]",
        "读 panel_header 文本拿到面板当前年月  [面板默认停在「今天」所在月]",
        "control.repeat_until 翻到目标年月：循环体 = click prev_month/next_month + extract panel_header 到变量，"
        "condition = 该变量 == 目标年月  [次数由运行时面板状态决定，不能写死]",
        "click day_cell  [{day} 替换为目标日的数字，delayMs: 500]",
        "回读 + 校验  [同主路线最后两步]",
    ],
    notes=[
        "带时间的 datetime 面板才有「确定」按钮，纯 date 面板点格即提交，不要凭空构建 confirm 节点。",
        "输入框里出现了日期文本 ≠ 组件已提交：组件通常要等 Enter 才把文本解析进自己的值，"
        "而 Enter 必须打在该输入框上。回读校验不通过就改走 fallback_steps，不要靠加 delayMs 试。",
        ":text-is() 只有 playwright 执行器支持，且必须落在有直接文本节点的元素（span 可以，td 不行）；"
        "扩展执行器只支持 :has-text() 子串匹配，找 1 号会同时命中 1/11/21。",
    ],
    priority=5,
)
