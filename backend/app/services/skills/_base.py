from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ComponentSkill:
    """Describes how to detect and interact with a specific UI component library widget.

    A skill is pure data — no logic. The registry handles discovery and matching;
    build_skill_recipe handles selector resolution.
    """

    library: str            # e.g. "el-ui", "ant-design", "arco"
    component: str          # e.g. "date-range-picker", "select-multiple"
    description: str        # shown to the model in interaction_recipe

    # Detection: a skill matches when ANY page_classes entry exactly equals ANY fingerprint.
    fingerprints: list[str]

    # Resolution: placeholder or label substrings that identify the trigger input on the page.
    # When a matching input is found its actual selector replaces recipe_template["trigger"].
    trigger_hints: list[str]

    # Interaction template: slot_name -> CSS selector (may contain {day} etc.)
    recipe_template: dict[str, str]

    # Ordered steps shown to the model; reference slot names from recipe_template.
    interaction_steps: list[str]

    # Higher priority = returned first when multiple skills match the same page.
    priority: int = 0
