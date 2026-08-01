from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock


@dataclass(frozen=True)
class DeviceStateChange:
    device_id: str
    old_state: bool | None
    new_state: bool
    changed_at: datetime


@dataclass
class DeviceDebounceState:
    last_state: bool | None = None
    last_change_time: datetime | None = None
    pending_state: bool | None = None
    debounce_until: datetime | None = None


class DeviceDebouncer:
    def __init__(self, debounce_window: timedelta) -> None:
        self._debounce_window = debounce_window
        self._states: dict[str, DeviceDebounceState] = {}
        self._lock = RLock()

    def process_state_change(
        self,
        device_id: str,
        state: bool,
        now: datetime,
    ) -> DeviceStateChange | None:
        with self._lock:
            entry = self._states.setdefault(device_id, DeviceDebounceState())
            if entry.last_state is None:
                if state:
                    # A startup ``unavailable`` can be superseded by the first
                    # available state before its debounce window expires.  Clear
                    # that candidate explicitly: otherwise ``flush_due`` would
                    # later commit an obsolete offline transition.
                    self._clear_pending(entry)
                    entry.last_state = True
                    entry.last_change_time = now
                else:
                    self._set_pending(entry, False, now)
                return None

            if state == entry.last_state:
                self._clear_pending(entry)
                return None

            if entry.pending_state != state:
                self._set_pending(entry, state, now)
                return None

            if entry.debounce_until and now >= entry.debounce_until:
                return self._commit(device_id, entry, now)
            return None

    def flush_due(self, now: datetime) -> list[DeviceStateChange]:
        with self._lock:
            return [
                self._commit(device_id, entry, now)
                for device_id, entry in self._states.items()
                if entry.pending_state is not None
                and entry.debounce_until is not None
                and now >= entry.debounce_until
            ]

    def diagnostics(self) -> dict[str, DeviceDebounceState]:
        with self._lock:
            return {
                device_id: DeviceDebounceState(
                    last_state=entry.last_state,
                    last_change_time=entry.last_change_time,
                    pending_state=entry.pending_state,
                    debounce_until=entry.debounce_until,
                )
                for device_id, entry in self._states.items()
            }

    def _set_pending(
        self,
        entry: DeviceDebounceState,
        state: bool,
        now: datetime,
    ) -> None:
        entry.pending_state = state
        entry.debounce_until = now + self._debounce_window

    @staticmethod
    def _clear_pending(entry: DeviceDebounceState) -> None:
        entry.pending_state = None
        entry.debounce_until = None

    def _commit(
        self,
        device_id: str,
        entry: DeviceDebounceState,
        now: datetime,
    ) -> DeviceStateChange:
        new_state = entry.pending_state
        if new_state is None:
            raise RuntimeError("Tentativo di confermare uno stato senza candidato")
        change = DeviceStateChange(
            device_id=device_id,
            old_state=entry.last_state,
            new_state=new_state,
            changed_at=now,
        )
        entry.last_state = new_state
        entry.last_change_time = now
        self._clear_pending(entry)
        return change
