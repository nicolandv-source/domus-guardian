from __future__ import annotations

from dataclasses import dataclass

from app.services.device_service import DeviceService
from app.services.health_weights import HealthWeights


@dataclass(frozen=True)
class HealthSnapshot:
    score: int
    status: str
    active_incidents: int
    critical_incidents: int
    warning_incidents: int
    offline_devices: int
    total_weight: float
    offline_weight: float


def calculate_health_score_status(
    *,
    database_connected: bool,
    critical_incidents: int,
    warning_incidents: int,
    offline_devices: int,
) -> tuple[int, str]:
    score = 100
    if not database_connected:
        score -= 40
    score -= critical_incidents * 25
    score -= warning_incidents * 10
    score -= offline_devices * 5
    score = max(0, score)
    if not database_connected or critical_incidents:
        status = "critical"
    elif warning_incidents or offline_devices:
        status = "warning"
    else:
        status = "healthy"
    return score, status


class HealthEngine:
    """Computes health from debounced physical-device states only."""

    def __init__(self, device_service: DeviceService, weights: HealthWeights) -> None:
        self._device_service = device_service
        self._weights = weights

    def snapshot(self, database_connected: bool) -> HealthSnapshot:
        devices = self._device_service.diagnostics(self._weights.profile_for)
        stable_devices = [
            device
            for device in devices
            if device["last_state"] in {"available", "unavailable"}
            and not device.get("maintenance_active", False)
        ]
        scored_devices = [
            device for device in stable_devices if device["include_in_score"]
        ]
        total_weight = sum(float(device["weight"]) for device in scored_devices)
        offline = [
            device for device in scored_devices if device["last_state"] == "unavailable"
        ]
        offline_weight = sum(float(device["weight"]) for device in offline)
        offline_devices = len(offline)
        critical_incidents = sum(
            1 for device in offline if device["category"] == "critical"
        )
        warning_incidents = sum(
            1 for device in offline if device["category"] == "important"
        )
        score, status = self._calculate_weighted_score_status(
            database_connected,
            total_weight,
            offline_weight,
        )
        return HealthSnapshot(
            score=score,
            status=status,
            active_incidents=offline_devices,
            critical_incidents=critical_incidents,
            warning_incidents=warning_incidents,
            offline_devices=offline_devices,
            total_weight=total_weight,
            offline_weight=offline_weight,
        )

    def _calculate_weighted_score_status(
        self,
        database_connected: bool,
        total_weight: float,
        offline_weight: float,
    ) -> tuple[int, str]:
        if not database_connected:
            return 0, "critical"
        if total_weight <= 0:
            return 100, "healthy"

        score = round(100 * max(0.0, 1 - offline_weight / total_weight))
        if score < self._weights.warning_min_score:
            return score, "critical"
        if score < self._weights.healthy_min_score:
            return score, "warning"
        return score, "healthy"
