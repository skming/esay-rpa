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
    """Return all skills whose fingerprints exactly match any class on the page.

    Skills are returned in priority order (highest first). Duplicate (library, component)
    pairs are de-duplicated — only the highest-priority variant is kept.
    """
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


def build_skill_recipe(skill: ComponentSkill, inputs: list[dict]) -> dict[str, object]:
    """Build an actionable recipe by resolving the trigger selector from real page inputs.

    Falls back to recipe_template["trigger"] when no input matches trigger_hints.
    """
    recipe: dict[str, object] = dict(skill.recipe_template)
    if skill.trigger_hints:
        for inp in inputs:
            combined = f"{inp.get('placeholder') or ''} {inp.get('label') or ''}".lower()
            if any(hint.lower() in combined for hint in skill.trigger_hints):
                recipe["trigger"] = inp["selector"]
                break
    recipe["steps"] = list(skill.interaction_steps)
    return recipe
