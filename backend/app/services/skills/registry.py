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
    """按 priority 降序返回匹配的 skill；同一 (library, component) 只保留优先级最高的一个。

    整页类名指纹只能回答「页面上存在这种控件」。页面上有两个同库控件时它分不出哪个槽位属于哪个，
    定位要用 discover_component_instances。
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


def _blob(inp: dict) -> str:
    parts = [inp.get("placeholder"), inp.get("label"), inp.get("name_hint"), inp.get("aria_label")]
    return " ".join(str(p) for p in parts if p).lower()


def _observable_slots(skill: ComponentSkill) -> dict[str, list[str]]:
    """能在 DOM 里直接看到的槽位。其余模板槽位（面板、日期格）只有点开控件后才存在，不能当已观测。"""
    return {"trigger": list(skill.trigger_hints), **{k: list(v) for k, v in skill.slot_hints.items()}}


def _resolve_slots(skill: ComponentSkill, scoped: list[dict]) -> tuple[dict[str, str], list[str]]:
    """在给定输入框集合内解析各观测槽位，返回 (已解析槽位, 未解决问题)。

    宁可交出问题也不猜：解析错的 selector 会让流程点开另一个控件、填进另一个字段，
    而运行结果照样是绿的——错的 selector 比没有 selector 难查得多。
    """
    slots = _observable_slots(skill)
    resolved: dict[str, str] = {}
    problems: list[str] = []
    single_slot = len(slots) == 1

    for slot, hints in slots.items():
        hit = [inp for inp in scoped if inp.get("selector") and any(h.lower() in _blob(inp) for h in hints)]
        if len(hit) == 1:
            resolved[slot] = str(hit[0]["selector"])
            continue
        if len(hit) > 1:
            problems.append(f"{slot}: 作用域内有 {len(hit)} 个输入框同时命中提示词，无法确定是哪一个")
            continue
        usable = [inp for inp in scoped if inp.get("selector")]
        if single_slot and len(usable) == 1:
            resolved[slot] = str(usable[0]["selector"])
            continue
        problems.append(
            f"{slot}: 作用域内 {len(usable)} 个输入框都没命中提示词" if usable else f"{slot}: 作用域内没有可定位的输入框"
        )

    dupes = {sel for sel in resolved.values() if list(resolved.values()).count(sel) > 1}
    for sel in sorted(dupes):
        problems.append(f"多个槽位解析到同一个输入框 {sel}，说明提示词分不开它们")
        resolved = {k: v for k, v in resolved.items() if v != sel}
    return resolved, problems


def build_skill_recipe(
    skill: ComponentSkill,
    inputs: list[dict],
    *,
    container_selector: str | None = None,
) -> dict[str, object]:
    """按真实页面输入框解析槽位；解析不出来就交出问题。

    模板里的 placeholder 选择器（如 input[placeholder='开始日期']）在别的站点上大概率零命中，
    混在真实 selector 里交给模型，它无法分辨哪个来自 DOM、哪个来自模板。
    """
    resolved, problems = _resolve_slots(skill, inputs)
    recipe: dict[str, object] = dict(resolved)
    if container_selector:
        recipe["container"] = container_selector

    # 面板、日期格只有点开控件后才存在于 DOM，这里给的是模板推断值，必须与已观测 selector 分开标注。
    panel = {k: v for k, v in skill.recipe_template.items() if k not in _observable_slots(skill)}
    if panel:
        recipe["panel_selectors_unverified"] = panel

    recipe["steps"] = list(skill.interaction_steps)
    if skill.fallback_steps:
        recipe["fallback_steps"] = list(skill.fallback_steps)
    if skill.notes:
        recipe["notes"] = list(skill.notes)

    recipe["actionable"] = not problems
    if problems:
        recipe["unresolved_slots"] = problems
        recipe["required_action"] = "interact_page_then_inspect_page"
    return recipe


def _skills_for_classes(classes: list[str]) -> list[ComponentSkill]:
    cls = set(classes)
    return [s for s in _SKILLS if any(fp in cls for fp in s.fingerprints)]


def discover_component_instances(inputs: list[dict], page_classes: list[str]) -> list[dict[str, object]]:
    """按控件容器逐个识别组件实例，而不是按整页类名指纹识别「页面上有哪些组件库」。

    整页指纹在同一页面有两个同库控件（两个区间选择器、查询+编辑各一套）时无法区分归属，
    槽位会全部解析到页面里第一个命中的输入框上；RangePicker 根节点同时带 ant-picker 和
    ant-picker-range，整页匹配还会把单日期和区间两个 skill 同时点亮。
    容器取输入框最近的、带框架/业务类名的祖先：同一控件的多个输入框落进同一容器，
    两个同款控件因 uid 不同而分开。
    """
    instances: list[dict[str, object]] = []
    groups: dict[str, dict[str, object]] = {}
    order: list[str] = []

    for inp in inputs:
        for anc in inp.get("ancestors") or []:
            matched = _skills_for_classes(anc.get("classes") or [])
            if not matched:
                continue
            uid = str(anc.get("uid"))
            if uid not in groups:
                groups[uid] = {
                    "skill": matched[0],
                    "selector": anc.get("selector"),
                    "classes": list(anc.get("classes") or []),
                    "inputs": [],
                }
                order.append(uid)
            group_inputs = groups[uid]["inputs"]
            assert isinstance(group_inputs, list)
            group_inputs.append(inp)
            break  # 只认最近的匹配祖先：更外层的表单/页面容器不是控件本身

    for uid in order:
        group = groups[uid]
        skill = group["skill"]
        assert isinstance(skill, ComponentSkill)
        scoped = group["inputs"]
        assert isinstance(scoped, list)
        container = group["selector"]
        instances.append({
            "type": f"{skill.library}/{skill.component}",
            "library": skill.library,
            "component": skill.component,
            "description": skill.description,
            "match_scope": "container",
            "interaction_recipe": build_skill_recipe(
                skill, scoped, container_selector=str(container) if container else None
            ),
            "claimed_selectors": [str(i["selector"]) for i in scoped if i.get("selector")],
        })
    container_classes = {c for g in groups.values() for c in g["classes"]}  # type: ignore[union-attr]
    return instances + _page_scope_instances(inputs, page_classes, instances, container_classes)


def _page_scope_instances(
    inputs: list[dict],
    page_classes: list[str],
    found: list[dict[str, object]],
    container_classes: set[str],
) -> list[dict[str, object]]:
    """整页指纹兜底：某些指纹挂在下拉面板的选项类名上（el-select-dropdown__item），
    它永远不会出现在输入框的祖先链里，只按容器匹配会整类漏掉。

    已被容器实例覆盖的指纹要排除，否则 RangePicker 容器上的 ant-picker 会再点亮一次单日期 skill。
    """
    covered = {(str(i["library"]), str(i["component"])) for i in found}
    claimed = {sel for i in found for sel in i.get("claimed_selectors") or []}  # type: ignore[union-attr]
    leftovers = [inp for inp in inputs if inp.get("selector") not in claimed]

    out: list[dict[str, object]] = []
    for skill in match_skills(page_classes):
        if (skill.library, skill.component) in covered:
            continue
        if any(fp in container_classes for fp in skill.fingerprints):
            continue
        recipe = build_skill_recipe(skill, leftovers)
        out.append({
            "type": f"{skill.library}/{skill.component}",
            "library": skill.library,
            "component": skill.component,
            "description": skill.description,
            "match_scope": "page",
            "interaction_recipe": recipe,
            "claimed_selectors": [
                str(v) for k, v in recipe.items()
                if k in _observable_slots(skill) and isinstance(v, str)
            ],
        })
    return out
