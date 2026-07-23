from .._base import ComponentSkill

skill = ComponentSkill(
    library="ant-design",
    component="range-picker",
    description=(
        "Ant Design RangePicker（区间）— 首选「往两个输入框键入日期文本 → Enter 提交 → 回读校验」；"
        "点日历格是备选，且必须先把面板翻到目标月份"
    ),
    fingerprints=["ant-picker-range"],
    trigger_hints=["开始日期", "开始时间", "起始日期", "start date", "start"],
    slot_hints={"end_input": ["结束日期", "结束时间", "终止日期", "end date", "end"]},
    recipe_template={
        "trigger":      ".ant-picker-range .ant-picker-input:first-child input",
        "end_input":    ".ant-picker-range .ant-picker-input:last-child input",
        "panel":        ".ant-picker-panel-container",
        "panel_header": ".ant-picker-header-view",
        "prev_month":   ".ant-picker-header-prev-btn",
        "next_month":   ".ant-picker-header-next-btn",
        "prev_year":    ".ant-picker-header-super-prev-btn",
        "next_year":    ".ant-picker-header-super-next-btn",
        "day_cell":     "td.ant-picker-cell-in-view:not(.ant-picker-cell-disabled) .ant-picker-cell-inner:text-is('{day}')",
    },
    interaction_steps=[
        "browser.fill trigger    [inputValue = 开始日期文本，格式照抄输入框已有值/placeholder；fillMode: 'type'，delayMs: 500]",
        "browser.press Enter on trigger  [AntD 每个输入框各需一次回车确认；Enter 必须打在该输入框自身上，delayMs: 500]",
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
        "control.repeat_until 翻到开始日期所在年月：循环体 = click prev_month/next_month + extract panel_header 到变量，"
        "condition = 该变量 == 目标年月  [次数由运行时面板状态决定，写死次数只在生成当天成立]",
        "click day_cell  [{day} 替换为开始日的数字，delayMs: 500]",
        "click day_cell  [{day} 替换为结束日的数字；跨月时先翻页，delayMs: 500]",
        "回读 + 校验  [同主路线最后三步]",
    ],
    notes=[
        "只有 showTime 的面板才有 .ant-picker-ok 确定按钮，纯日期区间选完第二格即提交。",
        "AntD 面板同时渲染上下月的灰色单元格，务必用 .ant-picker-cell-in-view 排除。",
        "扩展执行器不支持 :text-is()，只支持 :has-text() 子串匹配（找 1 号会命中 1/11/21）；"
        "键盘输入在扩展执行器下也可能只写了 value 而没提交组件模型，必要时把流程切到 playwright 执行器。",
    ],
    priority=10,
)
