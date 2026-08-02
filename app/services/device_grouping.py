from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from app.dto import StateChangedDTO


_GENERIC_NAME_TOKENS = frozenset(
    {
        "a",
        "camera",
        "chromecast",
        "cucina",
        "da",
        "del",
        "della",
        "di",
        "display",
        "google",
        "hub",
        "hisense",
        "il",
        "in",
        "la",
        "letto",
        "living",
        "lg",
        "media",
        "nel",
        "player",
        "room",
        "salotto",
        "samsung",
        "smart",
        "sony",
        "stanza",
        "the",
        "tv",
        "television",
    }
)


@dataclass(frozen=True)
class GroupedDeviceState:
    device_id: str
    entity_ids: tuple[str, ...]
    is_available: bool
    representative_entity_id: str


@dataclass(frozen=True)
class _DeviceIdentity:
    macs: frozenset[str] = frozenset()
    area_id: str | None = None
    models: frozenset[str] = frozenset()
    names: frozenset[str] = frozenset()


@dataclass
class _DeviceGroup:
    entity_states: dict[str, bool] = field(default_factory=dict)
    representative_entity_id: str = ""


class DeviceGrouping:
    """Aggregate HA entities by physical-device evidence from its registries.

    Entity-registry ``device_id`` values are the starting point, not the final
    identity.  Device-registry MAC addresses create strong clusters.  A
    no-MAC media player can join one of those clusters only through two
    coherent, registry-derived signals; this keeps a Cast/Nest in the same
    room from being treated as a TV merely because of its area or a generic
    name.
    """

    def __init__(self) -> None:
        self._entity_to_device: dict[str, str] = {}
        self._entity_platforms: dict[str, str | None] = {}
        self._device_identities: dict[str, _DeviceIdentity] = {}
        self._cluster_for_device: dict[str, str] = {}
        self._entity_states: dict[str, bool] = {}
        self._entity_updated_order: dict[str, int] = {}
        self._update_order = 0
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
            self._entity_to_device[entity_id] = device_id
            if platform is not None:
                self._entity_platforms[entity_id] = platform
            self._rebuild_clusters()

    def register_device_registry_entry(self, entry: Mapping[str, Any]) -> None:
        """Register device identity material returned by HA's device registry."""
        device_id = entry.get("id")
        if not isinstance(device_id, str) or not device_id:
            return
        with self._lock:
            self._device_identities[device_id] = _DeviceIdentity(
                macs=frozenset(self._macs_from_connections(entry.get("connections"))),
                area_id=self._normalise_area(entry.get("area_id")),
                models=frozenset(
                    value
                    for value in (
                        self._normalise_compact(entry.get("model")),
                        self._normalise_compact(entry.get("model_id")),
                    )
                    if value
                ),
                names=frozenset(
                    value
                    for value in (
                        self._normalise_name(entry.get("name_by_user")),
                        self._normalise_name(entry.get("name")),
                    )
                    if value
                ),
            )
            self._rebuild_clusters()

    def update(self, dto: StateChangedDTO) -> GroupedDeviceState:
        with self._lock:
            device_id = self._resolve_device_id(dto)
            self._entity_states[dto.entity_id] = dto.is_available
            self._update_order += 1
            self._entity_updated_order[dto.entity_id] = self._update_order
            self._rebuild_groups()
            group_id = self._cluster_for_device.get(device_id, device_id)
            return self._snapshot(group_id, self._groups[group_id])

    def snapshot(self, device_id: str) -> GroupedDeviceState | None:
        with self._lock:
            group_id = self._cluster_for_device.get(device_id, device_id)
            group = self._groups.get(group_id)
            return self._snapshot(group_id, group) if group else None

    def snapshot_for_entity(self, entity_id: str) -> GroupedDeviceState | None:
        with self._lock:
            device_id = self._entity_to_device.get(entity_id)
            if device_id is None:
                return None
            group_id = self._cluster_for_device.get(device_id, device_id)
            group = self._groups.get(group_id)
            return self._snapshot(group_id, group) if group else None

    def all_snapshots(self) -> list[GroupedDeviceState]:
        with self._lock:
            return [
                self._snapshot(device_id, group)
                for device_id, group in sorted(self._groups.items())
            ]

    def is_physical_entity(self, entity_id: str) -> bool:
        """Whether HA's entity registry associates this entity with a device."""
        with self._lock:
            return entity_id in self._entity_to_device

    def platform_for_entity(self, entity_id: str) -> str | None:
        with self._lock:
            return self._entity_platforms.get(entity_id)

    def _resolve_device_id(self, dto: StateChangedDTO) -> str:
        device_id = dto.device_id or self._entity_to_device.get(dto.entity_id)
        device_id = device_id or dto.entity_id
        if self._entity_to_device.get(dto.entity_id) != device_id:
            self._entity_to_device[dto.entity_id] = device_id
            self._rebuild_clusters()
        return device_id

    def _rebuild_clusters(self) -> None:
        device_ids = set(self._entity_to_device.values()) | set(
            self._device_identities
        )
        parents = {device_id: device_id for device_id in device_ids}

        def find(device_id: str) -> str:
            while parents[device_id] != device_id:
                parents[device_id] = parents[parents[device_id]]
                device_id = parents[device_id]
            return device_id

        def union(left: str, right: str) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parents[max(left_root, right_root)] = min(left_root, right_root)

        owners_by_mac: dict[str, str] = {}
        for device_id in sorted(device_ids):
            for mac in self._device_identities.get(device_id, _DeviceIdentity()).macs:
                owner = owners_by_mac.setdefault(mac, device_id)
                union(device_id, owner)

        components: dict[str, set[str]] = {}
        for device_id in device_ids:
            components.setdefault(find(device_id), set()).add(device_id)
        component_id = {
            root: min(component) for root, component in components.items()
        }
        cluster_for_device = {
            device_id: component_id[find(device_id)] for device_id in device_ids
        }

        for device_id in sorted(device_ids):
            identity = self._device_identities.get(device_id, _DeviceIdentity())
            if identity.macs or not self._is_media_player_device(device_id):
                continue
            candidates = {
                component_id[root]
                for root, component in components.items()
                if self._component_has_mac(component)
                and self._component_has_media_player(component)
                and any(
                    self._is_media_player_device(member)
                    and self._weakly_matches(
                        identity, self._device_identities.get(member)
                    )
                    for member in component
                )
            }
            if len(candidates) == 1:
                cluster_for_device[device_id] = candidates.pop()

        self._cluster_for_device = cluster_for_device
        self._rebuild_groups()

    def _rebuild_groups(self) -> None:
        groups: dict[str, _DeviceGroup] = {}
        for entity_id, is_available in self._entity_states.items():
            raw_device_id = self._entity_to_device.get(entity_id, entity_id)
            group_id = self._cluster_for_device.get(raw_device_id, raw_device_id)
            group = groups.setdefault(group_id, _DeviceGroup())
            group.entity_states[entity_id] = is_available

        for group in groups.values():
            group.representative_entity_id = max(
                group.entity_states,
                key=lambda entity_id: (
                    self._entity_updated_order.get(entity_id, 0),
                    entity_id,
                ),
            )
        self._groups = groups

    def _is_media_player_device(self, device_id: str) -> bool:
        return any(
            entity_id.startswith("media_player.")
            for entity_id, mapped_device_id in self._entity_to_device.items()
            if mapped_device_id == device_id
        )

    def _component_has_media_player(self, component: set[str]) -> bool:
        return any(self._is_media_player_device(device_id) for device_id in component)

    def _component_has_mac(self, component: set[str]) -> bool:
        return any(
            self._device_identities.get(device_id, _DeviceIdentity()).macs
            for device_id in component
        )

    @classmethod
    def _weakly_matches(
        cls,
        source: _DeviceIdentity,
        candidate: _DeviceIdentity | None,
    ) -> bool:
        if candidate is None:
            return False
        same_area = bool(source.area_id and source.area_id == candidate.area_id)
        models_compatible = cls._models_compatible(source.models, candidate.models)
        if source.models and candidate.models and not models_compatible:
            return False
        names_compatible = cls._names_compatible(source.names, candidate.names)
        return sum((same_area, models_compatible, names_compatible)) >= 2

    @staticmethod
    def _models_compatible(
        source_models: frozenset[str], candidate_models: frozenset[str]
    ) -> bool:
        return any(
            source_model in candidate_model or candidate_model in source_model
            for source_model in source_models
            for candidate_model in candidate_models
        )

    @classmethod
    def _names_compatible(
        cls, source_names: frozenset[str], candidate_names: frozenset[str]
    ) -> bool:
        return any(
            bool(
                cls._specific_name_tokens(source_name)
                & cls._specific_name_tokens(candidate_name)
            )
            for source_name in source_names
            for candidate_name in candidate_names
        )

    @staticmethod
    def _specific_name_tokens(value: str) -> frozenset[str]:
        return frozenset(
            token
            for token in value.split()
            if token not in _GENERIC_NAME_TOKENS and len(token) > 1
        )

    @staticmethod
    def _macs_from_connections(value: Any) -> set[str]:
        if not isinstance(value, (list, tuple, set)):
            return set()
        macs = set()
        for connection in value:
            if not isinstance(connection, (list, tuple)) or len(connection) < 2:
                continue
            connection_type, address = connection[0], connection[1]
            if str(connection_type).casefold() not in {"mac", "network_mac"}:
                continue
            mac = DeviceGrouping._normalise_mac(address)
            if mac:
                macs.add(mac)
        return macs

    @staticmethod
    def _normalise_mac(value: Any) -> str | None:
        compact = re.sub(r"[^0-9a-fA-F]", "", str(value))
        if len(compact) != 12 or not re.fullmatch(r"[0-9a-fA-F]{12}", compact):
            return None
        return ":".join(compact[index : index + 2].lower() for index in range(0, 12, 2))

    @staticmethod
    def _normalise_area(value: Any) -> str | None:
        normalised = DeviceGrouping._normalise_compact(value)
        return normalised or None

    @staticmethod
    def _normalise_compact(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalised = unicodedata.normalize("NFKD", value)
        compact = "".join(
            character.casefold() for character in normalised if character.isalnum()
        )
        return compact or None

    @staticmethod
    def _normalise_name(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalised = unicodedata.normalize("NFKD", value)
        tokens = re.findall(r"[a-z0-9]+", normalised.casefold())
        return " ".join(tokens) or None

    @staticmethod
    def _snapshot(device_id: str, group: _DeviceGroup) -> GroupedDeviceState:
        # ``off`` is intentionally operational: only unavailable/unknown are false.
        return GroupedDeviceState(
            device_id=device_id,
            entity_ids=tuple(sorted(group.entity_states)),
            is_available=any(group.entity_states.values()),
            representative_entity_id=group.representative_entity_id,
        )
