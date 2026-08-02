from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock

from app.dto import StateChangedDTO


@dataclass(frozen=True)
class GroupedDeviceState:
    device_id: str
    entity_ids: tuple[str, ...]
    is_available: bool
    representative_entity_id: str


@dataclass
class _DeviceGroup:
    entity_states: dict[str, bool] = field(default_factory=dict)
    representative_entity_id: str = ""


class DeviceGrouping:
    """Aggregates Home Assistant entities into an effective physical device."""

    def __init__(self) -> None:
        self._entity_to_device: dict[str, str] = {}
        self._entity_platforms: dict[str, str | None] = {}
        self._groups: dict[str, _DeviceGroup] = {}
        self._lock = RLock()

    def register_entity_mapping(
        self,
        entity_id: str,
        device_id: str | None,
        platform: str | None = None,
    ) -> None:
        if not entity_id or not device_id:
            return
        with self._lock:
            previous_group = self._entity_to_device.get(entity_id)
            self._entity_to_device[entity_id] = device_id
            if platform is not None:
                self._entity_platforms[entity_id] = platform
            if previous_group and previous_group != device_id:
                self._move_entity(entity_id, previous_group, device_id)

    def update(self, dto: StateChangedDTO) -> GroupedDeviceState:
        with self._lock:
            device_id = self._resolve_device_id(dto)
            group = self._groups.setdefault(device_id, _DeviceGroup())
            group.entity_states[dto.entity_id] = dto.is_available
            group.representative_entity_id = dto.entity_id
            return self._snapshot(device_id, group)

    def snapshot(self, device_id: str) -> GroupedDeviceState | None:
        with self._lock:
            group = self._groups.get(device_id)
            return self._snapshot(device_id, group) if group else None

    def snapshot_for_entity(self, entity_id: str) -> GroupedDeviceState | None:
        with self._lock:
            device_id = self._entity_to_device.get(entity_id)
            group = self._groups.get(device_id) if device_id else None
            return self._snapshot(device_id, group) if group and device_id else None

    def all_snapshots(self) -> list[GroupedDeviceState]:
        with self._lock:
            return [
                self._snapshot(device_id, group)
                for device_id, group in sorted(self._groups.items())
            ]

    def is_physical_entity(self, entity_id: str) -> bool:
        """Whether HA's entity registry associated this entity with a device."""
        with self._lock:
            return entity_id in self._entity_to_device

    def platform_for_entity(self, entity_id: str) -> str | None:
        with self._lock:
            return self._entity_platforms.get(entity_id)

    def has_operational_non_dlna_media_player_sibling(self, entity_id: str) -> bool:
        """Return whether a registry sibling can represent this media device."""
        with self._lock:
            device_id = self._entity_to_device.get(entity_id)
            group = self._groups.get(device_id) if device_id else None
            if group is None:
                return False
            return any(
                sibling_id != entity_id
                and sibling_id.startswith("media_player.")
                and self._entity_platforms.get(sibling_id) != "dlna_dmr"
                and is_available
                for sibling_id, is_available in group.entity_states.items()
            )

    def _resolve_device_id(self, dto: StateChangedDTO) -> str:
        device_id = dto.device_id or self._entity_to_device.get(dto.entity_id)
        device_id = device_id or dto.entity_id
        self.register_entity_mapping(dto.entity_id, device_id)
        return device_id

    def _move_entity(
        self,
        entity_id: str,
        previous_group_id: str,
        device_id: str,
    ) -> None:
        previous_group = self._groups.get(previous_group_id)
        if previous_group is None or entity_id not in previous_group.entity_states:
            return
        state = previous_group.entity_states.pop(entity_id)
        if not previous_group.entity_states:
            self._groups.pop(previous_group_id, None)
        new_group = self._groups.setdefault(device_id, _DeviceGroup())
        new_group.entity_states[entity_id] = state
        new_group.representative_entity_id = entity_id

    @staticmethod
    def _snapshot(device_id: str, group: _DeviceGroup) -> GroupedDeviceState:
        # A physical device remains usable if any known entity is usable.
        return GroupedDeviceState(
            device_id=device_id,
            entity_ids=tuple(sorted(group.entity_states)),
            is_available=any(group.entity_states.values()),
            representative_entity_id=group.representative_entity_id,
        )
