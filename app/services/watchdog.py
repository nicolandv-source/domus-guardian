from __future__ import annotations

import asyncio
import logging
import resource
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.event_bus import EventBus
from app.ha.websocket import HomeAssistantWebSocketClient


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchdogSnapshot:
    status: str
    last_check: datetime | None
    issues: tuple[str, ...]
    actions_taken: int
    actions: tuple[str, ...]
    database_latency_ms: float | None
    websocket_connected: bool
    last_websocket_event_at: datetime | None
    memory_mb: float
    active_tasks: int
    event_bus_pending_handlers: int
    event_bus_handler_failures: int
    event_loop_delay_ms: float


class WatchdogService:
    """In-process checks that never restart the Supervisor or expose credentials."""

    def __init__(
        self,
        *,
        database_check: Callable[[], object],
        database_recover: Callable[[], object],
        websocket: HomeAssistantWebSocketClient,
        event_bus: EventBus,
        interval_seconds: int = 60,
        websocket_stale_after: timedelta = timedelta(minutes=10),
        memory_threshold_mb: int = 512,
    ) -> None:
        self._database_check = database_check
        self._database_recover = database_recover
        self._websocket = websocket
        self._event_bus = event_bus
        self._interval_seconds = max(10, min(interval_seconds, 3600))
        self._websocket_stale_after = websocket_stale_after
        self._memory_threshold_mb = max(64, memory_threshold_mb)
        self._snapshot = WatchdogSnapshot(
            status="healthy",
            last_check=None,
            issues=(),
            actions_taken=0,
            actions=(),
            database_latency_ms=None,
            websocket_connected=False,
            last_websocket_event_at=None,
            memory_mb=0.0,
            active_tasks=0,
            event_bus_pending_handlers=0,
            event_bus_handler_failures=0,
            event_loop_delay_ms=0.0,
        )

    def snapshot(self) -> WatchdogSnapshot:
        return self._snapshot

    async def check_once(self, *, loop_delay_seconds: float = 0.0) -> WatchdogSnapshot:
        logger.info("watchdog.check_started")
        now = datetime.now(timezone.utc)
        issues: list[str] = []
        actions: list[str] = []

        database_latency_ms: float | None = None
        started = time.perf_counter()
        try:
            await asyncio.to_thread(self._database_check)
            database_latency_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.debug("watchdog.db_ok latency_ms=%s", database_latency_ms)
        except Exception as exc:
            issues.append("database_unavailable")
            logger.error("watchdog.db_error error=%s", type(exc).__name__)
            await asyncio.to_thread(self._database_recover)
            actions.append("database_pool_reset")

        websocket_status = self._websocket.status()
        websocket_connected = bool(websocket_status["connected"])
        last_event_at = websocket_status["last_event_at"]
        connected_at = websocket_status["connected_at"]
        stale_from = last_event_at or connected_at
        if not websocket_connected:
            issues.append("websocket_disconnected")
            logger.warning("watchdog.websocket_stale reason=disconnected")
        elif (
            isinstance(stale_from, datetime)
            and now - stale_from > self._websocket_stale_after
        ):
            issues.append("websocket_stale")
            logger.warning("watchdog.websocket_stale reason=no_state_changed")
            if await self._websocket.request_reconnect():
                actions.append("websocket_reconnect_requested")
        else:
            logger.debug("watchdog.websocket_ok")

        metrics = self._event_bus.metrics()
        if metrics.pending_handlers:
            issues.append("event_bus_busy")
            logger.warning(
                "watchdog.event_loop_blocked pending_handlers=%s",
                metrics.pending_handlers,
            )
        elif metrics.handler_failures:
            issues.append("event_bus_handler_errors")
            logger.warning(
                "watchdog.event_loop_blocked handler_failures=%s",
                metrics.handler_failures,
            )
        else:
            logger.debug("watchdog.event_loop_ok")

        memory_mb = self._memory_mb()
        if memory_mb >= self._memory_threshold_mb:
            issues.append("memory_high")
            logger.warning(
                "watchdog.memory_warning memory_mb=%.1f threshold_mb=%s",
                memory_mb,
                self._memory_threshold_mb,
            )

        event_loop_delay_ms = round(loop_delay_seconds * 1000, 1)
        if loop_delay_seconds > max(2.0, self._interval_seconds * 0.5):
            issues.append("event_loop_delayed")
            logger.error("watchdog.event_loop_blocked delay_ms=%s", event_loop_delay_ms)

        status = self._status_for(issues)
        if actions:
            logger.info("watchdog.actions_taken actions=%s", ",".join(actions))
        self._snapshot = WatchdogSnapshot(
            status=status,
            last_check=now,
            issues=tuple(issues),
            actions_taken=len(actions),
            actions=tuple(actions),
            database_latency_ms=database_latency_ms,
            websocket_connected=websocket_connected,
            last_websocket_event_at=last_event_at
            if isinstance(last_event_at, datetime)
            else None,
            memory_mb=memory_mb,
            active_tasks=len(asyncio.all_tasks()),
            event_bus_pending_handlers=metrics.pending_handlers,
            event_bus_handler_failures=metrics.handler_failures,
            event_loop_delay_ms=event_loop_delay_ms,
        )
        return self._snapshot

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        expected = loop.time()
        while True:
            delay_seconds = max(0.0, loop.time() - expected)
            await self.check_once(loop_delay_seconds=delay_seconds)
            expected += self._interval_seconds
            await asyncio.sleep(max(0.0, expected - loop.time()))

    @staticmethod
    def _status_for(issues: list[str]) -> str:
        if "database_unavailable" in issues:
            return "critical"
        if issues:
            return "degraded"
        return "healthy"

    @staticmethod
    def _memory_mb() -> float:
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KiB while macOS reports bytes.
        if sys.platform == "darwin":
            return round(value / (1024 * 1024), 1)
        return round(value / 1024, 1)
