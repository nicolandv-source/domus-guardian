from __future__ import annotations

from dataclasses import dataclass

from app.services.device_debounce import DeviceDebouncer


@dataclass(frozen=True)
class HealthSnapshot:
    score: int
    status: str
    active_incidents: int
    critical_incidents: int
    warning_incidents: int
    offline_devices: int


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

    def __init__(self, debouncer: DeviceDebouncer) -> None:
        self._debouncer = debouncer

    def snapshot(self, database_connected: bool) -> HealthSnapshot:
        stable_states = self._debouncer.diagnostics().values()
        offline_devices = sum(1 for state in stable_states if state.last_state is False)
        score, status = calculate_health_score_status(
            database_connected=database_connected,
            critical_incidents=offline_devices,
            warning_incidents=0,
            offline_devices=offline_devices,
        )
        return HealthSnapshot(
            score=score,
            status=status,
            active_incidents=offline_devices,
            critical_incidents=offline_devices,
            warning_incidents=0,
            offline_devices=offline_devices,
        )
