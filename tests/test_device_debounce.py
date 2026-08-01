from datetime import datetime, timedelta, timezone

from app.services.device_debounce import DeviceDebouncer


def test_debounce_commits_only_after_window() -> None:
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    debouncer = DeviceDebouncer(timedelta(seconds=30))

    assert debouncer.process_state_change("device-1", True, now) is None
    assert (
        debouncer.process_state_change("device-1", False, now + timedelta(seconds=1))
        is None
    )
    assert debouncer.flush_due(now + timedelta(seconds=30)) == []

    changes = debouncer.flush_due(now + timedelta(seconds=31))
    assert len(changes) == 1
    assert changes[0].old_state is True
    assert changes[0].new_state is False


def test_debounce_cancels_pending_state_when_device_recovers() -> None:
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    debouncer = DeviceDebouncer(timedelta(seconds=30))

    debouncer.process_state_change("device-1", True, now)
    debouncer.process_state_change("device-1", False, now + timedelta(seconds=1))
    debouncer.process_state_change("device-1", True, now + timedelta(seconds=10))

    assert debouncer.flush_due(now + timedelta(seconds=60)) == []
    state = debouncer.diagnostics()["device-1"]
    assert state.last_state is True
    assert state.pending_state is None


def test_initial_unavailable_then_available_cancels_pending_transition() -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)
    debouncer = DeviceDebouncer(timedelta(seconds=45))

    debouncer.process_state_change("device-1", False, now)
    debouncer.process_state_change("device-1", True, now + timedelta(seconds=10))

    assert debouncer.flush_due(now + timedelta(seconds=46)) == []
    state = debouncer.diagnostics()["device-1"]
    assert state.last_state is True
    assert state.pending_state is None


def test_rapid_double_change_only_commits_latest_stable_state() -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)
    debouncer = DeviceDebouncer(timedelta(seconds=45))

    debouncer.process_state_change("device-1", True, now)
    debouncer.process_state_change("device-1", False, now + timedelta(seconds=1))
    debouncer.process_state_change("device-1", True, now + timedelta(seconds=2))
    debouncer.process_state_change("device-1", False, now + timedelta(seconds=3))

    changes = debouncer.flush_due(now + timedelta(seconds=48))
    assert [(change.old_state, change.new_state) for change in changes] == [(True, False)]
