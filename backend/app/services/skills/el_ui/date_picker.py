from .._base import ComponentSkill

skill = ComponentSkill(
    library="el-ui",
    component="date-picker",
    description="Element UI DatePicker（单日期）— 触发输入框 → 日期单元格（→ 可选确定）",
    fingerprints=["el-date-picker", "el-date-editor--date"],
    trigger_hints=["选择日期", "日期", "date", "时间"],
    recipe_template={
        "trigger":   "input[placeholder='选择日期']",
        "date_cell": "td:not(.prev-month):not(.next-month) .el-date-table-cell:has-text('{day}')",
        "confirm":   ".el-date-picker .el-picker-panel__footer button:has-text('确定')",
    },
    interaction_steps=[
        "click trigger    [打开日期面板，delayMs: 1000]",
        "click date_cell  [{day} 替换为目标日数字，delayMs: 500]",
        "click confirm    [无确定按钮时跳过，delayMs: 500]",
    ],
    priority=5,
)
