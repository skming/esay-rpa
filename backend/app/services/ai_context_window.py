"""多轮工具循环的上下文预算管理：压缩旧工具结果、超限时整轮丢弃、维护缓存断点。

这些函数只对消息列表和模型窗口做纯变换，与编排状态无耦合，独立成模块后可单测，
也让 ai_orchestrator 不必把上下文裁剪的细节和主循环搅在一起。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.ai_model_caps import _DEFAULT_CONTEXT_WINDOW, _model_caps

logger = logging.getLogger(__name__)


# 多轮工具循环里 inspect_page / get_run_output 等结果动辄上万字符，旧结果对后续决策
# 只剩摘要价值。每轮请求前压缩「除最近 N 条外」的大体积 tool 消息，避免长会话
# 撑爆上下文窗口或拖慢每轮请求。
_KEEP_FULL_TOOL_RESULTS = 2          # 最近 N 条 tool 消息保留完整内容
_TOOL_COMPACT_THRESHOLD = 3_000      # 超过该字符数的旧 tool 消息才压缩
_COMPACTED_MARK = '"_compacted": true'
_CHARS_PER_TOKEN = 1.5               # 中英混排保守估计：纯 ASCII 约 4，CJK 约 1
_CONTEXT_USABLE_RATIO = 0.7          # 余量留给本轮输出与静态前缀
_MAX_CONTEXT_CHARS = 400_000         # 大窗口模型的实用上限：百万窗口塞满纯属烧钱


def _context_char_budget(model: str) -> int:
    """按模型上下文窗口推算字符预算。

    原先对所有模型写死 40 万字符。对 Claude 那种百万窗口是合理上限，但目录里还有
    131k 窗口的 qwen、200k 的 glm——静态前缀就占掉 6 万字符，
    40 万的阈值对它们等于毫无保护，超窗只会以 API 报错收场。
    """
    derived = _model_caps(model).context_window * _CONTEXT_USABLE_RATIO * _CHARS_PER_TOKEN
    return int(min(derived, _MAX_CONTEXT_CHARS))


def _summarize_tool_json(content: str) -> str:
    """压缩大体积工具结果 JSON：保留标量，列表/字典折叠为数量。"""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content[:800] + f"…（已截断，原始 {len(content)} 字符）"
    if not isinstance(data, dict):
        return content[:800] + f"…（已截断，原始 {len(content)} 字符）"

    summary: dict[str, Any] = {"_compacted": True}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value if not isinstance(value, str) or len(value) <= 300 else value[:300] + "…"
        elif isinstance(value, list):
            summary[key] = f"<list[{len(value)}] 已压缩>"
        elif isinstance(value, dict):
            summary[key] = f"<dict[{len(value)}键] 已压缩>"
    summary["_note"] = "此为历史工具结果摘要；如需完整数据请重新调用该工具。"
    return json.dumps(summary, ensure_ascii=False)


_INTERRUPTED_TOOL_RESULT = '{"status": "interrupted", "note": "该工具调用被用户中止，结果未知"}'


def _expand_history_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把前端历史里的 toolCalls 还原成 assistant.tool_calls + tool 消息对。

    前端只发 role/content 时，纯工具回合会退化成 content 为空的 assistant 消息：
    模型看不到自己上一轮跑过什么工具，且空 content 消息被部分厂商判为非法输入。
    还原成原生形态后，历史工具结果也一并落进 _compact_tool_messages 的压缩预算。
    """
    expanded: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            expanded.append(msg)
            continue

        content = msg.get("content")
        raw_calls = msg.get("toolCalls") or msg.get("tool_calls") or []
        calls: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        for idx, call in enumerate(raw_calls):
            if not isinstance(call, dict) or not call.get("tool"):
                continue
            # id 只需在本次请求内唯一；前端的 nanoid 可能因重放历史而重复
            call_id = f"hist_{len(expanded)}_{idx}"
            calls.append({
                "id": call_id,
                "type": "function",
                "function": {"name": str(call["tool"]), "arguments": str(call.get("args") or "{}")},
            })
            result = call.get("result")
            results.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": str(call["tool"]),
                "content": json.dumps(result, ensure_ascii=False) if result is not None else _INTERRUPTED_TOOL_RESULT,
            })

        if calls:
            expanded.append({"role": "assistant", "content": content or None, "tool_calls": calls})
            expanded.extend(results)
        elif content:
            expanded.append({"role": "assistant", "content": content})
    return expanded


_OLD_SCREENSHOT_PLACEHOLDER = "[历史截图已移除以控制上下文，如需查看请重新调用 inspect_screenshot]"


_DROPPED_HISTORY_MARK = "[上下文超限，已丢弃最早的"

_KEPT_CONSTRAINT_MARK = "【用户此前提出的硬性要求】"

# 用户的约束通常只说一次，且几乎总在会话最早那几轮——正好是超预算时最先被丢掉的部分。
# 整轮丢弃后模型会重新按自己的默认做法来，用户只能再说一遍，且往往察觉不到是上下文丢了。
_CONSTRAINT_MARKERS = (
    "必须", "一定要", "务必", "不要", "不能", "别再", "禁止", "只能", "只用", "记住",
    "注意", "始终", "每次", "千万", "不许", "不可以", "改成", "改为", "换成",
)
_MAX_KEPT_CONSTRAINTS = 8
_MAX_CONSTRAINT_CHARS = 120
_SENTENCE_SPLIT_RE = re.compile(r"[。！\n；;]+")


def _extract_user_constraints(text: str) -> list[str]:
    """从用户原话里摘出带约束语气的句子。

    只取**原句**不做改写：改写过的约束就是模型自己的话，没有任何东西能校验它，
    而这一层的全部价值恰恰在于它来自用户而非模型。
    """
    found: list[str] = []
    for raw in _SENTENCE_SPLIT_RE.split(text or ""):
        s = raw.strip()
        if not s or len(s) > _MAX_CONSTRAINT_CHARS:
            continue
        if any(marker in s for marker in _CONSTRAINT_MARKERS):
            found.append(s)
    return found


def _total_content_chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages)


def _drop_oldest_turns(messages: list[dict[str, Any]], protect_prefix: int, budget: int) -> int:
    """丢弃最老的完整对话轮次，返回丢弃条数。

    压缩是单调的——所有 tool 消息都压过一遍后就再也缩不动了，此时若仍超预算，
    唯一的出路是整轮丢弃，否则下一轮直接撞模型窗口报错。
    """
    prefix_chars = _total_content_chars(messages[:protect_prefix])
    if prefix_chars > budget:
        # 丢历史救不了：提示词本身就超预算。给出可行动的诊断，而不是把历史清空了事
        logger.warning(
            "静态前缀 %s 字符已超出预算 %s，该模型窗口对当前提示词过小", prefix_chars, budget
        )

    dropped = 0
    kept_constraints: list[str] = []
    while _total_content_chars(messages) > budget:
        # 只在 user 消息处切：保证不会留下没有 assistant.tool_calls 配对的 tool 消息，
        # 也保证最后一轮（没有后继 user）永远留着
        cut = next(
            (i for i in range(protect_prefix + 1, len(messages)) if messages[i].get("role") == "user"),
            None,
        )
        if cut is None:
            break
        # 丢之前先把要求捞出来：这一段里可能有上一次丢弃时留下的摘要，它同样会被删掉
        for doomed in messages[protect_prefix:cut]:
            content = str(doomed.get("content") or "")
            if doomed.get("role") == "user":
                kept_constraints.extend(_extract_user_constraints(content))
            elif content.startswith(_KEPT_CONSTRAINT_MARK):
                kept_constraints.extend(
                    line.lstrip("- ").strip() for line in content.splitlines()[1:] if line.strip()
                )
        del messages[protect_prefix:cut]
        dropped += cut - protect_prefix

    if kept_constraints:
        deduped = list(dict.fromkeys(kept_constraints))[-_MAX_KEPT_CONSTRAINTS:]
        messages.insert(protect_prefix, {
            "role": "system",
            "content": (
                f"{_KEPT_CONSTRAINT_MARK}以下是用户在已丢弃的早期对话里的原话，"
                "现在依然有效，不要因为看不到上文就退回默认做法：\n"
                + "\n".join(f"- {c}" for c in deduped)
            ),
        })

    if dropped:
        note = {
            "role": "system",
            "content": f"{_DROPPED_HISTORY_MARK} {dropped} 条历史消息；如需早期细节请重新调用对应工具]",
        }
        # 同一次会话可能反复触发，替换旧提示而不是层层叠加
        if protect_prefix < len(messages) and str(
            messages[protect_prefix].get("content") or ""
        ).startswith(_DROPPED_HISTORY_MARK):
            messages[protect_prefix] = note
        else:
            messages.insert(protect_prefix, note)
        logger.warning("上下文超预算 %s 字符，已丢弃最早 %s 条历史消息", budget, dropped)
    return dropped


def _compact_tool_messages(
    messages: list[dict[str, Any]], budget: int = _DEFAULT_CONTEXT_WINDOW, protect_prefix: int = 0
) -> None:
    """原地压缩较旧的超大 tool 消息，最近几条保留完整内容；压不动仍超预算则整轮丢弃。"""
    tool_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "tool" and isinstance(m.get("content"), str)
    ]
    if tool_indices:
        total_chars = _total_content_chars(messages)
        keep_full = 1 if total_chars > budget else _KEEP_FULL_TOOL_RESULTS
        for i in tool_indices[:-keep_full] if keep_full else tool_indices:
            content = messages[i]["content"]
            if len(content) > _TOOL_COMPACT_THRESHOLD and _COMPACTED_MARK not in content:
                messages[i]["content"] = _summarize_tool_json(content)

    # 截图 vision 消息单张就有几十万字符 base64，且永不因上文压缩而缩小；
    # 只保留最新一张，更早的替换为文本占位，防止多截图会话上下文只增不减。
    image_indices = [
        i for i, m in enumerate(messages)
        if isinstance(m.get("content"), list)
        and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
    ]
    for i in image_indices[:-1]:
        texts = [
            str(p.get("text") or "")
            for p in messages[i]["content"]
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        messages[i]["content"] = ("\n".join(t for t in texts if t) or "") + "\n" + _OLD_SCREENSHOT_PLACEHOLDER

    _drop_oldest_turns(messages, protect_prefix, budget)


_ELIDE_MIN_CHARS = 2_000  # 小结果重发比指回去更省，指针本身也要占字符


def _elide_repeated_result(
    tool_name: str, arguments: str, result: Any, seen: dict[tuple[str, str], str]
) -> str:
    """同参数同结果的工具调用只送一次全文，之后送一句指回原文的话。

    inspect_page 一次一万七千字符，同一页反复探是常态。这里是「已执行、逐字比对后确认相同」
    才折叠，不是跳过调用——页面被点击改变过就不会相等，也就不会折叠，不存在读到旧状态的风险。
    """
    payload = json.dumps(result, ensure_ascii=False)
    key = (tool_name, arguments)
    if seen.get(key) == payload and len(payload) >= _ELIDE_MIN_CHARS:
        return json.dumps({
            "_unchanged": True,
            "message": f"本次 {tool_name} 的返回与上一次同参数调用逐字相同，内容见上文，未重复输出。",
        }, ensure_ascii=False)
    seen[key] = payload
    return payload


def _stable_prefix_end(messages: list[dict[str, Any]]) -> int:
    """返回第一条「后续轮次还可能被改写」的消息下标，其之前的内容逐字不变。

    压缩与截图占位都是一次性的（`_COMPACTED_MARK` / 内容已不是 image_url 就不再改），
    所以改写前沿只会前进不会回头；前沿之前是可缓存的稳定前缀。
    """
    tool_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "tool" and isinstance(m.get("content"), str)
    ]
    end = tool_indices[-_KEEP_FULL_TOOL_RESULTS] if len(tool_indices) > _KEEP_FULL_TOOL_RESULTS else 0
    image_indices = [
        i for i, m in enumerate(messages)
        if isinstance(m.get("content"), list)
        and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
    ]
    if image_indices:
        # 最新一张截图会在下一张到来时被替换成占位符，不能划进稳定区
        end = min(end, image_indices[-1])
    return max(end, 0)


def _mark_history_cache_anchor(messages: list[dict[str, Any]], model: str, relayed: bool) -> None:
    """在稳定前缀的末尾打第三个缓存断点，让历史对话也走缓存读。

    system 与 few-shot 的断点只覆盖静态前缀；真正随轮次膨胀的是工具结果，28 轮能到十万字符，
    没有断点就每轮原价重发。断点必须打在改写前沿之前，否则一次改写让整段缓存作废。
    锚点打在 tool 消息上：litellm 只对 role=tool 读取消息顶层的 cache_control。
    """
    if relayed or not _model_caps(model).supports_cache_control:
        return
    # Anthropic 断点上限 4 个，system/few-shot 已占 2 个，旧锚点必须先撤
    for message in messages:
        if message.get("role") == "tool":
            message.pop("cache_control", None)
    for index in range(_stable_prefix_end(messages) - 1, -1, -1):
        if messages[index].get("role") == "tool":
            messages[index]["cache_control"] = {"type": "ephemeral"}
            return
