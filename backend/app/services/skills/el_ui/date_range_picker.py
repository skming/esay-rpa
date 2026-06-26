from .._base import ComponentSkill

skill = ComponentSkill(
    library="el-ui",
    component="date-range-picker",
    description="Element UI DateRangePicker — 四段式：触发输入框 → 开始日期单元格 → 结束日期单元格 → 确定",
    fingerprints=["el-date-range-picker", "el-date-editor--daterange"],
    trigger_hints=["开始日期", "开始时间", "起始日期", "start date", "start"],
    recipe_template={
        "trigger":    "input[placeholder='开始日期']",
        "start_cell": "td:not(.prev-month):not(.next-month) .el-date-table-cell:has-text('{day}')",
        "end_cell":   "td.available.today .el-date-table-cell",
        "confirm":    ".el-date-range-picker .el-picker-panel__footer button:has-text('确定')",
    },
    interaction_steps=[
        "click trigger              [打开日期面板，delayMs: 1000]",
        "click start_cell           [{day} 替换为开始日期数字，如 1；排除上月/下月单元格，delayMs: 500]",
        "click end_cell             [今天；若需指定结束日，改用 start_cell 模式并排除 prev/next-month，delayMs: 500]",
        "click confirm              [delayMs: 1000]",
    ],
    priority=10,
)
