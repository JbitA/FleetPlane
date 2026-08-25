from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fleetplane.core.reconciliation import ReconciliationService
from fleetplane.domain.enums import HealthState
from tests.conftest import provision_active, telemetry


def test_device_keyset_pagination_and_materialized_summary(runtime):
    for index in range(123):
        provision_active(runtime, f"edge-{index:03d}")
        outcome = runtime.ingestion.ingest(telemetry(f"edge-{index:03d}", 0))
        assert outcome.disposition.value == "accepted"

    summary = runtime.store.get_fleet_summary()
    assert summary.total_devices == 123
    assert summary.healthy == 123

    seen: list[str] = []
    cursor = None
    while True:
        page = runtime.store.list_device_states_page(limit=17, cursor=cursor)
        seen.extend(item.device_id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(seen) == 123
    assert len(set(seen)) == 123
    assert seen == sorted(seen)


def test_summary_read_does_not_scan_devices(runtime, monkeypatch):
    provision_active(runtime, "edge-001")
    runtime.ingestion.ingest(telemetry("edge-001", 0))

    def explode(*args, **kwargs):
        raise AssertionError("summary attempted a fleet scan")

    monkeypatch.setattr(runtime.store, "list_device_states_page", explode)
    summary = ReconciliationService(runtime.store).fleet_summary()
    assert summary.total_devices == 1


def test_health_filter_and_bounded_reconciliation(runtime):
    now = datetime.now(UTC)
    for index in range(7):
        provision_active(runtime, f"edge-{index}")
        runtime.ingestion.ingest(telemetry(f"edge-{index}", 0, observed_at=now))
    result = runtime.reconciliation.reconcile_batch(limit=3, now=now + timedelta(hours=2))
    assert result["processed"] == 3
    assert result["next_cursor"] is not None

    cursor = result["next_cursor"]
    while cursor:
        result = runtime.reconciliation.reconcile_batch(
            limit=3, cursor=cursor, now=now + timedelta(hours=2)
        )
        cursor = result["next_cursor"]

    summary = runtime.store.get_fleet_summary()
    assert summary.offline == 7
    page = runtime.store.list_device_states_page(limit=10, health_state=HealthState.OFFLINE)
    assert len(page.items) == 7
