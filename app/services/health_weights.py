from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.models import Device


@dataclass(frozen=True)
class DeviceProfile:
    category: str
    weight: float
    include_in_score: bool
    staleness_minutes: int | None = None


class HealthWeights:
    """Classifies physical devices using small, editable JSON rules."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._categories = config["categories"]
        self._rules = config.get("rules", [])
        self._default_category = config.get("default_category", "important")
        thresholds = config.get("thresholds", {})
        self.healthy_min_score = int(thresholds.get("healthy_min_score", 98))
        self.warning_min_score = int(thresholds.get("warning_min_score", 80))

    @classmethod
    def from_file(cls, path: Path) -> HealthWeights:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def profile_for(self, devices: Iterable[Device]) -> DeviceProfile:
        profiles = [self._profile_for_device(device) for device in devices]
        if not profiles:
            return self._category_profile(self._default_category)
        return max(profiles, key=lambda profile: profile.weight)

    def _profile_for_device(self, device: Device) -> DeviceProfile:
        for rule in self._rules:
            if self._matches(rule, device):
                return self._category_profile(rule["category"], rule)
        return self._category_profile(self._default_category)

    @staticmethod
    def _matches(rule: dict[str, Any], device: Device) -> bool:
        domains = rule.get("domains")
        if domains and device.domain not in domains:
            return False

        device_classes = rule.get("device_classes")
        if device_classes and device.device_class not in device_classes:
            return False

        patterns = rule.get("name_patterns")
        if patterns:
            searchable = " ".join(
                value for value in (device.entity_id, device.name or "") if value
            ).lower()
            if not any(pattern.lower() in searchable for pattern in patterns):
                return False

        return bool(domains or device_classes or patterns)

    def _category_profile(
        self,
        category: str,
        rule: dict[str, Any] | None = None,
    ) -> DeviceProfile:
        category_config = self._categories[category]
        rule = rule or {}
        return DeviceProfile(
            category=category,
            weight=float(rule.get("weight", category_config["weight"])),
            include_in_score=bool(
                rule.get("include_in_score", category_config["include_in_score"])
            ),
            staleness_minutes=self._staleness_minutes(rule, category_config),
        )

    @staticmethod
    def _staleness_minutes(
        rule: dict[str, Any], category_config: dict[str, Any]
    ) -> int | None:
        value = rule.get("staleness_minutes", category_config.get("staleness_minutes"))
        return int(value) if value is not None else None
