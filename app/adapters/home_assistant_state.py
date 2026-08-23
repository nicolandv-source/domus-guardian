from __future__ import annotations

import httpx


class HomeAssistantStateAdapter:
    """Publish Guardian's own metrics as native Home Assistant entity states.

    Uses HA Core's ``/api/states/{entity_id}`` endpoint directly with
    Guardian's own Supervisor-issued token — the same one already used for
    persistent notifications. This makes ``sensor.domus_*`` real HA entities
    without writing anything to HA's own live configuration and without a
    second token living in a shared file.
    """

    def __init__(self, base_url: str, token: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds

    async def set_state(
        self, entity_id: str, state: str, attributes: dict[str, object]
    ) -> None:
        if not self._token:
            raise RuntimeError("Token Home Assistant non disponibile")
        headers = {"Authorization": f"Bearer {self._token}"}
        payload = {"state": state, "attributes": attributes}
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}/api/states/{entity_id}",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
