from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from app.services.ai_config_service import AiConfigService

logger = logging.getLogger("app.overlay_analyzer")

_ANALYSIS_TIMEOUT_SECONDS = 15.0  # 弹层分析属于辅助信息，超时需短于人工接管等待，避免拖慢转人工提示

_SYSTEM_PROMPT = (
    "你是 RPA 自动化助手，负责判断浏览器页面上出现的阻断型弹层具体是什么、"
    "为什么会挡住自动化操作，并给人工接管者一句简短、可执行的操作建议。"
    "只输出 JSON，不要输出多余文字或 markdown 代码块标记。"
    "JSON 字段：category（取值 captcha_slider|captcha_click|ad|cookie_consent|login_prompt|notice|unknown）、"
    "reason（一句话说明弹层内容和为什么会挡住自动化）、"
    "human_action_hint（给人工的具体操作建议，比如“拖动滑块到缺口位置”）、"
    "confidence（0~1 的置信度数字）。"
)


@dataclass(frozen=True)
class OverlayAnalysis:
    category: str
    reason: str
    human_action_hint: str
    confidence: float


def _build_messages(overlay_summary: dict[str, Any], screenshot_b64: str | None) -> list[dict[str, Any]]:
    text_payload = (
        f"标签(tag): {overlay_summary.get('tag')}\n"
        f"class: {overlay_summary.get('className')}\n"
        f"可见文本: {overlay_summary.get('text')}\n"
        f"内部可交互元素: {json.dumps(overlay_summary.get('interactive', []), ensure_ascii=False)}\n"
        f"是否含 iframe: {overlay_summary.get('hasIframe')}\n"
        f"检测方式: {overlay_summary.get('reason')}"
    )
    user_content: Any
    if screenshot_b64:
        user_content = [
            {"type": "text", "text": text_payload},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
        ]
    else:
        user_content = text_payload
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _parse_analysis(content: str) -> OverlayAnalysis | None:
    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except Exception:
        return None
    try:
        return OverlayAnalysis(
            category=str(data.get("category", "unknown")),
            reason=str(data.get("reason", "")),
            human_action_hint=str(data.get("human_action_hint", "")),
            confidence=float(data.get("confidence", 0.0)),
        )
    except Exception:
        return None


class OverlayAnalyzer:
    """用 AI 分析阻断型弹层原因，纯增强、不参与转人工判定。
    未配置模型/无 Key/调用失败/超时均 best-effort 返回 None，调用方回退启发式标签。
    """

    def __init__(self, config_service: AiConfigService | None = None) -> None:
        self._config_service = config_service or AiConfigService()

    async def analyze(self, overlay_summary: dict[str, Any], *, screenshot_b64: str | None = None) -> OverlayAnalysis | None:
        try:
            import litellm
        except Exception:
            return None

        config = self._config_service.load()
        model = str(config.get("default_model") or "").strip()
        if not model:
            return None

        base_url = self._config_service.get_base_url_for_model(model)
        api_key = self._config_service.get_api_key_for_model(model)
        if not api_key and not base_url:
            return None

        use_vision = screenshot_b64 is not None and _model_supports_vision(model)
        messages = _build_messages(overlay_summary, screenshot_b64 if use_vision else None)

        extra: dict[str, Any] = {}
        if base_url:
            extra["base_url"] = base_url
        if api_key:
            extra["api_key"] = api_key

        try:
            response = await asyncio.wait_for(
                litellm.acompletion(model=model, messages=messages, timeout=_ANALYSIS_TIMEOUT_SECONDS, **extra),
                timeout=_ANALYSIS_TIMEOUT_SECONDS,
            )
            content = response.choices[0].message.content or ""
        except Exception:
            logger.warning("弹层 AI 分析失败", exc_info=True)
            return None

        return _parse_analysis(content)


def _model_supports_vision(model_id: str) -> bool:
    # 延迟导入避免与 ai_orchestrator 形成模块级循环依赖。
    from app.services.ai_orchestrator import _model_supports_vision as _impl

    return _impl(model_id)
