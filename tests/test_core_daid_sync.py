from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.dto import StateChangedDTO
from app.models import CoreDaidLink
from app.repositories.core_daid_links import CoreDaidLinkRepository
from app.services.core_daid_sync import CoreDaidSyncService
from app.services.device_grouping import DeviceGrouping


BASE_TIME = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)


class FakeCoreIdentityAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.fail_next = 0
        self._next_daid = 0

    async def create_identity(self) -> str:
        self.calls += 1
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("Core unreachable")
        self._next_daid += 1
        return f"urn:domus:asset:test-{self._next_daid}"


def make_service(
    grouping: DeviceGrouping | None = None,
    adapter: FakeCoreIdentityAdapter | None = None,
    max_attempts: int = 3,
) -> tuple[CoreDaidSyncService, sessionmaker, FakeCoreIdentityAdapter]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    fake_adapter = adapter or FakeCoreIdentityAdapter()
    service = CoreDaidSyncService(
        session_factory=factory,
        repository=CoreDaidLinkRepository(),
        grouping=grouping or DeviceGrouping(),
        core_identity_adapter=fake_adapter,
        max_attempts=max_attempts,
    )
    return service, factory, fake_adapter


def seen_device(grouping: DeviceGrouping, entity_id: str, device_id: str) -> None:
    grouping.update(
        StateChangedDTO(
            entity_id=entity_id,
            state="on",
            domain=entity_id.partition(".")[0],
            friendly_name=entity_id,
            time_fired=BASE_TIME,
            device_id=device_id,
        )
    )


def test_sync_pending_creates_one_row_per_known_device() -> None:
    grouping = DeviceGrouping()
    seen_device(grouping, "light.cucina", "device-1")
    seen_device(grouping, "sensor.temp_salotto", "device-2")
    service, factory, _ = make_service(grouping)

    created = service.sync_pending()

    assert created == 2
    with factory() as session:
        rows = session.query(CoreDaidLink).order_by(CoreDaidLink.device_id_ha).all()
        assert [row.device_id_ha for row in rows] == ["device-1", "device-2"]
        assert all(row.daid_status == "pending" for row in rows)
        assert all(row.daid is None for row in rows)
        assert all(row.attempts == 0 for row in rows)


def test_sync_pending_is_idempotent_for_already_linked_devices() -> None:
    grouping = DeviceGrouping()
    seen_device(grouping, "light.cucina", "device-1")
    service, factory, _ = make_service(grouping)

    first = service.sync_pending()
    second = service.sync_pending()

    assert first == 1
    assert second == 0
    with factory() as session:
        assert session.query(CoreDaidLink).count() == 1


@pytest.mark.asyncio
async def test_reconcile_confirms_pending_link_on_success() -> None:
    grouping = DeviceGrouping()
    seen_device(grouping, "light.cucina", "device-1")
    service, factory, adapter = make_service(grouping)
    service.sync_pending()

    confirmed = await service.reconcile_with_core()

    assert confirmed == 1
    assert adapter.calls == 1
    with factory() as session:
        link = session.get(CoreDaidLink, "device-1")
        assert link.daid_status == "confirmed"
        assert link.daid == "urn:domus:asset:test-1"
        assert link.attempts == 0


@pytest.mark.asyncio
async def test_reconcile_survives_core_unreachable_without_raising() -> None:
    grouping = DeviceGrouping()
    seen_device(grouping, "light.cucina", "device-1")
    adapter = FakeCoreIdentityAdapter()
    adapter.fail_next = 1
    service, factory, _ = make_service(grouping, adapter=adapter)
    service.sync_pending()

    confirmed = await service.reconcile_with_core()

    assert confirmed == 0
    with factory() as session:
        link = session.get(CoreDaidLink, "device-1")
        assert link.daid_status == "failed"
        assert link.daid is None
        assert link.attempts == 1


@pytest.mark.asyncio
async def test_reconcile_stops_retrying_past_max_attempts() -> None:
    grouping = DeviceGrouping()
    seen_device(grouping, "light.cucina", "device-1")
    adapter = FakeCoreIdentityAdapter()
    adapter.fail_next = 10
    service, factory, _ = make_service(grouping, adapter=adapter, max_attempts=3)
    service.sync_pending()

    await service.reconcile_with_core()
    await service.reconcile_with_core()
    await service.reconcile_with_core()
    # A fourth pass must not even try: attempts has reached max_attempts.
    calls_before_fourth = adapter.calls
    await service.reconcile_with_core()

    assert adapter.calls == calls_before_fourth
    with factory() as session:
        link = session.get(CoreDaidLink, "device-1")
        assert link.attempts == 3
        assert link.daid_status == "failed"


@pytest.mark.asyncio
async def test_reconcile_one_failure_does_not_block_the_rest_of_the_batch() -> None:
    grouping = DeviceGrouping()
    seen_device(grouping, "light.cucina", "device-1")
    seen_device(grouping, "sensor.temp_salotto", "device-2")
    adapter = FakeCoreIdentityAdapter()
    adapter.fail_next = 1  # only the first call in the batch fails
    service, factory, _ = make_service(grouping, adapter=adapter)
    service.sync_pending()

    confirmed = await service.reconcile_with_core()

    assert confirmed == 1
    with factory() as session:
        statuses = {
            row.device_id_ha: row.daid_status
            for row in session.query(CoreDaidLink).all()
        }
        assert sorted(statuses.values()) == ["confirmed", "failed"]
