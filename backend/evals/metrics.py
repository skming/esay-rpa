"""评测的行为指标。

断言只回答「这一次对不对」。用户报的问题是「大量重复审查创建修复」——那是一个分布，
不是单次对错：一个场景可以每条断言都通过，却用了 18 轮、把同一个工具调了 5 次。
断言看不见这件事，所以过去每一次「优化提示词/加护栏」都无法证伪。

这里只做记账，不做判定。阈值要等实测基线出来再定，先把数拿到手。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.services.ai_guards import call_fingerprint


@dataclass
class RunMetrics:
    """一次场景运行的代价与打转程度。"""

    rounds: int = 0
    max_rounds: int = 0
    tool_calls: int = 0
    # 同工具 + 同参数的第 2 次及以后。这是「重复审查」的直接度量：
    # 模型手上已经有答案却又问了一遍，每一次的真实成本是一整轮（生成 + 全上下文重发）。
    duplicate_calls: int = 0
    blocked_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    guard_hits: list[str] = field(default_factory=list)
    # 每个工具被调了几次，按次数降序。定位是哪个工具在被复读。
    call_histogram: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def duplicate_rate(self) -> float:
        return self.duplicate_calls / self.tool_calls if self.tool_calls else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rounds": self.rounds,
            "max_rounds": self.max_rounds,
            "tool_calls": self.tool_calls,
            "duplicate_calls": self.duplicate_calls,
            "blocked_calls": self.blocked_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "guard_hits": list(self.guard_hits),
            "call_histogram": dict(self.call_histogram),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> RunMetrics:
        if not isinstance(raw, dict):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


def count_duplicate_calls(calls: list[tuple[str, Any]]) -> int:
    """同工具同参数的重复次数。

    与护栏共用 `call_fingerprint`：两边算法不同的话，指标显示的「重复」和护栏拦的
    「重复」就不是一件事，调参会调到错的地方去。
    """
    seen: set[str] = set()
    duplicates = 0
    for name, args in calls:
        key = call_fingerprint(name, args if isinstance(args, dict) else {})
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def collect_run_metrics(
    calls: list[tuple[str, Any]],
    usage: dict[str, Any] | None,
    guard_hits: list[str],
) -> RunMetrics:
    """把一次运行的调用序列与最后一份 usage 快照合成指标。

    usage 取最后一份而不是累加：编排层的 `_SessionMeter` 本身已经是累计值。
    """
    usage = usage or {}
    histogram = Counter(name for name, _ in calls)
    return RunMetrics(
        rounds=int(usage.get("rounds") or 0),
        max_rounds=int(usage.get("max_rounds") or 0),
        tool_calls=len(calls),
        duplicate_calls=count_duplicate_calls(calls),
        blocked_calls=int(usage.get("blocked_calls") or 0),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        cached_tokens=int(usage.get("cached_tokens") or 0),
        guard_hits=list(guard_hits),
        call_histogram=dict(histogram.most_common()),
    )


@dataclass
class MetricsSummary:
    """一个场景 N 次运行的汇总。取均值而不是最好那次：打转是概率现象。"""

    runs: int
    avg_rounds: float
    avg_tool_calls: float
    avg_duplicate_calls: float
    duplicate_rate: float
    avg_total_tokens: float
    cache_hit_rate: float
    top_repeated: list[tuple[str, int]]
    guard_hits: dict[str, int]


def summarize(runs: list[RunMetrics]) -> MetricsSummary:
    if not runs:
        return MetricsSummary(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, [], {})
    n = len(runs)
    total_calls = sum(r.tool_calls for r in runs)
    total_dupes = sum(r.duplicate_calls for r in runs)
    total_prompt = sum(r.prompt_tokens for r in runs)
    repeated: Counter[str] = Counter()
    for run in runs:
        for name, count in run.call_histogram.items():
            if count > 1:
                repeated[name] += count - 1
    guards: Counter[str] = Counter()
    for run in runs:
        guards.update(run.guard_hits)
    return MetricsSummary(
        runs=n,
        avg_rounds=sum(r.rounds for r in runs) / n,
        avg_tool_calls=total_calls / n,
        avg_duplicate_calls=total_dupes / n,
        duplicate_rate=total_dupes / total_calls if total_calls else 0.0,
        avg_total_tokens=sum(r.total_tokens for r in runs) / n,
        cache_hit_rate=sum(r.cached_tokens for r in runs) / total_prompt if total_prompt else 0.0,
        top_repeated=repeated.most_common(3),
        guard_hits=dict(guards.most_common()),
    )


def format_summary_table(per_scenario: dict[str, MetricsSummary]) -> str:
    """一张纯文本表。指标要能贴进 PR 和 issue 里对比,所以不用富格式。"""
    if not per_scenario:
        return ""
    header = f"{'场景':<38}{'轮数':>6}{'调用':>6}{'重复':>6}{'重复率':>8}{'tokens':>10}{'缓存':>8}"
    lines = [header, "-" * 82]
    for name, s in per_scenario.items():
        lines.append(
            f"{name:<38}{s.avg_rounds:>6.1f}{s.avg_tool_calls:>6.1f}"
            f"{s.avg_duplicate_calls:>6.1f}{s.duplicate_rate:>7.0%}"
            f"{s.avg_total_tokens:>10.0f}{s.cache_hit_rate:>7.0%}"
        )
    worst = [(n, s) for n, s in per_scenario.items() if s.top_repeated]
    if worst:
        lines.append("")
        lines.append("被复读的工具（超出首次的次数）：")
        for name, s in worst:
            detail = "、".join(f"{tool}+{extra}" for tool, extra in s.top_repeated)
            lines.append(f"  {name}: {detail}")
    all_guards: Counter[str] = Counter()
    for s in per_scenario.values():
        all_guards.update(s.guard_hits)
    if all_guards:
        lines.append("")
        lines.append("护栏触发分布：" + "、".join(f"{g}×{c}" for g, c in all_guards.most_common()))
    return "\n".join(lines)
