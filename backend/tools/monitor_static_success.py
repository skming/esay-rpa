from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class MonitorRecord:
    started_at: str
    finished_at: str
    ok: bool
    status: str
    latency_ms: float
    task_id: str | None
    result_count: int
    error: str | None


@dataclass(frozen=True)
class MonitorSummary:
    target_url: str
    selector: str
    cycles: int
    success_count: int
    failure_count: int
    success_rate: float
    threshold: float
    passed: bool
    output: str


async def main() -> None:
    args = parse_args()
    summary = await run_monitor(args)
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    if not summary.passed:
        raise SystemExit(1)


async def run_monitor(args: argparse.Namespace) -> MonitorSummary:
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.reset_output and output.exists():
        output.unlink()

    deadline = datetime.now(UTC) + timedelta(seconds=args.duration_seconds) if args.duration_seconds is not None else None
    records: list[MonitorRecord] = []

    async with managed_client(args) as client:
        cycle = 0
        while cycle < args.cycles and (deadline is None or datetime.now(UTC) < deadline):
            record = await run_cycle(client=client, args=args)
            records.append(record)
            append_jsonl(output, asdict(record))
            cycle += 1
            if cycle < args.cycles and args.interval_seconds > 0:
                await asyncio.sleep(args.interval_seconds)

    success_count = sum(1 for record in records if record.ok)
    failure_count = len(records) - success_count
    success_rate = round(success_count / len(records), 4) if records else 0.0
    return MonitorSummary(
        target_url=args.target_url,
        selector=args.selector,
        cycles=len(records),
        success_count=success_count,
        failure_count=failure_count,
        success_rate=success_rate,
        threshold=args.success_threshold,
        passed=success_rate >= args.success_threshold,
        output=str(output),
    )


async def run_cycle(*, client: AsyncClient, args: argparse.Namespace) -> MonitorRecord:
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    task_id: str | None = None
    status = "error"
    result_count = 0
    error: str | None = None

    try:
        create_response = await client.post(
            "/api/tasks",
            json={
                "flowName": "books.toscrape.com 静态页面监控",
                "targetUrl": args.target_url,
                "selector": args.selector,
                "fetcher": "static",
                "extractMode": args.extract_mode,
                "attribute": args.attribute,
                "timeoutMs": args.task_timeout_ms,
            },
        )
        create_response.raise_for_status()
        task_id = create_response.json()["taskId"]
        snapshot = await poll_task(client=client, task_id=task_id, timeout_seconds=args.task_timeout_ms / 1000 + 5)
        status = snapshot["status"]
        result = snapshot.get("result")
        if isinstance(result, dict):
            result_count = int(result.get("count") or 0)
        error = snapshot.get("error")
    except Exception as exc:
        error = str(exc)

    finished_at = datetime.now(UTC)
    ok = status == "success" and result_count >= args.min_count
    return MonitorRecord(
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        ok=ok,
        status=status,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        task_id=task_id,
        result_count=result_count,
        error=error,
    )


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


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="连续监控 books.toscrape.com 静态页面采集成功率")
    parser.add_argument("--base-url", default=None, help="可选，已启动的后端地址；未提供时使用进程内 FastAPI app")
    parser.add_argument("--target-url", default="https://books.toscrape.com/", help="监控目标 URL")
    parser.add_argument("--selector", default=".product_pod h3 a", help="采集 selector")
    parser.add_argument("--extract-mode", choices=["text", "html", "attribute"], default="attribute", help="提取模式，默认 attribute")
    parser.add_argument("--attribute", default="title", help="属性提取模式下读取的属性名，默认 title")
    parser.add_argument("--cycles", type=int, default=10, help="采样轮数；7 天监控可按间隔换算后设置")
    parser.add_argument("--duration-seconds", type=float, default=None, help="可选，总运行秒数，和 cycles 先到者停止")
    parser.add_argument("--interval-seconds", type=float, default=60, help="每轮采样间隔，默认 60 秒")
    parser.add_argument("--task-timeout-ms", type=int, default=30000, help="单个采集任务超时，默认 30 秒")
    parser.add_argument("--http-timeout-seconds", type=float, default=60, help="HTTP 请求超时，默认 60 秒")
    parser.add_argument("--min-count", type=int, default=1, help="单轮成功所需最小结果数，默认 1")
    parser.add_argument("--success-threshold", type=float, default=0.99, help="成功率阈值，默认 0.99")
    parser.add_argument("--output", type=Path, default=Path("storage/monitor/static-success.jsonl"), help="JSONL 输出路径")
    parser.add_argument("--reset-output", action="store_true", help="运行前删除已有输出文件")
    args = parser.parse_args()

    if args.cycles < 1:
        parser.error("--cycles 必须大于等于 1")
    if args.interval_seconds < 0:
        parser.error("--interval-seconds 必须大于等于 0")
    if args.task_timeout_ms < 1000:
        parser.error("--task-timeout-ms 必须大于等于 1000")
    if not 0 <= args.success_threshold <= 1:
        parser.error("--success-threshold 必须在 0 到 1 之间")
    if args.min_count < 0:
        parser.error("--min-count 必须大于等于 0")
    return args


if __name__ == "__main__":
    asyncio.run(main())
