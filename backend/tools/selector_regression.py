from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from lxml import html

from app.models.schemas import AnalyzeSiteRequest
from app.services.site_analyzer import SiteAnalyzer


@dataclass(frozen=True)
class RegressionCase:
    name: str
    brittle_selector: str
    expected_text: str
    before_html: str
    after_html: str


@dataclass(frozen=True)
class CaseResult:
    name: str
    brittle_selector: str
    recommended_selector: str | None
    brittle_selector_failed_after_hash_change: bool
    recommended_selector_survived: bool
    relocated: bool
    before_matches: int
    after_matches: int
    recommended_after_matches: int
    stability_score: int | None


@dataclass(frozen=True)
class RegressionSummary:
    total_cases: int
    brittle_selector_failure_rate: float
    recommended_selector_survival_rate: float
    relocation_rate: float
    css_in_js_survival_target: float
    adaptive_relocation_target: float
    passed: bool
    results: list[CaseResult]


def main() -> None:
    args = parse_args()
    summary = run_regression(args)
    payload = asdict(summary)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.enforce and not summary.passed:
        raise SystemExit(1)


def run_regression(args: argparse.Namespace) -> RegressionSummary:
    cases = build_cases()
    results = [run_case(case) for case in cases]
    total = len(results)
    brittle_selector_failure_rate = ratio(sum(1 for item in results if item.brittle_selector_failed_after_hash_change), total)
    recommended_selector_survival_rate = ratio(sum(1 for item in results if item.recommended_selector_survived), total)
    relocation_rate = ratio(sum(1 for item in results if item.relocated), total)
    passed = recommended_selector_survival_rate >= args.css_in_js_survival_target and relocation_rate >= args.adaptive_relocation_target
    return RegressionSummary(
        total_cases=total,
        brittle_selector_failure_rate=brittle_selector_failure_rate,
        recommended_selector_survival_rate=recommended_selector_survival_rate,
        relocation_rate=relocation_rate,
        css_in_js_survival_target=args.css_in_js_survival_target,
        adaptive_relocation_target=args.adaptive_relocation_target,
        passed=passed,
        results=results,
    )


def run_case(case: RegressionCase) -> CaseResult:
    before_document = html.fromstring(case.before_html)
    after_document = html.fromstring(case.after_html)
    analyzer = SiteAnalyzer()
    request = AnalyzeSiteRequest(
        targetUrl="https://example.com/",
        selector=case.brittle_selector,
        maxCandidates=8,
    )
    analysis = analyzer.analyze_html(html_text=case.before_html, request=request)
    recommended = first_relocating_selector(
        after_document=after_document,
        candidates=[candidate.selector for candidate in analysis.candidates],
        expected_text=case.expected_text,
    )

    before_matches = matches_text(before_document, case.brittle_selector, case.expected_text)
    after_matches = matches_text(after_document, case.brittle_selector, case.expected_text)
    recommended_after_matches = matches_text(after_document, recommended, case.expected_text) if recommended is not None else 0
    score = next((candidate.stability_score for candidate in analysis.candidates if candidate.selector == recommended), None)

    return CaseResult(
        name=case.name,
        brittle_selector=case.brittle_selector,
        recommended_selector=recommended,
        brittle_selector_failed_after_hash_change=before_matches > 0 and after_matches == 0,
        recommended_selector_survived=recommended_after_matches > 0,
        relocated=recommended_after_matches > 0,
        before_matches=before_matches,
        after_matches=after_matches,
        recommended_after_matches=recommended_after_matches,
        stability_score=score,
    )


def first_relocating_selector(*, after_document, candidates: list[str], expected_text: str) -> str | None:
    for selector in candidates:
        if matches_text(after_document, selector, expected_text) > 0:
            return selector
    return None


def matches_text(document, selector: str | None, expected_text: str) -> int:
    if selector is None:
        return 0
    try:
        elements = document.cssselect(selector)
    except Exception:
        return 0
    return sum(1 for element in elements if expected_text in normalize_text(element))


def normalize_text(element) -> str:
    text_parts = [part.strip() for part in element.itertext() if part.strip()]
    for attr_name in ("value", "aria-label", "title", "name"):
        attr_value = element.get(attr_name)
        if attr_value is not None and str(attr_value).strip():
            text_parts.append(str(attr_value).strip())
    return " ".join(text_parts)


def ratio(value: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round(value / total, 4)


def build_cases() -> list[RegressionCase]:
    return [
        RegressionCase(
            name="订单提交按钮 data-testid",
            brittle_selector=".css-a1b2c3",
            expected_text="提交订单",
            before_html="""
            <html><body>
              <button class="css-a1b2c3 btn-primary" data-testid="submit-order">提交订单</button>
              <span class="css-331abc">待处理</span>
              <span class="css-aa11bb">已完成</span>
            </body></html>
            """,
            after_html="""
            <html><body>
              <button class="css-f9e8d7 btn-primary" data-testid="submit-order">提交订单</button>
              <span class="css-441def">待处理</span>
              <span class="css-bb22cc">已完成</span>
            </body></html>
            """,
        ),
        RegressionCase(
            name="账号输入框 name",
            brittle_selector=".x-9f8e7d6",
            expected_text="账号",
            before_html="""
            <html><body>
              <label class="css-331abc">账号</label>
              <input class="x-9f8e7d6" name="username" value="账号" />
              <span class="css-abc123">提示</span>
              <span class="css-def456">错误</span>
            </body></html>
            """,
            after_html="""
            <html><body>
              <label class="css-7788aa">账号</label>
              <input class="x-1a2b3c4" name="username" value="账号" />
              <span class="css-ffeedd">提示</span>
              <span class="css-aabbcc">错误</span>
            </body></html>
            """,
        ),
        RegressionCase(
            name="筛选器 aria-label",
            brittle_selector=".sc-42beef0",
            expected_text="状态筛选",
            before_html="""
            <html><body>
              <select class="sc-42beef0" aria-label="状态筛选">
                <option>状态筛选</option>
              </select>
              <div class="css-123abc">订单列表</div>
              <div class="css-456def">分页</div>
            </body></html>
            """,
            after_html="""
            <html><body>
              <select class="sc-99cafe1" aria-label="状态筛选">
                <option>状态筛选</option>
              </select>
              <div class="css-789abc">订单列表</div>
              <div class="css-987def">分页</div>
            </body></html>
            """,
        ),
        RegressionCase(
            name="详情链接 data-cy",
            brittle_selector=".lk-7788aa0",
            expected_text="查看详情",
            before_html="""
            <html><body>
              <a class="lk-7788aa0 link" data-cy="order-detail" href="/orders/1001">查看详情</a>
              <span class="css-111aaa">1001</span>
              <span class="css-222bbb">处理中</span>
            </body></html>
            """,
            after_html="""
            <html><body>
              <a class="lk-9911bb0 link" data-cy="order-detail" href="/orders/1001">查看详情</a>
              <span class="css-333ccc">1001</span>
              <span class="css-444ddd">处理中</span>
            </body></html>
            """,
        ),
        RegressionCase(
            name="提示文本稳定 class",
            brittle_selector=".css-abcdef",
            expected_text="风险提示",
            before_html="""
            <html><body>
              <p class="css-abcdef alert-message">风险提示</p>
              <span class="css-111aaa">低</span>
              <span class="css-222bbb">中</span>
            </body></html>
            """,
            after_html="""
            <html><body>
              <p class="css-fedcba alert-message">风险提示</p>
              <span class="css-333ccc">低</span>
              <span class="css-444ddd">中</span>
            </body></html>
            """,
        ),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="模拟 CSS-in-JS 哈希变更后的 selector 存活率与自动重定位回归")
    parser.add_argument("--css-in-js-survival-target", type=float, default=0.85, help="selector 存活率目标，默认 0.85")
    parser.add_argument("--adaptive-relocation-target", type=float, default=0.80, help="自动重定位率目标，默认 0.80")
    parser.add_argument("--json-output", type=Path, default=None, help="可选，将结果写入 JSON 文件")
    parser.add_argument("--no-enforce", action="store_false", dest="enforce", help="只输出指标，不用验收条件决定退出码")
    parser.set_defaults(enforce=True)
    args = parser.parse_args()

    if not 0 <= args.css_in_js_survival_target <= 1:
        parser.error("--css-in-js-survival-target 必须在 0 到 1 之间")
    if not 0 <= args.adaptive_relocation_target <= 1:
        parser.error("--adaptive-relocation-target 必须在 0 到 1 之间")
    return args


if __name__ == "__main__":
    main()
