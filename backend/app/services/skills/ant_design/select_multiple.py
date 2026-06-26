from .._base import ComponentSkill

skill = ComponentSkill(
    library="ant-design",
    component="select-multiple",
    description="Ant Design Select（多选）— 点击选择框展开 → 依次点击各选项",
    fingerprints=["ant-select-multiple"],
    trigger_hints=["状态", "类型", "项目进度", "select"],
    recipe_template={
        "trigger": ".ant-select-multiple .ant-select-selector",
        "option":  ".ant-select-dropdown .ant-select-item-option:has-text('{value}')",
    },
    interaction_steps=[
        "click trigger  [展开下拉，delayMs: 1000]",
        "click option   [依次点击每个目标选项；{value} 替换为选项文本；多次点击不会关闭面板，delayMs: 500]",
        "press Escape on body  [关闭下拉，delayMs: 300]",
    ],
    priority=8,
)
