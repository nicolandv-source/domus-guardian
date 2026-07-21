from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.event_bus import EventBus
from app.dto import StateChangedDTO
from app.services.device_service import DeviceService


logger = logging.getLogger(__name__)


class HomeAssistantAdapter:
    def __init__(self, event_bus: EventBus, device_service: DeviceService) -> None:
        self._event_bus = event_bus
        self._device_service = device_service

    def subscribe(self) -> None:
        self._event_bus.subscribe("state_changed", self.handle_state_changed)
        self._event_bus.subscribe(
            "entity_registry_loaded",
            self.handle_entity_registry_loaded,
        )

    def unsubscribe(self) -> None:
        self._event_bus.unsubscribe("state_changed", self.handle_state_changed)
        self._event_bus.unsubscribe(
            "entity_registry_loaded",
            self.handle_entity_registry_loaded,
        )

    def handle_entity_registry_loaded(self, payload: dict[str, Any]) -> None:
        for entry in payload.get("entries", []):
            self._device_service.register_entity_mapping(
                entry.get("entity_id", ""),
                entry.get("device_id"),
            )

    def handle_state_changed(self, event: dict[str, Any]) -> None:
        dto = self.to_dto(event)
        if dto is None:
            return
        self._device_service.handle_state_changed(dto)

    @staticmethod
    def to_dto(event: dict[str, Any]) -> StateChangedDTO | None:
        data = event.get("data") or {}
        entity_id = data.get("entity_id")
        new_state = data.get("new_state")
        old_state = data.get("old_state")
        if not entity_id or not isinstance(new_state, dict):
            logger.debug("Evento state_changed ignorato: payload incompleto")
            return None

        state = new_state.get("state")
        if state is None:
            return None
        attributes = new_state.get("attributes") or {}
        fired = event.get("time_fired") or new_state.get("last_updated")
        time_fired = HomeAssistantAdapter._parse_datetime(fired)

        return StateChangedDTO(
            entity_id=entity_id,
            state=str(state),
            old_state=(old_state or {}).get("state")
            if isinstance(old_state, dict)
            else None,
            device_id=data.get("device_id")
            or attributes.get("device_id")
            or attributes.get("ha_device_id"),
            domain=entity_id.partition(".")[0],
            friendly_name=attributes.get("friendly_name"),
            attributes=dict(attributes),
            time_fired=time_fired,
        )

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return datetime.now(timezone.utc)
