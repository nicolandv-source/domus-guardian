from __future__ import annotations

import httpx


class HomeAssistantNotifyAdapter:
    def __init__(self, base_url: str, token: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds

    async def upsert_persistent_notification(
        self,
        notification_id: str,
        title: str,
        message: str,
    ) -> None:
        await self._call_service(
            "persistent_notification/create",
            {"notification_id": notification_id, "title": title, "message": message},
        )

    async def dismiss_persistent_notification(self, notification_id: str) -> None:
        await self._call_service(
            "persistent_notification/dismiss",
            {"notification_id": notification_id},
        )

    async def _call_service(self, service_path: str, payload: dict[str, str]) -> None:
        if not self._token:
            raise RuntimeError("Token Home Assistant non disponibile")
        headers = {"Authorization": f"Bearer {self._token}"}
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}/api/services/{service_path}",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
