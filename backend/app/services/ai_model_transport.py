"""模型请求、供应商适配与流式响应解析；不依赖流程、Guard 或会话台账。"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from app.services.ai_config_service import AiConfigService
from app.services.ai_relay_errors import clean_litellm_error, is_vision_error

logger = logging.getLogger(__name__)
LLM_REQUEST_TIMEOUT = 90
FIRST_TOKEN_TIMEOUT = 60
STALL_TIMEOUT = 60


class ModelTransportError(Exception):
    def __init__(self, message: str, *, vision_unsupported: bool = False) -> None:
        super().__init__(message)
        self.vision_unsupported = vision_unsupported


_relay_models_cache: dict[str, tuple[list[str], float]] = {}
_RELAY_CACHE_TTL = 300  # seconds

@dataclass
class _RelayClient:
    client: Any
    users: int = 0
    retired: bool = False


_relay_client_cache: dict[tuple[str, str], _RelayClient] = {}


def _get_relay_client(base_url: str, api_key: str) -> _RelayClient | None:
    cache_key = (base_url, api_key)
    client = _relay_client_cache.get(cache_key)
    if client is not None:
        return client
    try:
        import httpx as _httpx
        import openai as _openai
    except Exception:
        return None  # openai 包不可用时回退到普通 litellm

    class _CleanRelay(_httpx.AsyncHTTPTransport):
        """剥离 SDK 遥测请求头，替换部分中转商会拦截的 user-agent。"""

        async def handle_async_request(self, request: "_httpx.Request") -> "_httpx.Response":
            clean = [
                (k, b"python-httpx/0.27" if k.lower() == b"user-agent" else v)
                for k, v in request.headers.raw
                if k.decode().lower() not in _STRIP_HEADERS
            ]
            request = _httpx.Request(
                method=request.method,
                url=request.url,
                headers=clean,
                stream=request.stream,
                extensions=request.extensions,
            )
            return await super().handle_async_request(request)

    client = _openai.AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=_httpx.AsyncClient(transport=_CleanRelay()),
    )
    entry = _RelayClient(client)
    _relay_client_cache[cache_key] = entry
    return entry


async def _close_relay_client(entry: _RelayClient) -> None:
    try:
        await entry.client.close()
    except Exception:
        logger.warning("Failed to close relay client")


async def close_relay_clients() -> None:
    global _relay_models_cache
    entries = list(_relay_client_cache.values())
    _relay_client_cache.clear()
    # 旧的 /models 请求仍可能在等待；它只能写回旧缓存，不能污染新配置。
    _relay_models_cache = {}
    for entry in entries:
        entry.retired = True
    idle = [entry for entry in entries if entry.users == 0]
    for entry in idle:
        await _close_relay_client(entry)


@asynccontextmanager
async def _relay_client_lease(base_url: str | None, api_key: str) -> AsyncIterator[Any]:
    entry = _get_relay_client(base_url, api_key) if base_url else None
    if entry is not None:
        entry.users += 1
    try:
        yield entry.client if entry is not None else None
    finally:
        if entry is not None:
            entry.users -= 1
            # 配置变更不能关闭另一条并发响应正在使用的连接池。
            if entry.retired and entry.users == 0:
                await _close_relay_client(entry)

# OpenAI SDK 注入的、部分中转商会拦截的请求头；user-agent 单独替换
_STRIP_HEADERS = frozenset({
    "x-stainless-lang", "x-stainless-package-version", "x-stainless-os",
    "x-stainless-arch", "x-stainless-runtime", "x-stainless-runtime-version",
    "x-stainless-async", "x-stainless-raw-response", "x-stainless-retry-count",
    "x-stainless-read-timeout",
})


def _normalize_base_url(url: str | None) -> str | None:
    """确保 base_url 以 /v1 结尾，供 LiteLLM 拼接 /v1/chat/completions。"""
    if not url:
        return url
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url = url + "/v1"
    return url


async def _fetch_relay_models(base_url: str, api_key: str) -> list[str]:
    """中转上架的模型 ID；空列表表示没问出来（网络失败、非 200、或该密钥无权列举）。

    空和「确实一个模型都没有」不可分，所以空列表只能当作无证据，不能拿来判某模型不存在。
    """
    import httpx

    cache_key = f"{base_url}|{api_key}"
    cache = _relay_models_cache
    cached = cache.get(cache_key)
    if cached and time.monotonic() - cached[1] < _RELAY_CACHE_TTL:
        return cached[0]

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        relay_models = [m["id"] for m in r.json().get("data", []) if m.get("id")] if r.status_code == 200 else []
    except Exception:
        relay_models = []
    cache[cache_key] = (relay_models, time.monotonic())
    return relay_models


async def _resolve_relay_model(model: str, base_url: str, api_key: str) -> str:
    """在中转 base_url 下从其实际提供的模型中匹配最接近的一个，按 openai/ 前缀路由。"""
    relay_models = await _fetch_relay_models(base_url, api_key)

    if not relay_models:
        bare = model.split("/", 1)[-1]
        return f"openai/{bare}"

    non_chat = (
        "embed", "whisper", "tts", "audio", "dall-e", "dalle", "image",
        "moderation", "rerank", "vision-ocr", "stable-diffusion", "flux",
        "realtime", "codex",
    )
    chat_models = [rm for rm in relay_models if not any(kw in rm.lower() for kw in non_chat)]
    if not chat_models:
        chat_models = relay_models  # 中转只有非标准模型时的兜底

    # 无 provider 前缀的 OpenAI 原生 ID（如 "gpt-5.4-mini"）取首个 "-" 段作为 family
    bare = model.split("/", 1)[-1]
    if "/" in model:
        family = model.split("/", 1)[0].lower()
    else:
        family = bare.split("-")[0].lower()

    for rm in chat_models:
        if rm == bare:
            return f"openai/{rm}"
    for rm in chat_models:
        if rm.startswith(bare):
            return f"openai/{rm}"
    # 按名称倒序，优先匹配高版本/latest
    family_matches = sorted(
        [rm for rm in chat_models if family in rm.lower()],
        reverse=True,
    )
    if family_matches:
        return f"openai/{family_matches[0]}"
    return f"openai/{sorted(chat_models, reverse=True)[0]}"


def _split_partial_tag_suffix(text: str, tag: str) -> tuple[str, str]:
    """若 text 以 tag 的真前缀结尾（如 "<thi"——标签被流式 chunk 边界劈开），
    把该前缀扣下留到拼上下一个 chunk 后再判定，返回 (可安全发出的部分, 扣下的部分)。"""
    for plen in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:plen]):
            return text[:-plen], text[-plen:]
    return text, ""


def _serialize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """请求出口合并 system；内部历史保留状态块位置，供更新和压缩使用。"""
    systems = [message for message in messages if message.get("role") == "system"]
    history = [{key: value for key, value in message.items() if key != "_context_synthetic"}
               for message in messages if message.get("role") != "system"]
    if not systems:
        return history
    if all(isinstance(message["content"], str) for message in systems):
        content: Any = "\n\n".join(message["content"] for message in systems)
    else:
        # 保留 Anthropic 文本块上的缓存断点，不把动态状态加入稳定前缀断点。
        content = []
        for message in systems:
            value = message["content"]
            content.extend([{"type": "text", "text": value}] if isinstance(value, str) else value)
    return [{"role": "system", "content": content}, *history]


def _log_prompt_cache_usage(model: str, round_num: int, usage: Any, elapsed_s: float | None = None) -> None:
    """记录每轮输入规模、缓存命中量与耗时。

    缓存数为 0 也照记：只打印非零命中会让缓存完全没生效的会话一条日志都没有，
    正好是最需要排查的那种。
    """
    if usage is None:
        return
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    created = getattr(usage, "cache_creation_input_tokens", None)
    logger.info(
        "llm round (model=%s round=%s): 输入 %s tokens（缓存命中 %s / 写入 %s），输出 %s tokens，耗时 %s",
        model, round_num,
        getattr(usage, "prompt_tokens", "?"), cached or 0, created or 0,
        getattr(usage, "completion_tokens", "?"),
        f"{elapsed_s:.1f}s" if elapsed_s is not None else "?",
    )


class _ThinkTagFilter:
    """把嵌在正文里的 <think>…</think> 拆成可见文本与思维链。

    DeepSeek-R1、非 API-thinking 模式的 Qwen3 不走独立的 reasoning_content 字段。
    """

    def __init__(self) -> None:
        self._in_think = False
        # 标签可能被 chunk 边界劈开（"<thi"+"nk>"），结尾的疑似标签前缀先扣下
        self._carry = ""

    def feed(self, delta: str) -> list[tuple[str, str]]:
        """返回 [(事件类型, 文本)]，事件类型为 "text" 或 "thinking"。"""
        events: list[tuple[str, str]] = []
        raw = self._carry + delta
        self._carry = ""
        visible = ""
        while raw:
            if not self._in_think:
                idx = raw.find("<think>")
                if idx == -1:
                    raw, self._carry = _split_partial_tag_suffix(raw, "<think>")
                    visible += raw
                    break
                visible += raw[:idx]
                self._in_think = True
                raw = raw[idx + 7:]
            else:
                idx = raw.find("</think>")
                if idx == -1:
                    raw, self._carry = _split_partial_tag_suffix(raw, "</think>")
                    if raw:
                        events.append(("thinking", raw))
                    break
                frag = raw[:idx]
                if frag:
                    events.append(("thinking", frag))
                self._in_think = False
                raw = raw[idx + 8:].lstrip("\n")
        if visible:
            events.append(("text", visible))
        return events

    def flush(self) -> list[tuple[str, str]]:
        """流结束：扣下的疑似标签前缀并不是完整标签，按当前状态原样补发。"""
        if not self._carry:
            return []
        events = [("thinking" if self._in_think else "text", self._carry)]
        self._carry = ""
        return events


@dataclass
class RoundOutput:
    """一轮模型输出的全部落点。调用方在流结束后按这些字段决定收尾还是执行工具。"""

    text: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    tool_calls: dict[int, dict[str, str]] = field(default_factory=dict)
    # 已发出 tool_start 的流式索引，避免工具执行后重复发卡片
    emitted_tool_starts: set[int] = field(default_factory=set)
    finish_reason: str | None = None
    usage: Any = None
    elapsed_s: float = 0.0
    # 看门狗判定的致命错误。生成器不能带返回值，改由调用方读这个字段决定是否终止整条流。
    stall_error: str | None = None


async def _consume_round_stream(
    response: Any, output: RoundOutput, *, model: str, round_num: int
) -> AsyncIterator[dict[str, Any]]:
    """把一轮流式响应收进 output，同时把要给前端的增量事件让出去。

    逐 chunk 手动迭代 + 看门狗：中转卡死（无首 token 或流中途停滞）时不再等待，
    把停滞原因写进 output.stall_error 并结束迭代。
    """
    think_filter = _ThinkTagFilter()
    response_iter = response.__aiter__()
    got_first_chunk = False
    while True:
        chunk_timeout = STALL_TIMEOUT if got_first_chunk else FIRST_TOKEN_TIMEOUT
        try:
            chunk = await asyncio.wait_for(response_iter.__anext__(), timeout=chunk_timeout)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            if got_first_chunk:
                output.stall_error = f"模型在输出中途停止响应（超过 {STALL_TIMEOUT}s 无新内容），请重试或更换模型。"
            else:
                output.stall_error = (
                    f"模型 {model} 超过 {FIRST_TOKEN_TIMEOUT}s 未返回任何内容。"
                    "可能是当前中转服务不可用或该模型已下线，请在设置页更换模型或中转地址后重试。"
                )
            logger.warning("LLM stream timeout (model=%s, got_first=%s)", model, got_first_chunk)
            return
        got_first_chunk = True
        output.usage = getattr(chunk, "usage", None) or output.usage
        if not chunk.choices:
            continue  # 用量统计块不带 choices
        choice = chunk.choices[0]
        delta = choice.delta
        output.finish_reason = choice.finish_reason or output.finish_reason

        thinking_delta: str | None = getattr(delta, "reasoning_content", None)
        if thinking_delta:
            output.thinking.append(thinking_delta)
            yield {"type": "thinking", "delta": thinking_delta}

        if delta.content:
            for kind, text in think_filter.feed(delta.content):
                (output.thinking if kind == "thinking" else output.text).append(text)
                yield {"type": kind, "delta": text}

        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in output.tool_calls:
                    # call_id 独立于厂商的 tc.id：后者可能缺失或到得比 tool_start 晚，
                    # 而前端要靠它把 tool_args/tool_result 对到具体那张卡片上
                    output.tool_calls[idx] = {
                        "id": "", "name": "", "arguments": "", "call_id": f"r{round_num}_{idx}",
                    }
                entry = output.tool_calls[idx]
                if tc.id:
                    entry["id"] = tc.id
                if tc.function and tc.function.name:
                    entry["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    entry["arguments"] += tc.function.arguments
                # 工具名一确定就立即发 tool_start，无需等整个流结束
                if idx not in output.emitted_tool_starts and entry["name"]:
                    output.emitted_tool_starts.add(idx)
                    yield {"type": "tool_start", "tool": entry["name"], "args": "", "call_id": entry["call_id"]}
    for kind, text in think_filter.flush():
        (output.thinking if kind == "thinking" else output.text).append(text)
        yield {"type": kind, "delta": text}
    # 部分模型/中转流式返回 tool_calls 时不带 id；空 tool_call_id 会让
    # 严格的 OpenAI 兼容端点在下一轮请求时拒绝整个对话，这里合成兜底 id。
    for idx, entry in output.tool_calls.items():
        if not entry["id"]:
            entry["id"] = f"call_r{round_num}_{idx}"


class ModelTransport:
    def __init__(self, config_service: AiConfigService | None = None) -> None:
        self._config_service = config_service

    @staticmethod
    def ensure_available() -> Any:
        try:
            import litellm
        except ImportError as exc:
            raise ModelTransportError("litellm 未安装，请运行 pip install litellm") from exc
        return litellm

    def is_relayed(self, model: str) -> bool:
        return bool(self._config_service and self._config_service.get_base_url_for_model(model))

    @asynccontextmanager
    async def _completion_kwargs(self, model: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        raw_base_url = self._config_service.get_base_url_for_model(model) if self._config_service else None
        api_key = self._config_service.get_api_key_for_model(model) if self._config_service else None
        # LiteLLM 直接把 /chat/completions 拼到 base_url 后面，须以 /v1 结尾
        base_url = _normalize_base_url(raw_base_url)

        # 提前给出清晰的缺 key 提示；配置了中转时跳过（中转自行处理鉴权）
        if not api_key and self._config_service and not base_url:
            from app.services.ai_config_service import AI_MODEL_CATALOG
            required_env_key = next(
                (m.get("env_key", "") for m in AI_MODEL_CATALOG if m["id"] == model), ""
            )
            if required_env_key and not os.environ.get(required_env_key):
                raise ModelTransportError(
                    f"模型 {model} 未配置 API Key（需要 {required_env_key}），请在设置页添加。"
                )
        # 中转未配置 key 时用占位符，供 litellm 组出合法 Authorization 头
        if base_url and not api_key:
            api_key = "sk-relay"

        extra: dict[str, Any] = {}
        effective_model = model
        async with _relay_client_lease(base_url, api_key or "sk-relay") as relay_client:
            if base_url and api_key:
                effective_model = await _resolve_relay_model(model, base_url, api_key)
            if base_url:
                extra["base_url"] = base_url
            if relay_client is not None:
                extra["client"] = relay_client
            if api_key:
                extra["api_key"] = api_key
            yield effective_model, extra

    async def stream_round(
        self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
        output: RoundOutput, *, round_num: int,
    ) -> AsyncIterator[dict[str, Any]]:
        started_at = time.monotonic()
        litellm = self.ensure_available()
        try:
            async with self._completion_kwargs(model) as (effective_model, extra):
                request: dict[str, Any] = {
                    "model": effective_model,
                    "messages": messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "drop_params": True,
                    "timeout": LLM_REQUEST_TIMEOUT,
                    **extra,
                }
                if tools:
                    request.update(tools=tools, tool_choice="auto")
                response = await litellm.acompletion(**request)
                try:
                    async with aclosing(_consume_round_stream(
                        response, output, model=effective_model, round_num=round_num,
                    )) as events:
                        async for event in events:
                            yield event
                    if output.stall_error is None:
                        output.elapsed_s = time.monotonic() - started_at
                        _log_prompt_cache_usage(effective_model, round_num, output.usage, output.elapsed_s)
                finally:
                    close_response = getattr(response, "aclose", None)
                    if close_response is not None:
                        try:
                            await close_response()
                        except Exception:
                            logger.warning("Failed to close LLM response")
        except ModelTransportError:
            raise
        except Exception as exc:
            raise ModelTransportError(
                clean_litellm_error(str(exc)), vision_unsupported=is_vision_error(str(exc)),
            ) from exc
