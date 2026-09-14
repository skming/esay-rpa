"""按四个维度把一轮 evals 结果与基线逐案例对齐。

数据正确性 / 契约验收 / 模型收口 / 成本各记各的：数据逐字正确但契约判失败的案例，
在「数据正确性」计入、在「契约验收」与「模型收口」照样计失败，不得合并成一个通过数。

用法：uv run python evals/compare_baseline.py <new_report.json> [baseline.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BASELINE = Path.home() / ".easy-rpa/eval-baselines/20260913-stage5-gpt55-10cases.json"


def _by_case(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["case"]: item for item in report.get("results") or []}


def _verdict_rows(item: dict[str, Any]) -> tuple[int, int]:
    """(逐字相同的变体数, 能逐字比较的变体数)。got/expected 相同才算数据正确，与 passed 无关。

    运行报错的变体两个键都不写，用 .get 取会拿到 None == None——把「一行数据都没产出」
    记成逐字一致，正是本轮要量的那类误判。走 readback 判据的案例本来就没有这两个键，
    分母也要排除：判据不适用与判错混在一起，这一维的数就读不出意思。
    """
    verdicts = item.get("verdicts") or []
    comparable = [v for v in verdicts if "got" in v and "expected" in v]
    errored = [v for v in verdicts if not v.get("passed") and "got" not in v]
    same = sum(1 for v in comparable if v["got"] == v["expected"])
    # 报错变体没有 got 可比，但它确实没产出数据，必须留在分母里当失败计
    return same, len(comparable) + len(errored)


def _totals(items: list[dict[str, Any]]) -> dict[str, int]:
    metrics = [item.get("metrics") or {} for item in items]
    return {
        "prompt": sum(m.get("prompt_tokens", 0) for m in metrics),
        "completion": sum(m.get("completion_tokens", 0) for m in metrics),
        "cached": sum(m.get("cached_tokens", 0) for m in metrics),
        "tool_calls": sum(m.get("tool_calls", 0) for m in metrics),
        "duplicate": sum(m.get("duplicate_calls", 0) for m in metrics),
        "blocked": sum(m.get("blocked_calls", 0) for m in metrics),
    }


def main() -> int:
    new = json.loads(Path(sys.argv[1]).read_text())
    base = json.loads(Path(sys.argv[2] if len(sys.argv) > 2 else BASELINE).read_text())
    new_cases, base_cases = _by_case(new), _by_case(base)
    shared = [c for c in new_cases if c in base_cases]

    print(f"{'case':<28}{'数据(变体一致)':<18}{'契约 replay':<16}{'模型收口 passed':<18}")
    for case in shared:
        n, b = new_cases[case], base_cases[case]
        ns, nt = _verdict_rows(n)
        bs, bt = _verdict_rows(b)
        data = "判据不适用" if (nt == 0 and bt == 0) else f"{bs}/{bt} → {ns}/{nt}"
        replay = f"{b.get('replay_passed')} → {n.get('replay_passed')}"
        closed = f"{b.get('passed')} → {n.get('passed')}"
        print(f"{case:<28}{data:<18}{replay:<16}{closed:<18}")

    nl = [new_cases[c] for c in shared]
    bl = [base_cases[c] for c in shared]
    for label, items in (("基线", bl), ("本轮", nl)):
        same = sum(_verdict_rows(i)[0] for i in items)
        total = sum(_verdict_rows(i)[1] for i in items)
        t = _totals(items)
        obs = [i.get("page_observation") or {} for i in items]
        print(
            f"\n{label}: 数据 {same}/{total} 变体逐字一致"
            f" | 契约 replay {sum(1 for i in items if i.get('replay_passed'))}/{len(items)}"
            f" | 收口 {sum(1 for i in items if i.get('passed'))}/{len(items)}"
            f" | 观察 {sum(o.get('target_ready', 0) for o in obs)}/{sum(o.get('observations', 0) for o in obs)}"
            f"\n      prompt {t['prompt']:,} completion {t['completion']:,} cached {t['cached']:,}"
            f" | 工具调用 {t['tool_calls']} 重复 {t['duplicate']} 被拦 {t['blocked']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
