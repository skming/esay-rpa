from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from httpx import ASGITransport, AsyncClient

from app.models.schemas import RunTaskRequest, ScrapeResult
from app.services.log_broker import LogBroker
from app.services.scheduler_service import ScheduleService
from app.services.task_manager import TaskManager


@dataclass(frozen=True)
class LoadTestSummary:
    task_count: int
    create_concurrency: int
    worker_concurrency: int
    successful_creates: int
    failed_creates: int
    completed_tasks: int
    failed_tasks: int
    timed_out_tasks: int
    create_latency_p50_ms: float
    create_latency_p95_ms: float
    total_elapsed_ms: float
    max_active_count: int
    max_queued_count: int
    cron_samples: int
    cron_max_error_ms: float
    cron_avg_error_ms: float


class SyntheticRunner:
    def __init__(self, sleep_ms: int) -> None:
        if sleep_ms < 0:
            raise ValueError("sleep_ms 必须大于等于 0")
        self._sleep_seconds = sleep_ms / 1000

    async def run(self, task_id: str, request: RunTaskRequest, on_log) -> ScrapeResult:
        await on_log("running", "合成压测任务启动", task_id)
        if self._sleep_seconds > 0:
            await asyncio.sleep(self._sleep_seconds)
        await on_log("success", "合成压测任务完成", request.selector)
        return ScrapeResult(url=str(request.target_url), selector=request.selector, count=1, values=[f"ok:{task_id}"])


async def main() -> None:
    args = parse_args()
    summary = await run_load_test(args)
    payload = asdict(summary)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.enforce and not is_passing(summary=summary, max_cron_error_ms=args.max_cron_error_ms):
        raise SystemExit(1)


async def run_load_test(args: argparse.Namespace) -> LoadTestSummary:
    import app.main as main_module

    original_task_manager = main_module.task_manager
    original_scheduler_service = main_module.scheduler_service
    synthetic_task_manager = TaskManager(
        runner=SyntheticRunner(sleep_ms=args.runner_sleep_ms),
        broker=LogBroker(),
        concurrency=args.worker_concurrency,
    )
    synthetic_scheduler = ScheduleService(task_manager=synthetic_task_manager)

    main_module.task_manager = synthetic_task_manager
    main_module.scheduler_service = synthetic_scheduler
    synthetic_task_manager.start_workers()

    try:
        async with AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://testserver") as client:
            started = time.perf_counter()
            task_ids, create_latencies, failed_creates = await create_tasks(client=client, args=args)
            queue_samples, task_statuses = await wait_for_tasks(client=client, task_ids=task_ids, timeout_seconds=args.timeout_seconds)
            cron_errors = await measure_cron_error(client=client, samples=args.cron_samples, tick_interval_ms=args.cron_tick_interval_ms)
            elapsed_ms = (time.perf_counter() - started) * 1000

        return LoadTestSummary(
            task_count=args.tasks,
            create_concurrency=args.create_concurrency,
            worker_concurrency=args.worker_concurrency,
            successful_creates=len(task_ids),
            failed_creates=failed_creates,
            completed_tasks=sum(1 for status in task_statuses.values() if status == "success"),
            failed_tasks=sum(1 for status in task_statuses.values() if status == "error"),
            timed_out_tasks=sum(1 for status in task_statuses.values() if status not in {"success", "error", "stopped"}),
            create_latency_p50_ms=percentile(create_latencies, 50),
            create_latency_p95_ms=percentile(create_latencies, 95),
            total_elapsed_ms=round(elapsed_ms, 2),
            max_active_count=max((sample["activeCount"] for sample in queue_samples), default=0),
            max_queued_count=max((sample["queuedCount"] for sample in queue_samples), default=0),
            cron_samples=len(cron_errors),
            cron_max_error_ms=max(cron_errors, default=0.0),
            cron_avg_error_ms=round(statistics.fmean(cron_errors), 2) if cron_errors else 0.0,
        )
    finally:
        await synthetic_task_manager.stop_workers()
        main_module.task_manager = original_task_manager
        main_module.scheduler_service = original_scheduler_service


async def create_tasks(*, client: AsyncClient, args: argparse.Namespace) -> tuple[list[str], list[float], int]:
    semaphore = asyncio.Semaphore(args.create_concurrency)
    latencies: list[float] = []
    task_ids: list[str] = []
    failed_count = 0

    async def create_one(index: int) -> None:
        nonlocal failed_count
        payload = {
            "flowName": f"压测流程-{index}",
            "targetUrl": "https://books.toscrape.com/",
            "selector": ".product_pod h3 a::text",
            "fetcher": "static",
            "extractMode": "text",
            "timeoutMs": 30000,
        }
        async with semaphore:
            started = time.perf_counter()
            response = await client.post("/api/tasks", json=payload)
            latencies.append((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            failed_count += 1
            return
        task_ids.append(response.json()["taskId"])

    await asyncio.gather(*(create_one(index) for index in range(args.tasks)))
    return task_ids, latencies, failed_count


async def wait_for_tasks(*, client: AsyncClient, task_ids: list[str], timeout_seconds: float) -> tuple[list[dict[str, Any]], dict[str, str]]:
    deadline = time.perf_counter() + timeout_seconds
    statuses = {task_id: "queued" for task_id in task_ids}
    queue_samples: list[dict[str, Any]] = []

    while time.perf_counter() < deadline:
        queue_response = await client.get("/api/queue")
        if queue_response.status_code == 200:
            queue_samples.append(queue_response.json())

        for task_id in task_ids:
            if statuses[task_id] in {"success", "error", "stopped"}:
                continue
            response = await client.get(f"/api/tasks/{task_id}")
            if response.status_code == 200:
                statuses[task_id] = response.json()["status"]

        if all(status in {"success", "error", "stopped"} for status in statuses.values()):
            break
        await asyncio.sleep(0.05)

    return queue_samples, statuses


async def measure_cron_error(*, client: AsyncClient, samples: int, tick_interval_ms: int) -> list[float]:
    if samples <= 0:
        return []

    response = await client.post(
        "/api/schedules",
        json={
            "name": "Cron 误差压测",
            "cronExpression": "* * * * * *",
            "timezone": "UTC",
            "enabled": True,
            "task": {
                "flowName": "Cron 误差压测",
                "targetUrl": "https://books.toscrape.com/",
                "selector": ".product_pod h3 a::text",
                "timeoutMs": 30000,
            },
        },
    )
    response.raise_for_status()
    schedule = response.json()
    expected_next = parse_datetime(schedule["nextRunAt"])
    errors: list[float] = []
    deadline = time.perf_counter() + max(samples * 3, 10)

    while len(errors) < samples and time.perf_counter() < deadline:
        await asyncio.sleep(max(tick_interval_ms, 1) / 1000)
        tick_response = await client.post("/api/schedules:tick")
        tick_response.raise_for_status()
        triggered = tick_response.json()
        if not triggered:
            continue
        schedule = triggered[0]
        last_run_at = parse_datetime(schedule["lastRunAt"])
        errors.append(round(max((last_run_at - expected_next).total_seconds() * 1000, 0), 2))
        expected_next = parse_datetime(schedule["nextRunAt"])

    return errors


def is_passing(*, summary: LoadTestSummary, max_cron_error_ms: float) -> bool:
    return (
        summary.successful_creates == summary.task_count
        and summary.completed_tasks == summary.task_count
        and summary.failed_creates == 0
        and summary.failed_tasks == 0
        and summary.timed_out_tasks == 0
        and summary.cron_samples > 0
        and summary.cron_max_error_ms <= max_cron_error_ms
    )


def parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def percentile(values: list[float], percentile_value: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(max(round((percentile_value / 100) * (len(ordered) - 1)), 0), len(ordered) - 1)
    return round(ordered[index], 2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Easy RPA 后端任务队列与 Cron 调度压测工具")
    parser.add_argument("--tasks", type=int, default=100, help="并发入队任务数量，默认 100")
    parser.add_argument("--create-concurrency", type=int, default=100, help="创建任务请求并发度，默认 100")
    parser.add_argument("--worker-concurrency", type=int, default=16, help="后端队列 Worker 并发度，默认 16")
    parser.add_argument("--runner-sleep-ms", type=int, default=120, help="Synthetic runner 每个任务模拟耗时，默认 120ms")
    parser.add_argument("--timeout-seconds", type=float, default=30, help="等待任务完成超时时间，默认 30 秒")
    parser.add_argument("--cron-samples", type=int, default=5, help="Cron 调度误差采样次数，默认 5")
    parser.add_argument("--cron-tick-interval-ms", type=int, default=100, help="手动 tick 间隔，默认 100ms")
    parser.add_argument("--json-output", type=Path, default=None, help="可选，将结果写入 JSON 文件")
    parser.add_argument("--max-cron-error-ms", type=float, default=5000, help="验收允许的最大 Cron 误差，默认 5000ms")
    parser.add_argument("--no-enforce", action="store_false", dest="enforce", help="只输出指标，不用验收条件决定退出码")
    parser.set_defaults(enforce=True)
    args = parser.parse_args()

    if args.tasks < 1:
        parser.error("--tasks 必须大于等于 1")
    if args.create_concurrency < 1:
        parser.error("--create-concurrency 必须大于等于 1")
    if args.worker_concurrency < 1:
        parser.error("--worker-concurrency 必须大于等于 1")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds 必须大于 0")
    return args


if __name__ == "__main__":
    asyncio.run(main())
