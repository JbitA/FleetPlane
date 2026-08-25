from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from fleetplane.domain.models import ConfigurationPatch
from tests.conftest import provision_active, telemetry


def test_concurrent_telemetry_cannot_rewind_projection(runtime):
    base = datetime.now(UTC)
    provision_active(runtime, "edge-race")

    def ingest(seq: int):
        return runtime.ingestion.ingest(
            telemetry("edge-race", seq, observed_at=base + timedelta(milliseconds=seq))
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(ingest, range(50)))
    assert all(item.disposition.value != "rejected" for item in outcomes)
    state = runtime.store.get_device_state("edge-race")
    assert state is not None
    assert state.last_sequence == 49
    assert state.projection_version >= 50


def test_concurrent_configuration_revisions_are_unique_and_monotonic(runtime):
    provision_active(runtime, "edge-config")
    runtime.ingestion.ingest(telemetry("edge-config", 0))

    def update(index: int) -> int:
        return runtime.configuration.set_desired(
            "edge-config",
            ConfigurationPatch(anomaly_threshold=0.5 + index / 1000),
            actor=f"worker-{index}",
        ).revision

    with ThreadPoolExecutor(max_workers=10) as pool:
        revisions = list(pool.map(update, range(20)))
    assert sorted(revisions) == list(range(1, 21))
    assert runtime.store.get_desired_config("edge-config").revision == 20
