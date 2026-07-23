from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ComponentSkill:
    """描述某个 UI 组件库控件的检测与交互方式。纯数据类，发现/匹配由 registry 负责，选择器解析由 build_skill_recipe 负责。"""

    library: str            # e.g. "el-ui", "ant-design", "arco"
    component: str          # e.g. "date-range-picker", "select-multiple"
    description: str        # 展示给模型的 interaction_recipe 说明

    fingerprints: list[str]  # 匹配规则：page_classes 中任一项与 fingerprints 任一项完全相等即命中

    # 用于在页面中定位 trigger 输入框的 placeholder/label 子串；命中后其真实选择器会替换 recipe_template["trigger"]
    trigger_hints: list[str]

    recipe_template: dict[str, str]  # slot_name -> CSS 选择器（可含 {day} 等占位符）

    interaction_steps: list[str]  # 展示给模型的有序步骤，引用 recipe_template 中的 slot 名

    # 除 trigger 外还需要按页面实际输入框解析的槽位：slot_name -> placeholder/label 子串。
    # 区间控件的结束日期输入框就靠它拿到真实选择器，否则模型只能照抄模板里的占位 placeholder。
    slot_hints: dict[str, list[str]] = field(default_factory=dict)

    # 主步骤不可用时的备选路线（如点日历格）。与 interaction_steps 分开，避免模型把两条路线混着用。
    fallback_steps: list[str] = field(default_factory=list)

    # 执行器/选择器引擎层面的限制，直接透传给模型，避免它重复踩同一个坑
    notes: list[str] = field(default_factory=list)

    priority: int = 0  # 多个 skill 命中同一页面时，priority 越高越先返回
