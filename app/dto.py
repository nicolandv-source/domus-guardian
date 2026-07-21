from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class StateChangedDTO:
    entity_id: str
    state: str
    domain: str
    friendly_name: str | None
    time_fired: datetime
    old_state: str | None = None
    device_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return self.state not in {"unavailable", "unknown"}
