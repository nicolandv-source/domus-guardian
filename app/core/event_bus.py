from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any


EventHandler = Callable[[dict[str, Any]], None]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventBusMetrics:
    published_events: int
    handler_failures: int
    pending_handlers: int
    last_published_at: datetime | None


class EventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[EventHandler]] = defaultdict(list)
        self._published_events = 0
        self._handler_failures = 0
        self._pending_handlers = 0
        self._last_published_at: datetime | None = None

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        if handler not in self._listeners[event_type]:
            self._listeners[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        listeners = self._listeners.get(event_type, [])
        if handler in listeners:
            listeners.remove(handler)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        self._published_events += 1
        self._last_published_at = datetime.now(timezone.utc)
        for handler in tuple(self._listeners.get(event_type, ())):
            self._pending_handlers += 1
            try:
                handler(payload)
            except Exception:
                self._handler_failures += 1
                logger.exception("event_bus.handler_error event_type=%s", event_type)
            finally:
                self._pending_handlers -= 1

    def metrics(self) -> EventBusMetrics:
        """Return lightweight diagnostics; the bus intentionally has no queue."""
        return EventBusMetrics(
            published_events=self._published_events,
            handler_failures=self._handler_failures,
            pending_handlers=self._pending_handlers,
            last_published_at=self._last_published_at,
        )
