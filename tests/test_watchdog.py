from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.event_bus import EventBus
from app.services import watchdog
from app.services.watchdog import WatchdogService


class FakeWebSocket:
    def __init__(
        self, *, connected: bool, last_event_at: datetime | None = None
    ) -> None:
        self._status = {
            "connected": connected,
            "connected_at": datetime.now(timezone.utc) - timedelta(minutes=20),
            "last_event_at": last_event_at,
            "last_message_at": last_event_at,
        }
        self.reconnects = 0

    def status(self) -> dict[str, object]:
        return self._status

    async def request_reconnect(self) -> bool:
        self.reconnects += 1
        return True


def test_watchdog_memory_metric_is_safe_without_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watchdog, "resource", None)

    assert WatchdogService._memory_mb() == 0.0


@pytest.mark.asyncio
async def test_watchdog_is_healthy_for_fresh_dependencies() -> None:
    service = WatchdogService(
        database_check=lambda: None,
        database_recover=lambda: None,
        websocket=FakeWebSocket(
            connected=True,
            last_event_at=datetime.now(timezone.utc),
        ),
        event_bus=EventBus(),
        memory_threshold_mb=100_000,
    )

    snapshot = await service.check_once()

    assert snapshot.status == "healthy"
    assert snapshot.issues == ()
    assert snapshot.database_latency_ms is not None


@pytest.mark.asyncio
async def test_watchdog_recovers_from_a_past_handler_failure() -> None:
    """A handler failure must not latch the watchdog "degraded" forever.

    Regression test: EventBus.metrics().handler_failures is a lifetime
    total, so gating status on it directly means one past failure keeps
    reporting "degraded" even after handlers start succeeding again.
    """
    event_bus = EventBus()

    def failing_handler(_payload: dict[str, object]) -> None:
        raise RuntimeError("boom")

    event_bus.subscribe("state_changed", failing_handler)
    event_bus.publish("state_changed", {})
    event_bus.unsubscribe("state_changed", failing_handler)

    service = WatchdogService(
        database_check=lambda: None,
        database_recover=lambda: None,
        websocket=FakeWebSocket(
            connected=True,
            last_event_at=datetime.now(timezone.utc),
        ),
        event_bus=event_bus,
        memory_threshold_mb=100_000,
    )

    first = await service.check_once()
    assert first.status == "degraded"
    assert first.issues == ("event_bus_handler_errors",)
    assert first.event_bus_handler_failures == 1

    second = await service.check_once()
    assert second.status == "healthy"
    assert second.issues == ()
    assert second.event_bus_handler_failures == 1


@pytest.mark.asyncio
async def test_watchdog_requests_reconnect_for_stale_websocket() -> None:
    websocket = FakeWebSocket(
        connected=True,
        last_event_at=datetime.now(timezone.utc) - timedelta(minutes=11),
    )
    service = WatchdogService(
        database_check=lambda: None,
        database_recover=lambda: None,
        websocket=websocket,
        event_bus=EventBus(),
        websocket_stale_after=timedelta(minutes=10),
        memory_threshold_mb=100_000,
    )

    snapshot = await service.check_once()

    assert snapshot.status == "degraded"
    assert "websocket_stale" in snapshot.issues
    assert snapshot.actions == ("websocket_reconnect_requested",)
    assert websocket.reconnects == 1


@pytest.mark.asyncio
async def test_watchdog_marks_database_failure_critical_and_resets_pool() -> None:
    resets: list[bool] = []

    def broken_database() -> None:
        raise RuntimeError("database unavailable")

    service = WatchdogService(
        database_check=broken_database,
        database_recover=lambda: resets.append(True),
        websocket=FakeWebSocket(
            connected=True, last_event_at=datetime.now(timezone.utc)
        ),
        event_bus=EventBus(),
        memory_threshold_mb=100_000,
    )

    snapshot = await service.check_once()

    assert snapshot.status == "critical"
    assert "database_unavailable" in snapshot.issues
    assert snapshot.actions == ("database_pool_reset",)
    assert resets == [True]


@pytest.mark.asyncio
async def test_watchdog_recovers_from_transient_database_error_without_blocking_loop() -> None:
    attempts: list[int] = []
    resets: list[bool] = []
    event_loop_progress = asyncio.Event()

    def flaky_database() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("temporary database error")

    async def progress_event_loop() -> None:
        await asyncio.sleep(0)
        event_loop_progress.set()

    service = WatchdogService(
        database_check=flaky_database,
        database_recover=lambda: resets.append(True),
        websocket=FakeWebSocket(
            connected=True, last_event_at=datetime.now(timezone.utc)
        ),
        event_bus=EventBus(),
        memory_threshold_mb=100_000,
        database_retry_attempts=2,
        database_retry_backoff_seconds=0.01,
    )

    progress_task = asyncio.create_task(progress_event_loop())
    snapshot = await service.check_once()
    await progress_task

    assert event_loop_progress.is_set()
    assert attempts == [1, 1]
    assert resets == [True]
    assert snapshot.status == "healthy"
    assert snapshot.actions == ("database_pool_reset", "database_retry_succeeded")


@pytest.mark.asyncio
async def test_watchdog_survives_pool_reset_error_and_retries_later() -> None:
    calls = 0

    def unavailable_database() -> None:
        raise RuntimeError("database unavailable")

    def broken_recovery() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("pool reset failed")

    service = WatchdogService(
        database_check=unavailable_database,
        database_recover=broken_recovery,
        websocket=FakeWebSocket(
            connected=True, last_event_at=datetime.now(timezone.utc)
        ),
        event_bus=EventBus(),
        memory_threshold_mb=100_000,
        database_retry_attempts=2,
        database_retry_backoff_seconds=0,
    )

    snapshot = await service.check_once()

    assert snapshot.status == "critical"
    assert snapshot.actions == ()
    assert calls == 1
