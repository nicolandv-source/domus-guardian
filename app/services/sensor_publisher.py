from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from app.adapters.home_assistant_state import HomeAssistantStateAdapter


logger = logging.getLogger(__name__)


class SensorPublisher:
    """Push a health snapshot to a fixed set of native ``sensor.domus_*`` entities.

    Takes a plain snapshot-producing callable rather than Guardian's service
    objects directly, so this stays a leaf module: no dependency on the
    application's composition root, and trivially testable with a fake
    snapshot.
    """

    def __init__(
        self,
        adapter: HomeAssistantStateAdapter,
        snapshot_provider: Callable[[], dict[str, object]],
    ) -> None:
        self._adapter = adapter
        self._snapshot_provider = snapshot_provider

    async def publish(self) -> None:
        snapshot = self._snapshot_provider()
        for entity_id, state, attributes in self._sensors(snapshot):
            try:
                await self._adapter.set_state(entity_id, state, attributes)
            except Exception as exc:
                logger.warning(
                    "Pubblicazione sensore Home Assistant fallita: %s (%s)",
                    entity_id,
                    type(exc).__name__,
                )

    @staticmethod
    def _sensors(
        snapshot: dict[str, object],
    ) -> list[tuple[str, str, dict[str, object]]]:
        last_sync = snapshot.get("last_websocket_event_at")
        last_sync_state = (
            last_sync.isoformat() if isinstance(last_sync, datetime) else "unknown"
        )
        return [
            (
                "sensor.domus_guardian_health_score",
                str(snapshot["score"]),
                {
                    "friendly_name": "DOMUS Guardian Health Score",
                    "unit_of_measurement": "%",
                    "icon": "mdi:heart-pulse",
                    "status": snapshot["status"],
                },
            ),
            (
                "sensor.domus_guardian_open_incidents",
                str(snapshot["active_incidents"]),
                {
                    "friendly_name": "DOMUS Guardian Incidenti aperti",
                    "icon": "mdi:alert-circle-outline",
                    "critical": snapshot["critical_incidents"],
                    "warning": snapshot["warning_incidents"],
                },
            ),
            (
                "sensor.domus_guardian_degraded_devices",
                str(snapshot["offline_devices"]),
                {
                    "friendly_name": "DOMUS Guardian Dispositivi degradati",
                    "icon": "mdi:devices",
                    "offline_weighted": snapshot["offline_devices_weighted"],
                },
            ),
            (
                "sensor.domus_guardian_last_sync",
                last_sync_state,
                {
                    "friendly_name": "DOMUS Guardian Ultimo sync",
                    "device_class": "timestamp",
                    "icon": "mdi:sync",
                },
            ),
            (
                "sensor.domus_guardian_watchdog",
                str(snapshot["watchdog_status"]),
                {
                    "friendly_name": "DOMUS Guardian Watchdog",
                    "icon": "mdi:shield-check",
                    "issues": snapshot.get("watchdog_issues", []),
                },
            ),
        ]
