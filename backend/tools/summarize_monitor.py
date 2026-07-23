from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MonitorSummary:
    input: str
    records: int
    success_count: int
    failure_count: int
    success_rate: float
    threshold: float
    passed: bool
    first_started_at: str | None
    last_finished_at: str | None
    duration_hours: float
    expected_min_records: int | None
    enough_records: bool
    expected_min_hours: float | None
    enough_duration: bool


def main() -> None:
    args = parse_args()
    summary = summarize(args)
    payload = asdict(summary)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.enforce and not summary.passed:
        raise SystemExit(1)


def summarize(args: argparse.Namespace) -> MonitorSummary:
    records = read_jsonl(args.input)
    success_count = sum(1 for record in records if bool(record.get("ok")))
    failure_count = len(records) - success_count
    success_rate = round(success_count / len(records), 4) if records else 0.0
    first_started_at = min((str(record.get("started_at")) for record in records if record.get("started_at")), default=None)
    last_finished_at = max((str(record.get("finished_at")) for record in records if record.get("finished_at")), default=None)
    duration_hours = compute_duration_hours(first_started_at, last_finished_at)
    enough_records = args.expected_min_records is None or len(records) >= args.expected_min_records
    enough_duration = args.expected_min_hours is None or duration_hours >= args.expected_min_hours
    passed = success_rate >= args.success_threshold and enough_records and enough_duration
    return MonitorSummary(
        input=str(args.input),
        records=len(records),
        success_count=success_count,
        failure_count=failure_count,
        success_rate=success_rate,
        threshold=args.success_threshold,
        passed=passed,
        first_started_at=first_started_at,
        last_finished_at=last_finished_at,
        duration_hours=duration_hours,
        expected_min_records=args.expected_min_records,
        enough_records=enough_records,
        expected_min_hours=args.expected_min_hours,
        enough_duration=enough_duration,
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"监控文件不存在: {path}")
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_number} 行不是合法 JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"第 {line_number} 行必须是 JSON 对象")
            records.append(payload)
    return records


def compute_duration_hours(started_at: str | None, finished_at: str | None) -> float:
    if started_at is None or finished_at is None:
        return 0.0
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    end = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    return round(max((end - start).total_seconds(), 0) / 3600, 4)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="汇总静态页面监控 JSONL，并按成功率阈值输出验收结果")
    parser.add_argument("--input", type=Path, required=True, help="monitor_static_success.py 产生的 JSONL 文件")
    parser.add_argument("--success-threshold", type=float, default=0.99, help="成功率阈值，默认 0.99")
    parser.add_argument("--expected-min-records", type=int, default=None, help="可选，期望最少记录数；7 天每分钟采样为 10080")
    parser.add_argument("--expected-min-hours", type=float, default=None, help="可选，期望最少覆盖小时数；7 天为 168")
    parser.add_argument("--json-output", type=Path, default=None, help="可选，将汇总结果写入 JSON 文件")
    parser.add_argument("--no-enforce", action="store_false", dest="enforce", help="只输出汇总，不用阈值决定退出码")
    parser.set_defaults(enforce=True)
    args = parser.parse_args()

    if not 0 <= args.success_threshold <= 1:
        parser.error("--success-threshold 必须在 0 到 1 之间")
    if args.expected_min_records is not None and args.expected_min_records < 1:
        parser.error("--expected-min-records 必须大于等于 1")
    if args.expected_min_hours is not None and args.expected_min_hours <= 0:
        parser.error("--expected-min-hours 必须大于 0")
    return args


if __name__ == "__main__":
    main()
