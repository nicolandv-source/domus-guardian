from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from app.main import run_debounce_worker, run_notification_retry_worker


class FlakyDeviceService:
    def __init__(self) -> None:
        self.calls = 0
        self.recovered = asyncio.Event()

    def flush_debounce(self) -> list[object]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient database error")
        self.recovered.set()
        return []


class FlakyNotificationEngine:
    def __init__(self) -> None:
        self.calls = 0
        self.recovered = asyncio.Event()

    async def retry_failed(self) -> int:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient Home Assistant error")
        self.recovered.set()
        return 0


@pytest.mark.asyncio
async def test_debounce_worker_survives_transient_error() -> None:
    service = FlakyDeviceService()
    task = asyncio.create_task(run_debounce_worker(service, interval_seconds=0))

    await asyncio.wait_for(service.recovered.wait(), timeout=1)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert service.calls >= 2


@pytest.mark.asyncio
async def test_notification_retry_worker_survives_transient_error() -> None:
    engine = FlakyNotificationEngine()
    task = asyncio.create_task(
        run_notification_retry_worker(engine, interval_seconds=0)
    )

    await asyncio.wait_for(engine.recovered.wait(), timeout=1)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert engine.calls >= 2
