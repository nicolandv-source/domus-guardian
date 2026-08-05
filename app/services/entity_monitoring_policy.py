from __future__ import annotations

import os
from fnmatch import fnmatchcase


class EntityMonitoringPolicy:
    """Decide whether a Home Assistant entity is eligible for monitoring."""

    def __init__(self) -> None:
        self._monitored_domains = self._parse_domains(
            os.getenv("GUARDIAN_MONITORED_DOMAINS", "")
        )
        self._excluded_entity_patterns = self._parse_values(
            os.getenv("GUARDIAN_EXCLUDED_ENTITY_PATTERNS", "")
        )

    @classmethod
    def permissive(cls) -> EntityMonitoringPolicy:
        """Return a policy that preserves the historical monitor-all behavior."""
        policy = cls.__new__(cls)
        policy._monitored_domains = frozenset()
        policy._excluded_entity_patterns = ()
        return policy

    def allows(self, entity_id: str) -> bool:
        if any(
            fnmatchcase(entity_id, pattern)
            for pattern in self._excluded_entity_patterns
        ):
            return False

        domain = entity_id.partition(".")[0].lower()
        return not self._monitored_domains or domain in self._monitored_domains

    @staticmethod
    def _parse_domains(value: str) -> frozenset[str]:
        return frozenset(item.lower() for item in EntityMonitoringPolicy._parse_values(value))

    @staticmethod
    def _parse_values(value: str) -> tuple[str, ...]:
        return tuple(item.strip() for item in value.split(",") if item.strip())
