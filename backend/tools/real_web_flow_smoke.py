from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class SmokeCase:
    name: str
    min_count: int
    expected_contains: str
    variable_name: str
    definition: dict[str, object]
    input_variables: list[dict[str, object]]


@dataclass(frozen=True)
class SmokeResult:
    name: str
    ok: bool
    flow_id: str | None
    task_id: str | None
    status: str
    elapsed_ms: float
    result_count: int
    variable_name: str
    variable_count: int
    expected_contains: str
    matched: bool
    sample_values: list[str]
    error: str | None


@dataclass(frozen=True)
class SmokeSummary:
    started_at: str
    finished_at: str
    total: int
    passed: int
    failed: int
    ok: bool
    output: str | None
    results: list[SmokeResult]


async def main() -> None:
    args = parse_args()
    summary = await run_smoke(args)
    payload = asdict(summary)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not summary.ok:
        raise SystemExit(1)


async def run_smoke(args: argparse.Namespace) -> SmokeSummary:
    started_at = datetime.now(UTC)
    cases = build_cases()
    async with managed_client(args) as client:
        results = [await run_case(client=client, case=case, args=args) for case in cases]
    passed = sum(1 for item in results if item.ok)
    summary = SmokeSummary(
        started_at=started_at.isoformat(),
        finished_at=datetime.now(UTC).isoformat(),
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        ok=passed == len(results),
        output=str(args.output) if args.output is not None else None,
        results=results,
    )
    return summary


async def run_case(*, client: AsyncClient, case: SmokeCase, args: argparse.Namespace) -> SmokeResult:
    started = time.perf_counter()
    flow_id: str | None = None
    task_id: str | None = None
    status = "error"
    result_count = 0
    variable_count = 0
    sample_values: list[str] = []
    error: str | None = None

    try:
        flow_response = await client.post(
            "/api/flows",
            json={
                "name": f"真实网页 Smoke · {case.name}",
                "version": "v-smoke",
                "description": "真实网页抓取链路 smoke test",
                "definition": case.definition,
                "inputVariables": case.input_variables,
                "status": "active",
            },
        )
        flow_response.raise_for_status()
        flow_id = flow_response.json()["flowId"]

        run_response = await client.post(
            f"/api/flows/{flow_id}/run",
            json={
                "mode": "run",
                "scope": "full",
                "failureStrategy": "stop",
                "screenshot": args.screenshot,
                "timeoutMs": args.task_timeout_ms,
            },
        )
        run_response.raise_for_status()
        task_id = run_response.json()["taskId"]
        snapshot = await poll_task(client=client, task_id=task_id, timeout_seconds=args.task_timeout_ms / 1000 + args.poll_grace_seconds)
        status = snapshot["status"]
        result = snapshot.get("result")
        if isinstance(result, dict):
            result_count = int(result.get("count") or 0)
        variable_values = await read_variable_values(client=client, task_id=task_id, variable_name=case.variable_name)
        variable_count = len(variable_values)
        sample_values = variable_values[:5]
        error = snapshot.get("error")
    except Exception as exc:
        error = str(exc)

    matched = any(case.expected_contains in value for value in sample_values)
    ok = status == "success" and variable_count >= case.min_count and matched
    return SmokeResult(
        name=case.name,
        ok=ok,
        flow_id=flow_id,
        task_id=task_id,
        status=status,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        result_count=result_count,
        variable_name=case.variable_name,
        variable_count=variable_count,
        expected_contains=case.expected_contains,
        matched=matched,
        sample_values=sample_values,
        error=error,
    )


async def read_variable_values(*, client: AsyncClient, task_id: str, variable_name: str) -> list[str]:
    response = await client.get(f"/api/tasks/{task_id}/variables")
    response.raise_for_status()
    variables = response.json()
    if not isinstance(variables, list):
        return []
    matched = next((item for item in variables if isinstance(item, dict) and item.get("name") == variable_name), None)
    if not isinstance(matched, dict):
        return []
    raw_value = matched.get("value")
    if not isinstance(raw_value, str):
        return []
    try:
        decoded = json.loads(raw_value)
    except json.JSONDecodeError:
        return [raw_value] if raw_value else []
    if isinstance(decoded, list):
        return [stringify_value(value) for value in decoded if stringify_value(value)]
    if isinstance(decoded, dict):
        return [json.dumps(decoded, ensure_ascii=False, sort_keys=True)]
    return [str(decoded)] if decoded is not None else []


def stringify_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


async def poll_task(*, client: AsyncClient, task_id: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.perf_counter() + timeout_seconds
    last_snapshot: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        response = await client.get(f"/api/tasks/{task_id}")
        response.raise_for_status()
        last_snapshot = response.json()
        if last_snapshot["status"] in {"success", "error", "stopped"}:
            return last_snapshot
        await asyncio.sleep(0.25)
    if last_snapshot:
        return last_snapshot
    raise TimeoutError(f"任务 {task_id} 未在 {timeout_seconds:.1f}s 内返回状态")


@asynccontextmanager
async def managed_client(args: argparse.Namespace) -> AsyncIterator[AsyncClient]:
    if args.base_url is not None:
        async with AsyncClient(base_url=args.base_url.rstrip("/"), timeout=args.http_timeout_seconds) as client:
            yield client
        return

    import app.main as main_module

    main_module.task_manager.start_workers()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=main_module.app),
            base_url="http://testserver",
            timeout=args.http_timeout_seconds,
        ) as client:
            yield client
    finally:
        await main_module.task_manager.stop_workers()


def build_cases() -> list[SmokeCase]:
    return [
        SmokeCase(
            name="quotes 静态列表提取",
            min_count=10,
            expected_contains="thinking",
            variable_name="quote_texts",
            input_variables=[],
            definition={
                "nodes": [
                    node("start", "start", "开始"),
                    node("open", "browser.open", "打开 Quotes", targetUrl="https://quotes.toscrape.com/"),
                    node("wait", "browser.wait", "等待 Quote", selector=".quote"),
                    node("extract", "browser.extract", "提取 Quote", selector=".quote .text::text", outputVariable="quote_texts", firstValueVariable="first_quote", countVariable="quote_count"),
                    node("end", "end", "结束"),
                ],
                "edges": chain(["start", "open", "wait", "extract", "end"]),
            },
        ),
        SmokeCase(
            name="Wikipedia 搜索提交提取",
            min_count=1,
            expected_contains="Albert Einstein",
            variable_name="search_heading",
            input_variables=[
                variable("search_keyword", "Albert Einstein"),
                variable("submit_key", "Enter"),
            ],
            definition={
                "nodes": [
                    node("start", "start", "开始"),
                    node("open", "browser.open", "打开 Wikipedia", targetUrl="https://en.wikipedia.org/wiki/Main_Page"),
                    node("fill", "browser.fill", "输入搜索词", selector='input[name="search"]', inputValue="${var.search_keyword}"),
                    node("press", "browser.press", "提交搜索", selector='input[name="search"]', inputValue="${var.submit_key}", outputVariable="submit_key_result"),
                    node("wait", "browser.wait", "等待标题", selector="#firstHeading"),
                    node("extract", "browser.extract", "提取标题", selector="#firstHeading::text", outputVariable="search_heading", firstValueVariable="first_heading", countVariable="heading_count"),
                    node("end", "end", "结束"),
                ],
                "edges": chain(["start", "open", "fill", "press", "wait", "extract", "end"]),
            },
        ),
        SmokeCase(
            name="books 下一页分页提取",
            min_count=40,
            expected_contains="A Light in the Attic",
            variable_name="book_titles",
            input_variables=[],
            definition={
                "nodes": [
                    node("start", "start", "开始"),
                    node("open", "browser.open", "打开 Books", targetUrl="https://books.toscrape.com/catalogue/page-1.html"),
                    node(
                        "paginate",
                        "browser.paginateNext",
                        "翻页提取书名",
                        selector="li.next a",
                        targetSelector=".product_pod h3 a::attr(title)",
                        extractMode="attribute",
                        attribute="title",
                        maxIterations=2,
                        delayMs=300,
                        outputVariable="book_titles",
                        firstValueVariable="first_book_title",
                        countVariable="book_title_count",
                        pageCountVariable="book_page_count",
                    ),
                    node("end", "end", "结束"),
                ],
                "edges": chain(["start", "open", "paginate", "end"]),
            },
        ),
        SmokeCase(
            name="books 列表详情累计提取",
            min_count=3,
            expected_contains="A Light in the Attic",
            variable_name="book_detail_titles",
            input_variables=[],
            definition={
                "nodes": [
                    node("start", "start", "开始"),
                    node("open", "browser.open", "打开 Books", targetUrl="https://books.toscrape.com/catalogue/page-1.html"),
                    node("extract-links", "browser.extract", "提取详情链接", selector=".product_pod h3 a::attr(href)", outputVariable="book_links", firstValueVariable="first_book_link", countVariable="book_link_count"),
                    node("foreach", "control.foreach", "遍历前三个详情链接", itemsVariable="book_links", itemVariable="book_detail_url", indexVariable="book_detail_index", maxIterations=3),
                    node("open-detail", "browser.open", "打开详情页", targetUrl="${var.book_detail_url}"),
                    node("extract-title", "browser.extract", "累计详情标题", selector=".product_main h1::text", outputVariable="last_book_detail_title", appendVariable="book_detail_titles", firstValueVariable="last_book_detail_title", countVariable="last_book_detail_title_count"),
                    node("extract-price", "browser.extract", "累计详情价格", selector=".product_main .price_color::text", outputVariable="last_book_detail_price", appendVariable="book_detail_prices", firstValueVariable="last_book_detail_price", countVariable="last_book_detail_price_count"),
                    node("end", "end", "结束"),
                ],
                "edges": [
                    *chain(["start", "open", "extract-links", "foreach"]),
                    {"source": "foreach", "target": "open-detail", "label": "循环体"},
                    {"source": "open-detail", "target": "extract-title"},
                    {"source": "extract-title", "target": "extract-price"},
                    {"source": "extract-price", "target": "foreach", "label": "loop"},
                    {"source": "foreach", "target": "end", "label": "完成"},
                ],
            },
        ),
    ]


def node(node_id: str, node_type: str, title: str, **kwargs: object) -> dict[str, object]:
    payload: dict[str, object] = {"id": node_id, "type": node_type, "title": title, "timeoutMs": 30_000}
    payload.update(kwargs)
    return payload


def chain(ids: list[str]) -> list[dict[str, str]]:
    return [{"source": source, "target": target} for source, target in zip(ids, ids[1:])]


def variable(name: str, value: str) -> dict[str, object]:
    return {"name": name, "value": value, "type": "String", "scope": "全局", "category": "flow", "sensitive": False}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="真实网页 RPA 流程链路 smoke test")
    parser.add_argument("--base-url", default=None, help="可选，已启动的后端地址；未提供时使用进程内 FastAPI app")
    parser.add_argument("--task-timeout-ms", type=int, default=60_000, help="单个流程运行超时，默认 60 秒")
    parser.add_argument("--poll-grace-seconds", type=float, default=10, help="轮询额外等待秒数，默认 10 秒")
    parser.add_argument("--http-timeout-seconds", type=float, default=90, help="HTTP 请求超时，默认 90 秒")
    parser.add_argument("--screenshot", action="store_true", help="运行时保存浏览器截图")
    parser.add_argument("--output", type=Path, default=Path("storage/smoke/real-web-flow-smoke.json"), help="JSON 输出路径")
    args = parser.parse_args()
    if args.task_timeout_ms < 1000:
        parser.error("--task-timeout-ms 必须大于等于 1000")
    if args.poll_grace_seconds < 0:
        parser.error("--poll-grace-seconds 必须大于等于 0")
    if args.http_timeout_seconds < 1:
        parser.error("--http-timeout-seconds 必须大于等于 1")
    return args


if __name__ == "__main__":
    asyncio.run(main())
