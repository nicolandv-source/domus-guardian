from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.event_bus import EventBus
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
