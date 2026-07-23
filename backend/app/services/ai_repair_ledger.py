"""跨会话的节点修复台账。

编排层已有的防打转护栏（字段回摆 `_detect_field_oscillation`、selector 修改预算
`_NODE_SELECTOR_FIX_BUDGET`）原本只活在单次请求的 guard_state 里：用户每发一条
「还是不行」，计数就清零，同一个失败方案可以在不同会话里被反复试上几十次。
「改了好几天还没修好」正是这么来的。

台账把这两份记录按 flow 落盘，开新会话时读回 guard_state，护栏逻辑本身不用改。
流程一旦跑通并通过业务校验就整份清空，避免陈旧计数挡住后续正常编辑。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.core.storage import resolve_ai_dir

_MAX_VALUES_PER_FIELD = 8   # 同一字段只留最近 8 个取值，够判断回摆
_MAX_FIELDS = 60            # 单个流程最多记 60 个字段轨迹
_STALE_SECONDS = 30 * 24 * 3600


def _ledger_dir() -> Path:
    return resolve_ai_dir() / "repairs"


def _ledger_path(flow_id: str) -> Path:
    return _ledger_dir() / f"flow_{flow_id}.json"


def load(flow_id: str | None) -> dict[str, Any]:
    """读取台账；文件缺失/损坏/过期一律当空账，绝不因此打断对话。"""
    empty: dict[str, Any] = {"node_field_history": {}, "node_selector_fix_counts": {}, "sessions": 0}
    if not flow_id:
        return empty
    path = _ledger_path(flow_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty
    if not isinstance(data, dict):
        return empty
    # 隔了一个月的修复轨迹多半对应已经变过的页面，留着只会误导
    if time.time() - float(data.get("updated_at") or 0) > _STALE_SECONDS:
        return empty
    return {
        "node_field_history": dict(data.get("node_field_history") or {}),
        "node_selector_fix_counts": dict(data.get("node_selector_fix_counts") or {}),
        "sessions": int(data.get("sessions") or 0),
    }


def save(
    flow_id: str | None,
    *,
    node_field_history: dict[str, list[str]],
    node_selector_fix_counts: dict[str, int],
    sessions: int,
) -> None:
    if not flow_id:
        return
    trimmed_history = {
        key: list(values)[-_MAX_VALUES_PER_FIELD:]
        for key, values in list(node_field_history.items())[:_MAX_FIELDS]
        if values
    }
    payload = {
        "node_field_history": trimmed_history,
        "node_selector_fix_counts": {k: int(v) for k, v in node_selector_fix_counts.items() if v},
        "sessions": sessions,
        "updated_at": time.time(),
    }
    try:
        _ledger_dir().mkdir(parents=True, exist_ok=True)
        _ledger_path(flow_id).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # 台账是辅助信号，写不进去不该影响对话


def clear(flow_id: str | None) -> None:
    """流程跑通并通过业务校验后调用：问题已解决，历史尝试不再构成约束。"""
    if not flow_id:
        return
    try:
        _ledger_path(flow_id).unlink(missing_ok=True)
    except Exception:
        pass


def summarize(ledger: dict[str, Any]) -> str | None:
    """把台账压成一条给模型看的提示；没有值得提的历史就返回 None。

    只报「改过 2 次以上仍在被修」的节点——改过一次很正常，报出来只是噪音。
    """
    counts: dict[str, int] = ledger.get("node_selector_fix_counts") or {}
    history: dict[str, list[str]] = ledger.get("node_field_history") or {}
    hot = sorted(((nid, n) for nid, n in counts.items() if n >= 2), key=lambda kv: -kv[1])[:5]
    if not hot:
        return None

    lines = [
        "【历史修复记录】这个流程在**之前的会话**里已经被修过，以下节点反复改动但问题仍未解决：",
    ]
    for node_id, count in hot:
        tried = history.get(f"{node_id}.selector") or []
        detail = f"，试过的 selector：{tried}" if tried else ""
        lines.append(f"- `{node_id}`：selector 累计改过 {count} 次{detail}")
    lines.append(
        "**同一个方案换个写法再试一遍不会有新结果。** 这类反复失败的根因通常不在 selector 文本上，"
        "而是：执行器/事件层面的差异（合成事件被组件忽略）、页面存在 DOM 看不出的状态（遮挡、未跳转、验证码）、"
        "或者动作打在了错误的元素上（如按键打在 body 而非输入框）。"
        "先用 inspect_page / inspect_screenshot / get_run_error 拿到新证据，"
        "明确说出这次改的是哪一层、与之前哪次不同，再动手。"
        "若判断已超出可自动修复的范围，直接告诉用户你的判断和依据，不要继续试。"
    )
    return "\n".join(lines)
