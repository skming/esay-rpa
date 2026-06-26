from .._base import ComponentSkill

skill = ComponentSkill(
    library="ant-design",
    component="date-picker",
    description="Ant Design DatePicker（单日期）— 触发输入框 → 日期单元格",
    fingerprints=["ant-picker"],
    trigger_hints=["选择日期", "日期", "date", "时间"],
    recipe_template={
        "trigger":   ".ant-picker input",
        "date_cell": "td.ant-picker-cell:not(.ant-picker-cell-disabled) .ant-picker-cell-inner:has-text('{day}')",
    },
    interaction_steps=[
        "click trigger    [打开日期面板，delayMs: 1000]",
        "click date_cell  [{day} 替换为目标日数字，delayMs: 500]",
    ],
    priority=5,
)
