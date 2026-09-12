from __future__ import annotations

import httpx


class CoreIdentityAdapter:
    """Talks to DOMUS Core's identity API. Never called synchronously from
    Guardian's own health/incident path — see ADR-0011 in domus-platform."""

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def create_identity(self, external_ref: str) -> str:
        """Get-or-create a DAID for ``external_ref``. Core is idempotent on
        this field (domus-platform, core/api/main.py): a known
        ``external_ref`` returns the existing identity (200) instead of
        minting a new one (201) — either way this just returns the DAID.
        Raises on any non-2xx or network failure; the caller decides how
        that counts against ``attempts``."""
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}/core/v1/identities",
                json={"external_ref": external_ref},
            )
            response.raise_for_status()
            return response.json()["daid"]
