from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from ._base import ComponentSkill

_SKILLS: list[ComponentSkill] = []


def _discover() -> None:
    here = Path(__file__).parent
    for subdir in sorted(here.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith("_"):
            continue
        for _, mod_name, _ in pkgutil.iter_modules([str(subdir)]):
            mod = importlib.import_module(f".{subdir.name}.{mod_name}", package=__package__)
            skill = getattr(mod, "skill", None)
            if isinstance(skill, ComponentSkill):
                _SKILLS.append(skill)
    _SKILLS.sort(key=lambda s: s.priority, reverse=True)


_discover()


def match_skills(page_classes: list[str]) -> list[ComponentSkill]:
    """按 priority 降序返回匹配的 skill；同一 (library, component) 只保留优先级最高的一个。"""
    class_set = set(page_classes)
    seen: set[tuple[str, str]] = set()
    result: list[ComponentSkill] = []
    for skill in _SKILLS:
        if any(fp in class_set for fp in skill.fingerprints):
            key = (skill.library, skill.component)
            if key not in seen:
                seen.add(key)
                result.append(skill)
    return result


def _resolve_slot(inputs: list[dict], hints: list[str]) -> str | None:
    for inp in inputs:
        combined = f"{inp.get('placeholder') or ''} {inp.get('label') or ''}".lower()
        if any(hint.lower() in combined for hint in hints):
            return inp.get("selector")
    return None


def build_skill_recipe(skill: ComponentSkill, inputs: list[dict]) -> dict[str, object]:
    """结合真实页面输入框解析各输入槽位的选择器；无匹配时保留 recipe_template 里的占位选择器。"""
    recipe: dict[str, object] = dict(skill.recipe_template)
    slot_hints = {"trigger": skill.trigger_hints, **skill.slot_hints}
    for slot, hints in slot_hints.items():
        if not hints:
            continue
        resolved = _resolve_slot(inputs, hints)
        if resolved:
            recipe[slot] = resolved
    recipe["steps"] = list(skill.interaction_steps)
    if skill.fallback_steps:
        recipe["fallback_steps"] = list(skill.fallback_steps)
    if skill.notes:
        recipe["notes"] = list(skill.notes)
    return recipe
