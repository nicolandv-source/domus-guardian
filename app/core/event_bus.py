from collections import defaultdict
from collections.abc import Callable
from typing import Any


EventHandler = Callable[[dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        if handler not in self._listeners[event_type]:
            self._listeners[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        listeners = self._listeners.get(event_type, [])
        if handler in listeners:
            listeners.remove(handler)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        for handler in tuple(self._listeners.get(event_type, ())):
            handler(payload)
