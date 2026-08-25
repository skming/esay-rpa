"""LiteLLM / 中转上游错误的分类与清洗。

从 ai_orchestrator 拆出：这些判定纯按错误文本工作，与编排状态无耦合，独立成模块后
可单独测，也让主循环里"这是什么错、要不要降级重试"的判断有一个明确的归属地。
"""
from __future__ import annotations

import re

_VISION_ERROR_HINTS = (
    "does not support image",
    "not support vision",
    "vision is not supported",
    "image input is not supported",
    "multimodal",
    "image_url",
    "images are not",
    "does not support images",
    "doesn't support image",
    "unsupported content type",
    "Invalid content type",
    "image content",
)

_BALANCE_ERROR_HINTS = (
    "insufficient balance",
    "insufficient_balance",
    "insufficient quota",
    "insufficientquota",
    "credit balance is too low",
    "you exceeded your current quota",
    "exceeded your current quota",
    "account balance",
    "billing",
    "payment required",
    "402",
    "余额不足",
    "账户余额",
    "balance is insufficient",
    "no balance",
    "out of credits",
    "out of quota",
    "low balance",
)

_AUTH_ERROR_HINTS = (
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "api key is invalid",
    "api key not found",
    "unauthorized",
    "authentication failed",
    "invalid authentication",
    "invalid credentials",
)


def is_vision_error(msg: str) -> bool:
    lower = msg.lower()
    return any(hint in lower for hint in _VISION_ERROR_HINTS)


def is_balance_error(msg: str) -> bool:
    lower = msg.lower()
    return any(hint in lower for hint in _BALANCE_ERROR_HINTS)


# 中转拒绝的是「调用方这个客户端」而不是凭据（agentrouter 回 unauthorized client detected）。
# 这类文案里带 unauthorized，会被 _AUTH_ERROR_HINTS 的裸子串吃掉，于是同一把好密钥、
# 连中转确实上架的模型也照样被判成「API Key 无效」。所以要先于鉴权判，并把上游原话留给用户。
_CLIENT_REJECTED_HINTS = (
    "unauthorized client",
    "unauthorized_client",
    "client not allowed",
    "forbidden client",
)


def is_client_rejection(msg: str) -> bool:
    lower = msg.lower()
    return any(hint in lower for hint in _CLIENT_REJECTED_HINTS)


def is_auth_error(msg: str) -> bool:
    # 客户端被拒不算凭据失效：落进鉴权分支会让用户去重填一把本来就好的密钥
    if is_client_rejection(msg):
        return False
    lower = msg.lower()
    return any(hint in lower for hint in _AUTH_ERROR_HINTS)


_LITELLM_PREFIXES = (
    "litellm.MidStreamFallbackError: ",
    "litellm.APIConnectionError: ",
    "litellm.InternalServerError: ",
    "litellm.APIError: APIError: ",
    "litellm.AuthenticationError: ",
    "litellm.BadRequestError: ",
    "litellm.RateLimitError: ",
    "litellm.ServiceUnavailableError: ",
    "litellm.Timeout: ",
    "litellm.ContextWindowExceededError: ",
)


def clean_litellm_error(msg: str) -> str:
    """剥离 LiteLLM 异常前缀，提取可读错误信息。"""
    # 部分服务商把余额错误包装成 AuthenticationError，需在剥离前先按异常类型判断
    is_balance_by_type = "AuthenticationError" in msg and (
        "402" in msg or "balance" in msg.lower() or "quota" in msg.lower() or "credit" in msg.lower()
    )

    changed = True
    while changed:
        changed = False
        for prefix in _LITELLM_PREFIXES:
            if msg.startswith(prefix):
                msg = msg[len(prefix):]
                changed = True
                break

    original_idx = msg.find(" Original exception:")
    if original_idx != -1:
        msg = msg[:original_idx].strip()

    # 多层异常类前缀需循环剥离（如 APIConnectionError: OpenAIException - ...）
    changed = True
    while changed:
        new_msg = re.sub(r'^[A-Za-z]+(?:Exception|Error)\s*[-:]\s*', '', msg)
        changed = new_msg != msg
        msg = new_msg

    # 兼容双引号 JSON 和 Python dict repr（单引号）两种错误体格式
    m = re.search(r'["\']message["\']\s*:\s*["\']([^"\']+)["\']', msg)
    if m:
        msg = m.group(1)

    if is_vision_error(msg):
        return "当前模型不支持图片输入，请切换到支持视觉的模型（如 Claude、GPT-4、Gemini）后重试。"

    if is_balance_error(msg) or is_balance_by_type:
        return "模型账户余额不足，请前往服务商平台充值后重试。"

    if is_client_rejection(msg):
        # 上游原话里通常带着申诉入口（支持群/工单地址），换成我们自己的措辞等于把出路删掉
        return f"中转拒绝了本客户端（不是密钥问题，同一把密钥换模型同样被拒）：{msg[:200]}"

    if is_auth_error(msg):
        return "API Key 无效或已过期，请在设置页重新配置正确的 API Key。"

    lower = msg.lower()
    if "concurrency limit" in lower or "too many requests" in lower or "rate limit" in lower:
        return "请求并发或频率超限，请稍后重试。"

    return msg[:300] if len(msg) > 300 else msg
