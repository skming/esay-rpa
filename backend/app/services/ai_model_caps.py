"""模型能力差异的集中查询。

分级、上下文窗口、视觉、提示词缓存原先散在编排层里各扫一遍目录，加一个模型要改四处，
漏一处就是静默降级。收进独立模块后，上下文预算层（ai_context_window）与编排层都从这里取，
不必反向依赖 ai_orchestrator。
"""
from __future__ import annotations

from typing import NamedTuple


_DEFAULT_CONTEXT_WINDOW = 200_000


class _ModelCaps(NamedTuple):
    tier: str
    context_window: int
    supports_vision: bool
    supports_cache_control: bool


def _model_caps(model_id: str) -> _ModelCaps:
    """模型能力差异的唯一查询入口。

    分级、上下文窗口、视觉、提示词缓存原先各扫一遍目录、各写一套兜底，加一个模型
    要记得改四处；漏掉任一处的表现都是静默降级——图片被丢、缓存不生效、按错误的
    窗口裁剪历史——而不是报错。
    """
    from app.services.ai_config_service import AI_MODEL_CATALOG
    entry = next((e for e in AI_MODEL_CATALOG if e.get("id") == model_id), None)
    if entry is None:
        return _ModelCaps(
            tier="standard",
            context_window=_DEFAULT_CONTEXT_WINDOW,
            # 未知模型（自定义/中转透传）视觉乐观放行，被拒时靠 mid-stream fallback 兜底
            supports_vision=True,
            supports_cache_control=model_id.startswith(("claude-", "anthropic/")),
        )
    return _ModelCaps(
        tier=str(entry.get("tier") or "standard"),
        context_window=int(entry.get("context_window") or 0) or _DEFAULT_CONTEXT_WINDOW,
        supports_vision=not bool(entry.get("no_vision")),
        supports_cache_control=entry.get("provider") == "anthropic",
    )
