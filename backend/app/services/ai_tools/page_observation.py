"""把 page_probe_js 的原始返回加工成交给模型的观察结果。

与 executor 分开，是因为同一份加工要用在两条路径上：一次性的 inspect_page(url=...) 和
探索会话里对当前页面的重复观察。两边各写一份就会各自漂移。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _shared_script(name: str) -> str:
    """从 page_effect.js 取一段脚本。

    脚本本体放在 .js 里而不是这里，是因为扩展内容脚本要 import 它：MV3 的扩展 CSP
    禁 unsafe-eval，下发字符串再 eval 这条路走不通，而两边各写一份必然漂移。
    """
    marker = f"export const {name} = "
    _, found, tail = (Path(__file__).with_name("page_effect.js")).read_text(encoding="utf-8").partition(marker)
    expression = tail.split("\nexport const ", 1)[0].strip().rstrip(";")
    if not found or not expression.endswith("}"):
        raise RuntimeError(f"page_effect.js 里没有取到完整的 {name} 表达式")
    return expression

# 只给服务端用的字段：祖先链用于按容器识别组件，容器链用于按容器分组日期控件实例，
# 全量类名用于指纹匹配。交给模型只会挤占上下文，还会让它照着祖先类名瞎拼选择器。
_SERVER_ONLY_ELEMENT_KEYS = ("ancestors", "containers")


def strip_server_only(result: dict[str, Any]) -> None:
    for key in ("inputs", "selects"):
        for item in result.get(key) or []:
            if isinstance(item, dict):
                for drop in _SERVER_ONLY_ELEMENT_KEYS:
                    item.pop(drop, None)


SPA_LOADING_CLASSES = ("v-loading", "el-loading-mask", "ant-spin-spinning", "arco-spin")
# 这两个是「加载遮罩正在淡入淡出」的过渡类名，出现时遮罩正要消失，不是仍在加载
_LOADING_FADE_CLASSES = ("el-loading-fade-enter", "el-loading-fade-leave")


def detect_spa_loading(page_classes: list[str], all_classes: list[str]) -> bool:
    """页面是不是还在渲染。判早了模型会拿一份空元素列表去改流程拓扑。

    精确指示类名用全量集合（组件库的遮罩类名常挂在 DOM 靠后的浮层上，截断就漏）；
    loading/skeleton 子串匹配只在截断后的前 120 个里做，全量集合上误报率太高。
    """
    return (
        "nprogress-busy" in all_classes
        or any(cls in all_classes for cls in SPA_LOADING_CLASSES)
        or any(
            "loading" in cls or "skeleton" in cls
            for cls in page_classes
            if cls not in _LOADING_FADE_CLASSES
        )
    )


def finalize_observation(result: dict[str, Any]) -> bool:
    """探测返回 → 交给模型的观察结果：识别日期控件、判加载态、摘掉服务端专用字段。

    两条观察通道（Playwright 与 Chrome 扩展）都必须走这里。扩展那条只要另写一份，
    同一个页面在两条通道上就会得出不同的控件配方，而两边都自称读的是真实 DOM。

    返回 spa_loading，让调用方决定要不要挂自己的 warning。
    """
    page_classes: list[str] = result.get("page_classes") or []
    all_classes: list[str] = result.pop("all_classes", None) or page_classes
    try:
        controls = build_date_controls(result.get("inputs") or [], list(all_classes))
        if controls:
            result["date_controls"] = controls
    except Exception:
        pass  # 控件识别是增益，任何情况下都不能让整次观察失败
    strip_server_only(result)
    return detect_spa_loading(page_classes, list(all_classes))


OBSERVATION_NOTE = (
    "selector 字段为推荐选择器，可直接用于 browser.click / browser.fill 等节点。"
    ":has-text() 为 Playwright 伪选择器，合法可用。"
    "ref 字段只在本次观察内有效，只能给 interact_page 用；"
    "存进流程的节点必须写 selector，ref 换一次观察就失效。"
    "matches>1 说明该 selector 命中多个元素，必须先用 container 收窄再写进流程。"
    "若 date_controls 字段存在，按 interaction_recipe.steps 构建节点（selector 直接用，"
    "日期文本/目标年月/节点数量按本次任务改写）；主路线走不通时才看 fallback_steps，"
    "notes 里是该框架与执行器的已知限制；"
    "actionable=false 表示槽位没解析出来（见 unresolved_slots），"
    "改用 interact_page 点开控件后再观察，不要照抄 panel_selectors_unverified。"
)

_SPA_LOADING_WARNING = (
    "检测到页面加载指示，当前观察可能尚不完整。"
    "请根据当前页面已观察到的目标使用 wait_selector 等待后重新取证；"
    "仅凭加载态不能判断缺少导航节点，也不能证明增加 delayMs 能解决问题。"
)

_EMPTY_PAGE_WARNING = (
    "⚠️ 页面元素为空——SPA 可能未渲染完毕。"
    "请重新调用 inspect_page，并指定 wait_selector 参数等待页面核心元素出现，"
    "例如 wait_selector='nav, table, [role=grid], [role=navigation], main'。"
    "如果多次重试仍为空，请检查 url 是否正确、是否需要重新登录。"
)


def annotate_observation(result: dict[str, Any]) -> bool:
    """探测返回 → 交给模型的观察结果：字段说明、控件识别、加载态与空页告警。

    两条观察通道都必须走这里。同一个页面在两条通道上给出的告警和配方一旦不同，
    模型会按看到的那份去改流程拓扑，而两边都自称读的是真实 DOM。
    """
    result["note"] = OBSERVATION_NOTE
    spa_loading = finalize_observation(result)
    result["spa_loading"] = spa_loading
    if spa_loading:
        result["warning"] = _SPA_LOADING_WARNING
        return True
    # 空元素判定必须在加载态之后：只有 logo 的页面元素数也是 1，先判空会把「还在渲染」
    # 报成「页面就是这样」，模型于是拿一份空列表去写 selector。
    if sum(len(result.get(key) or []) for key in ("inputs", "buttons", "links", "tables")) == 0:
        result["warning"] = _EMPTY_PAGE_WARNING
    return False


# 定位失败的三种结论：措辞和 required_action 由这里单点给出，两条通道共用。
# 各写一份的话，同一个「命中多个元素」在扩展通道上会变成另一句话，模型的应对也跟着变。
def stale_ref_error(detail: str) -> dict[str, Any]:
    return {"status": "stale_element_ref", "error": detail, "required_action": "call_inspect_page_again"}


def element_not_found_error(selector: str) -> dict[str, Any]:
    return {
        "status": "element_not_found",
        "error": f"selector {selector} 在当前页面（或 iframe）上没有命中元素",
        "required_action": "call_inspect_page_again",
    }


def ambiguous_selector_error(selector: str, matches: int) -> dict[str, Any]:
    # 同一个页面上「查询」按钮往两三处都有。默认取第一个，在探索阶段看着能过，
    # 写进流程后点的是另一行的按钮——错的元素比找不到元素难查得多。
    return {
        "status": "ambiguous_selector",
        "matches": matches,
        "error": f"selector {selector} 命中 {matches} 个元素，无法确定操作哪一个",
        "required_action": "narrow_selector_or_use_element_ref",
    }


def build_date_controls(inputs: list[dict], all_classes: list[str]) -> list[dict[str, Any]]:
    """识别页面上的组件库控件实例，并对没有被任何实例认领的日期输入框给出通用配方。"""
    from app.services.skills.generic import build_generic_date_controls
    from app.services.skills.registry import discover_component_instances

    instances = discover_component_instances(inputs, all_classes)
    claimed = {sel for inst in instances for sel in inst.get("claimed_selectors") or []}

    controls: list[dict[str, Any]] = []
    for inst in instances:
        item = {k: v for k, v in inst.items() if k != "claimed_selectors"}
        controls.append(item)

    # 剩下的输入框才走通用识别。按「页面上有没有日期类组件」整体压制通用配方，
    # 会让同一页面里另一个无关的日期输入框（组件库没写过配方、或干脆是原生 input）
    # 一并失去可用配方，模型只能凭空猜它的交互方式。
    leftovers = [inp for inp in inputs if inp.get("selector") not in claimed]
    for generic in build_generic_date_controls(leftovers):
        generic.setdefault("match_scope", "unclaimed_inputs")
        controls.append(generic)
    return controls


# 操作前后的页面指纹：只取几个能反映「面板开了、页面换了、内容变了」的量，
# 比完整探测便宜得多，可以每次操作都取两次。
EFFECT_SIGNATURE_JS = _shared_script("EFFECT_SIGNATURE")

_EFFECT_KEYS = ("url", "elements", "options", "layers", "active")


def describe_effect(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """整页有没有可观测的变化。只回答页面层面这一件事。

    changed=false 不等于动作失败：已聚焦的 fill、原生 select 换选项、不新增 DOM 的滚动都不动指纹。
    changed=true 也不等于业务成功：焦点变化、无关的异步刷新同样会让指纹变。动作本身有没有达到
    目标状态由 describe_action_effect 回答，业务后置条件只能靠抓回的数据断言。
    """
    diff = {
        key: [before.get(key), after.get(key)]
        for key in _EFFECT_KEYS
        if before.get(key) != after.get(key)
    }
    # 哈希值本身对模型没有信息量，只报「可见文字变了」这个结论
    if before.get("text_hash") != after.get("text_hash"):
        diff["visible_text"] = "changed"
    return {"changed": bool(diff), "diff": diff}


# 目标状态回读：页面指纹只看整页，看不见「这个框现在的值、这个 select 选了谁、滚到哪了」。
# el 为空时读文档滚动容器——整页滚动没有元素可回读。
TARGET_STATE_JS = _shared_script("TARGET_STATE")


def _state_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key in sorted(set(before) | set(after)):
        if key in ("tag", "scroll"):
            continue
        if before.get(key) != after.get(key):
            diff[key] = [before.get(key), after.get(key)]
    sb, sa = before.get("scroll") or {}, after.get("scroll") or {}
    if (sb.get("top"), sb.get("left")) != (sa.get("top"), sa.get("left")):
        diff["scroll"] = [[sb.get("top"), sb.get("left")], [sa.get("top"), sa.get("left")]]
    return diff


def _norm(value: Any) -> str:
    return "" if value is None else str(value).strip()


# 三类结论必须分开说：动作被页面接收 ≠ 目标状态达到 ≠ 业务后置条件成立。混成一句「操作成功」，
# 模型就会拿「值写进去了」当筛选已生效——页面返回全量数据，流程照样报绿。
_VALUE_NOT_COMMITTED = (
    "值已经在控件里，但组件收下没收下要看后续：很多组件只在 Enter/blur 时提交。"
    "筛选是否真的生效只能靠抓回的数据断言。"
)
_VALUE_MISMATCH = (
    "控件里的值与写入值不一致：有的组件会自己格式化，有的直接把输入吃掉。"
    "以回读值为准，必要时换格式重试，不要加大 delayMs。"
)
_SCROLL_AT_END = "已经滚到可滚动范围底部，不是动作没生效；要继续取数据得换翻页方式。"
_SCROLL_STUCK = (
    "滚动位置没变且没到底：selector 可能指到了内容而不是带 overflow 的滚动容器。"
)
_NO_TARGET_STATE = "这个动作没有由参数指定的目标状态，只能看页面与元素的状态变化。"
_FOCUS_ONLY = "只观察到焦点变化：点到了元素，但面板/选中/提交都没有证据，不能当成功。"
_NOTHING_OBSERVED = (
    "没有观察到任何变化。这不等于失败：变化可能落在观测不到的位置（iframe、closed shadow DOM、"
    "还没完成的异步）。先确认目标元素对不对、是否需要先聚焦或按键，再决定换目标；不要加大 delayMs。"
)
_STATE_UNREADABLE = "读不到目标元素的状态（元素可能已被替换或移出文档），只能靠页面变化判断。"


def describe_action_effect(
    action: str,
    value: str | None,
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    page_diff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """这个动作自己的目标状态达到了没有——与「页面有没有变化」是两件事。

    回读它该改的那个状态（value、选中项、滚动位置），分成达到、本来就达到、没达到、读不到。
    少了这一层，不动页面指纹的动作会被报成「没生效，换目标」，而它其实已经成功。
    """
    if not after:
        return {"status": "unknown", "note": _STATE_UNREADABLE}

    diff = _state_diff(before, after)
    # 焦点不算「页面变了」：点一个没绑事件的按钮也会拿到焦点，整页指纹里的 active 跟着变。
    # 把它算进去，死按钮就会被报成 state_changed——正是最需要被认出来的那一种。
    page_changed = bool(set(page_diff or {}) - {"active"})
    want = _norm(value)
    note: str | None = None

    if action in ("fill", "select_option"):
        if action == "fill":
            now, was = [_norm(after.get("value"))], [_norm(before.get("value"))]
        else:
            now = [_norm(v) for v in (after.get("selected") or [])]
            now += [_norm(v) for v in (after.get("selected_text") or [])]
            was = [_norm(v) for v in (before.get("selected") or [])]
            was += [_norm(v) for v in (before.get("selected_text") or [])]
        if want in now:
            status = "already_in_target_state" if want in was else "target_reached"
            note = _VALUE_NOT_COMMITTED
        else:
            status, note = "target_not_reached", _VALUE_MISMATCH
    elif action == "scroll":
        sb, sa = before.get("scroll") or {}, after.get("scroll") or {}
        top_a, room = int(sa.get("top") or 0), int(sa.get("max") or 0)
        at_end = room <= 4 or top_a >= room - 2
        if int(sb.get("top") or 0) != top_a:
            status = "target_reached"
        elif at_end:
            status, note = "already_in_target_state", _SCROLL_AT_END
        else:
            status, note = "target_not_reached", _SCROLL_STUCK
    elif (diff and set(diff) != {"focused"}) or page_changed:
        status, note = "state_changed", _NO_TARGET_STATE
    elif diff:
        status, note = "focus_only", _FOCUS_ONLY
    else:
        status, note = "no_observable_change", _NOTHING_OBSERVED

    out: dict[str, Any] = {"status": status, "target_state": after, "state_diff": diff}
    if note:
        out["note"] = note
    return out


def build_interaction_result(
    action: str,
    element_ref: str | None,
    selector: str | None,
    action_effect: dict[str, Any],
    effect: dict[str, Any],
    observation: dict[str, Any],
    wait_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """两条执行通道使用相同的证据结论，通道只负责动作与回读。"""
    result: dict[str, Any] = {
        "status": "ok", "action": action,
        "target": {"element_ref": element_ref, "selector": selector},
        "effect": effect, "action_effect": action_effect, "observation": observation,
    }
    if action == "fill":
        filled = (action_effect.get("target_state") or {}).get("value")
        if filled is not None:
            result["input_value_after"] = filled
    verdict = action_effect.get("status")
    if verdict in ("target_not_reached", "focus_only", "no_observable_change", "unknown"):
        result["warning"] = action_effect.get("note") or ""
    elif verdict in ("target_reached", "already_in_target_state", "state_changed"):
        result["business_check"] = (
            "未验证：动作被页面接收 ≠ 业务后置条件成立（筛选真的生效、表单真的提交）。"
            "业务结论只能靠抓回的数据断言，或提交后回读服务端返回的内容。"
        )
    if wait_result is not None:
        result["wait_result"] = wait_result
    return result
