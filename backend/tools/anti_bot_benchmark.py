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
from typing import Any, Literal

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from httpx import ASGITransport, AsyncClient


Fetcher = Literal["static", "dynamic", "stealthy"]


@dataclass(frozen=True)
class AntiBotTarget:
    name: str
    target_url: str
    selector: str
    fetcher: Fetcher
    extract_mode: str = "text"
    attribute: str | None = None
    min_count: int = 1
    timeout_ms: int = 60000
    category: str = "anti-bot"


@dataclass(frozen=True)
class AntiBotRecord:
    name: str
    category: str
    attempt: int
    target_url: str
    fetcher: str
    ok: bool
    status: str
    latency_ms: float
    task_id: str | None
    result_count: int
    error: str | None
    started_at: str
    finished_at: str


@dataclass(frozen=True)
class AntiBotSummary:
    targets: int
    attempts_per_target: int
    total_attempts: int
    success_count: int
    failure_count: int
    success_rate: float
    threshold: float
    passed: bool
    output: str
    by_category: dict[str, dict[str, int | float]]
    by_target: dict[str, dict[str, int | float]]
    records: list[AntiBotRecord]


async def main() -> None:
    args = parse_args()
    summary = await run_benchmark(args)
    payload = asdict(summary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.enforce and not summary.passed:
        raise SystemExit(1)


async def run_benchmark(args: argparse.Namespace) -> AntiBotSummary:
    targets = load_targets(args.targets_file)
    async with managed_client(args) as client:
        records: list[AntiBotRecord] = []
        for target in targets:
            for attempt in range(1, args.attempts + 1):
                records.append(await run_target(client=client, target=target, attempt=attempt))
                if args.attempt_interval_seconds > 0 and not (target is targets[-1] and attempt == args.attempts):
                    await asyncio.sleep(args.attempt_interval_seconds)

    success_count = sum(1 for record in records if record.ok)
    failure_count = len(records) - success_count
    success_rate = round(success_count / len(records), 4) if records else 0.0
    return AntiBotSummary(
        targets=len(targets),
        attempts_per_target=args.attempts,
        total_attempts=len(records),
        success_count=success_count,
        failure_count=failure_count,
        success_rate=success_rate,
        threshold=args.success_threshold,
        passed=success_rate >= args.success_threshold,
        output=str(args.output),
        by_category=group_summary(records, key="category"),
        by_target=group_summary(records, key="name"),
        records=records,
    )


def fetch_definition(target: AntiBotTarget) -> dict[str, Any]:
    """TaskManager 只执行 flowDefinition，顶层扁平字段仅被携带，不参与抓取。
    所以 fetcher/extractMode/attribute 必须写进节点：漏一个就落回
    build_request_for_fetch_node 的默认值（fetcher=static），评测出的成功率
    不属于这个 target 声明的抓取方式，而这条偏差在结果里看不出来。"""
    return {
        "nodes": [
            {"id": "start", "type": "start"},
            {
                "id": "fetch",
                "type": "browser.fetch",
                "targetUrl": target.target_url,
                "selector": target.selector,
                "fetcher": target.fetcher,
                "extractMode": target.extract_mode,
                "attribute": target.attribute,
                "adaptive": True,
                "autoSave": False,
                "timeoutMs": target.timeout_ms,
                "outputVariable": "rows",
            },
        ],
        "edges": [{"source": "start", "target": "fetch"}],
    }


async def run_target(*, client: AsyncClient, target: AntiBotTarget, attempt: int) -> AntiBotRecord:
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    task_id: str | None = None
    status = "error"
    result_count = 0
    error: str | None = None

    try:
        response = await client.post(
            "/api/tasks",
            json={
                "flowName": f"反爬评测 · {target.name}",
                "targetUrl": target.target_url,
                "selector": target.selector,
                "fetcher": target.fetcher,
                "extractMode": target.extract_mode,
                "attribute": target.attribute,
                "timeoutMs": target.timeout_ms,
                "adaptive": True,
                "autoSave": False,
                "flowDefinition": fetch_definition(target),
            },
        )
        response.raise_for_status()
        task_id = response.json()["taskId"]
        snapshot = await poll_task(client=client, task_id=task_id, timeout_seconds=target.timeout_ms / 1000 + 10)
        status = snapshot["status"]
        result = snapshot.get("result")
        if isinstance(result, dict):
            result_count = int(result.get("count") or 0)
        error = snapshot.get("error")
    except Exception as exc:
        error = str(exc)

    finished_at = datetime.now(UTC)
    ok = status == "success" and result_count >= target.min_count
    return AntiBotRecord(
        name=target.name,
        category=target.category,
        attempt=attempt,
        target_url=target.target_url,
        fetcher=target.fetcher,
        ok=ok,
        status=status,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        task_id=task_id,
        result_count=result_count,
        error=error,
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
    )


def group_summary(records: list[AntiBotRecord], *, key: Literal["category", "name"]) -> dict[str, dict[str, int | float]]:
    grouped: dict[str, list[AntiBotRecord]] = {}
    for record in records:
        grouped.setdefault(getattr(record, key), []).append(record)
    return {
        group_name: {
            "attempts": len(items),
            "successCount": sum(1 for item in items if item.ok),
            "failureCount": sum(1 for item in items if not item.ok),
            "successRate": round(sum(1 for item in items if item.ok) / len(items), 4) if items else 0.0,
        }
        for group_name, items in sorted(grouped.items())
    }


async def poll_task(*, client: AsyncClient, task_id: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.perf_counter() + timeout_seconds
    last_snapshot: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        response = await client.get(f"/api/tasks/{task_id}")
        response.raise_for_status()
        last_snapshot = response.json()
        if last_snapshot["status"] in {"success", "error", "stopped"}:
            return last_snapshot
        await asyncio.sleep(0.5)
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


def load_targets(path: Path | None) -> list[AntiBotTarget]:
    if path is None:
        return [
            AntiBotTarget(
                name="books smoke",
                category="smoke",
                target_url="https://books.toscrape.com/",
                selector=".product_pod h3 a",
                fetcher="static",
                extract_mode="attribute",
                attribute="title",
                min_count=1,
                timeout_ms=30000,
            )
        ]

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("targets 文件必须是 JSON 数组")
    targets: list[AntiBotTarget] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 个 target 必须是 JSON 对象")
        targets.append(AntiBotTarget(**item))
    return targets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="反爬页面采集成功率评测工具，支持 Cloudflare/DataDome 等真实目标清单")
    parser.add_argument("--base-url", default=None, help="可选，已启动的后端地址；未提供时使用进程内 FastAPI app")
    parser.add_argument("--targets-file", type=Path, default=None, help="真实目标 JSON 文件；未提供时运行 books smoke")
    parser.add_argument("--attempts", type=int, default=3, help="每个目标采样次数，默认 3")
    parser.add_argument("--attempt-interval-seconds", type=float, default=0, help="每次采样之间的等待秒数，默认 0")
    parser.add_argument("--success-threshold", type=float, default=0.90, help="反爬页面成功率阈值，默认 0.90")
    parser.add_argument("--http-timeout-seconds", type=float, default=120, help="HTTP 请求超时，默认 120 秒")
    parser.add_argument("--output", type=Path, default=Path("storage/bench/anti-bot-benchmark.json"), help="JSON 输出路径")
    parser.add_argument("--no-enforce", action="store_false", dest="enforce", help="只输出指标，不用阈值决定退出码")
    parser.set_defaults(enforce=True)
    args = parser.parse_args()

    if not 0 <= args.success_threshold <= 1:
        parser.error("--success-threshold 必须在 0 到 1 之间")
    if args.attempts < 1:
        parser.error("--attempts 必须大于等于 1")
    if args.attempt_interval_seconds < 0:
        parser.error("--attempt-interval-seconds 必须大于等于 0")
    if args.http_timeout_seconds <= 0:
        parser.error("--http-timeout-seconds 必须大于 0")
    return args


if __name__ == "__main__":
    asyncio.run(main())
