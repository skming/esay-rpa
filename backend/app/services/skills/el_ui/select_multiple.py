from .._base import ComponentSkill

skill = ComponentSkill(
    library="el-ui",
    component="select-multiple",
    description="Element UI Select（多选）— 点击 tags 区域展开 → 依次点击各选项",
    fingerprints=["el-select-dropdown__item"],
    trigger_hints=["项目进度", "状态", "类型", "select"],
    recipe_template={
        "trigger": ".el-select:has-text('{label}') .el-select__tags",
        "option":  ".el-select-dropdown__item:has-text('{value}')",
    },
    interaction_steps=[
        "click trigger  [展开下拉；{label} 替换为字段标签文本，delayMs: 1000]",
        "click option   [依次点击每个目标选项；{value} 替换为选项文本；多次点击不会关闭面板，delayMs: 500]",
        "press Escape on body  [关闭下拉，delayMs: 300]",
    ],
    priority=8,
)
