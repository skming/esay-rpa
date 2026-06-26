from .._base import ComponentSkill

skill = ComponentSkill(
    library="ant-design",
    component="range-picker",
    description="Ant Design RangePicker — 四段式：触发输入框 → 开始日期单元格 → 结束日期单元格 → 确定",
    fingerprints=["ant-picker-range"],
    trigger_hints=["开始日期", "开始时间", "起始日期", "start date", "start"],
    recipe_template={
        "trigger":    ".ant-picker-range .ant-picker-input:first-child input",
        "start_cell": "td.ant-picker-cell:not(.ant-picker-cell-disabled):not(.ant-picker-cell-prev-hover):not(.ant-picker-cell-next-hover) .ant-picker-cell-inner:has-text('{day}')",
        "end_cell":   "td.ant-picker-cell-today .ant-picker-cell-inner",
        "confirm":    ".ant-picker-footer .ant-picker-ok button",
    },
    interaction_steps=[
        "click trigger              [打开日期面板，delayMs: 1000]",
        "click start_cell           [{day} 替换为开始日期数字；排除 prev/next-hover 单元格，delayMs: 500]",
        "click end_cell             [今天；若需指定结束日，用 start_cell 模式并过滤 prev/next-hover，delayMs: 500]",
        "click confirm              [delayMs: 1000]",
    ],
    priority=10,
)
