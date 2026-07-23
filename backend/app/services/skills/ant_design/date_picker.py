from .._base import ComponentSkill

skill = ComponentSkill(
    library="ant-design",
    component="date-picker",
    description=(
        "Ant Design DatePicker（单日期）— 首选「往输入框键入日期文本 → Enter 提交 → 回读校验」；"
        "点日历格是备选，且必须先把面板翻到目标月份"
    ),
    fingerprints=["ant-picker"],
    trigger_hints=["选择日期", "日期", "date", "时间"],
    recipe_template={
        "trigger":      ".ant-picker input",
        "panel":        ".ant-picker-panel-container",
        "panel_header": ".ant-picker-header-view",
        "prev_month":   ".ant-picker-header-prev-btn",
        "next_month":   ".ant-picker-header-next-btn",
        "prev_year":    ".ant-picker-header-super-prev-btn",
        "next_year":    ".ant-picker-header-super-next-btn",
        "day_cell":     "td.ant-picker-cell-in-view:not(.ant-picker-cell-disabled) .ant-picker-cell-inner:text-is('{day}')",
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
        "AntD 面板同时渲染上下月的灰色单元格，务必用 .ant-picker-cell-in-view 排除。",
        "扩展执行器不支持 :text-is()，只支持 :has-text() 子串匹配；"
        "键盘输入在扩展执行器下也可能只写了 value 而没提交组件模型，必要时把流程切到 playwright 执行器。",
    ],
    priority=5,
)
