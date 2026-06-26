from __future__ import annotations

import asyncio

import pytest

from app.models.schemas import RunTaskRequest
from app.services.scrapling_runner import ScraplingRunner


async def _noop_log(level: str, message: str, detail: str | None = None) -> None:
    return None


async def test_scrapling_runner_reports_timeout_with_context(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = ScraplingRunner()

    async def timeout_run(request: RunTaskRequest) -> object:
        raise asyncio.TimeoutError()

    monkeypatch.setattr(runner, "_run_async", timeout_run)

    with pytest.raises(RuntimeError) as exc_info:
        await runner.run(
            "task-timeout",
            RunTaskRequest(
                flowName="超时流程",
                targetUrl="https://quotes.toscrape.com/",
                selector=".quote .text::text",
                timeoutMs=1000,
            ),
            _noop_log,
        )

    assert str(exc_info.value) == "Scrapling 采集超时：1000ms · static · https://quotes.toscrape.com/"


async def test_scrapling_runner_reports_empty_exception_with_type(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = ScraplingRunner()

    async def empty_error_run(request: RunTaskRequest) -> object:
        raise ValueError()

    monkeypatch.setattr(runner, "_run_async", empty_error_run)

    with pytest.raises(RuntimeError) as exc_info:
        await runner.run(
            "task-empty-error",
            RunTaskRequest(
                flowName="空错误流程",
                targetUrl="https://quotes.toscrape.com/",
                selector=".quote .text::text",
            ),
            _noop_log,
        )

    assert str(exc_info.value) == "Scrapling 采集失败：ValueError · static · https://quotes.toscrape.com/"
